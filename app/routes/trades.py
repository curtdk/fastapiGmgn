"""交易监控路由 - REST API + WebSocket"""
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

# Jinja2 模板（用于独立页面）
templates = Jinja2Templates(directory="app/templates")

from app.utils.database import get_db, SessionLocal
from app.models.trade import Setting
from app.schemas.trade import (
    TradeListResponse, TradeData, MonitorStatus, FourMetrics, WsMessage
)
from app.services.trade_backfill import TradeBackfill
from app.services.trade_stream import TradeStream
from app.services.trade_processor import calculate_metrics
from app.services.settings_service import (
    get_all_settings, update_setting, init_default_settings, SettingResponse
)
from app.services import tx_redis
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trades", tags=["交易监控"])

# 全局监控状态
active_monitors = {}  # mint -> {"backfill": TradeBackfill, "stream": TradeStream}


@router.on_event("startup")
async def startup_event():
    """应用启动时初始化默认设置"""
    db = SessionLocal()
    try:
        init_default_settings(db)
    finally:
        db.close()


@router.get("/{mint}")
async def get_trades(
    mint: str,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    """获取某 mint 的交易列表（最新在前）- 从 Redis 获取"""
    # 从 Redis 获取交易总数
    rpc_sigs = await tx_redis.get_tx_list(mint, "rpc_fill")
    ws_sigs = await tx_redis.get_tx_list(mint, "ws")
    total = len(rpc_sigs) + len(ws_sigs)
    
    # 分页计算
    start = (page - 1) * page_size
    end = start + page_size
    
    # 合并签名列表（rpc_fill 在前，然后按 score 排序）
    all_sigs = rpc_sigs + ws_sigs
    
    # 分页
    paginated_sigs = all_sigs[start:end]
    
    trade_list = []
    for sig in paginated_sigs:
        tx_detail = await tx_redis.get_tx(sig)
        if not tx_detail:
            continue
        
        trade_list.append(TradeData(
            sig=tx_detail.get("sig", ""),
            slot=tx_detail.get("slot", 0),
            block_time=tx_detail.get("block_time"),
            from_address=tx_detail.get("from_address", ""),
            to_address=tx_detail.get("to_address", ""),
            amount=tx_detail.get("amount", 0),
            token_mint=tx_detail.get("token_mint", mint),
            token_symbol=tx_detail.get("token_symbol", ""),
            transaction_type=tx_detail.get("transaction_type", ""),
            dex=tx_detail.get("dex", ""),
            pool_address=tx_detail.get("pool_address", ""),
            sol_spent=round(tx_detail.get("sol_spent", 0) or 0, 6),
            fee=tx_detail.get("fee", 0),
            source=tx_detail.get("source", ""),
            net_sol_flow=tx_detail.get("net_sol_flow", 0.0),
            net_token_flow=tx_detail.get("net_token_flow", 0.0),
            price_per_token=tx_detail.get("price_per_token", 0.0),
            wallet_tag=tx_detail.get("wallet_tag"),
            # 风险相关
            risk_score=tx_detail.get("risk_score", 0) or 0,
            risk_verdict=tx_detail.get("risk_verdict", ""),
            risk_indicators=tx_detail.get("risk_indicators", "[]"),
            # Compute Unit
            priority_fee=tx_detail.get("priority_fee", 0),
            cu_consumed=tx_detail.get("cu_consumed", 0) or 0,
            cu_limit=tx_detail.get("cu_limit", 200000) or 200000,
            cu_price=tx_detail.get("cu_price", 0) or 0,
            # 指令统计
            instructions_count=tx_detail.get("instructions_count", 0) or 0,
            inner_instructions_count=tx_detail.get("inner_instructions_count", 0) or 0,
            total_instruction_count=tx_detail.get("total_instruction_count", 0) or 0,
            account_keys_count=tx_detail.get("account_keys_count", 0) or 0,
            uses_lookup_table=tx_detail.get("uses_lookup_table", False) or False,
            signers_count=tx_detail.get("signers_count", 0) or 0,
            # 指令详情
            main_instructions=tx_detail.get("main_instructions", "[]"),
            inner_instructions=tx_detail.get("inner_instructions", "[]"),
            program_ids=tx_detail.get("program_ids", "[]"),
        ))

    return TradeListResponse(
        trades=trade_list,
        total=total,
        has_more=(end < total),
    )


@router.get("/{mint}/metrics")
async def get_metrics(mint: str, db: Session = Depends(get_db)):
    """获取四个核心指标"""
    metrics = await calculate_metrics(db, mint)
    return FourMetrics(**metrics)


@router.get("/{mint}/status")
def get_monitor_status(mint: str):
    """获取当前监听状态"""
    monitor = active_monitors.get(mint)
    if not monitor:
        return MonitorStatus(mint=mint, status="IDLE", message="未启动")

    stream = monitor.get("stream")
    backfill = monitor.get("backfill")

    if backfill and backfill.running:
        status = "BACKFILLING"
        message = f"回填中... 已获取 {backfill.total_fetched} 条"
        progress = 0.0
    elif stream and stream.running:
        status = "STREAMING"
        message = "实时监听中..."
        progress = 100.0
    else:
        status = "STOPPED"
        message = "已停止"
        progress = 0.0

    return MonitorStatus(
        mint=mint,
        status=status,
        message=message,
        progress=progress,
    )


@router.post("/{mint}/start")
async def start_monitor(mint: str, db: Session = Depends(get_db)):
    """启动监听（实时流 + 回填接力）"""
    # 如果已在运行，先停止
    if mint in active_monitors:
        await stop_monitor(mint)

    from app.services.settings_service import get_setting
    api_key = get_setting(db, "helius_api_key") or ""

    stream = TradeStream(mint=mint, api_key=api_key)
    backfill = TradeBackfill(db=db, mint=mint, stream=stream)

    active_monitors[mint] = {
        "backfill": backfill,
        "stream": stream,
    }

    import asyncio

    # 先启动实时流
    asyncio.create_task(stream.start())

    # 实时流启动后立即启动回填（回填会等待 sync_point）
    async def start_backfill_after_stream():
        # 等待 stream 启动完成
        while not stream.running:
            await asyncio.sleep(0.2)
        await backfill.run()

    asyncio.create_task(start_backfill_after_stream())

    return {"message": f"已开始监听 {mint}"}


@router.post("/{mint}/stop")
async def stop_monitor(mint: str):
    """停止监听"""
    monitor = active_monitors.get(mint)
    if not monitor:
        return {"message": "未运行"}

    backfill = monitor.get("backfill")
    stream = monitor.get("stream")

    if backfill:
        backfill.stop()
    if stream:
        await stream.stop()

    del active_monitors[mint]
    return {"message": f"已停止监听 {mint}"}


@router.websocket("/ws/{mint}")
async def websocket_endpoint(websocket: WebSocket, mint: str):
    """WebSocket 端点 - 实时推送交易数据和状态"""
    await ws_manager.connect(websocket, mint)
    try:
        # 发送当前状态
        status_resp = await get_monitor_status(mint)
        await websocket.send_json({
            "type": "status",
            "data": status_resp.model_dump(),
        })

        while True:
            # 保持连接，接收客户端消息（心跳等）
            data = await websocket.receive_text()
            # 可以处理客户端指令
            try:
                msg = eval(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket)


# ===== 设置管理路由 =====
settings_router = APIRouter(prefix="/api/settings", tags=["系统设置"])


@settings_router.get("/")
def get_settings(db: Session = Depends(get_db)):
    """获取所有设置"""
    return get_all_settings(db)


@settings_router.put("/{key}")
def put_setting(key: str, body: SettingResponse, db: Session = Depends(get_db)):
    """更新设置"""
    return update_setting(db, key, body.value)


# ===== 独立页面路由 =====
live_router = APIRouter(tags=["实时交易"])


@live_router.get("/trade")
async def trade_live_page(request: Request):
    """独立实时交易页面（无后台菜单）"""
    return templates.TemplateResponse(request, "trade_live.html")
