"""交易监控路由 - REST API + WebSocket"""
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import case

# Jinja2 模板（用于独立页面）
templates = Jinja2Templates(directory="app/templates")

from app.utils.database import get_db, SessionLocal
from app.models.models import Transaction
from app.models.trade import TradeAnalysis, Setting
from app.schemas.trade import (
    TradeListResponse, TradeData, MonitorStatus, FourMetrics, WsMessage
)
from app.services.trade_backfill import TradeBackfill
from app.services.trade_stream import TradeStream
from app.services.trade_processor import calculate_metrics
from app.services.settings_service import (
    get_all_settings, update_setting, init_default_settings, SettingResponse
)
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
def get_trades(
    mint: str,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
):
    """获取某 mint 的交易列表（最新在前）"""
    total = db.query(Transaction).filter(Transaction.token_mint == mint).count()

    trades = (
        db.query(Transaction, TradeAnalysis)
        .outerjoin(TradeAnalysis, Transaction.sig == TradeAnalysis.sig)
        .filter(Transaction.token_mint == mint)
        .order_by(
            case((Transaction.source == "rpc_fill", 0), else_=1),  # rpc_fill 先显示
            Transaction.slot.desc(),
            Transaction.block_time.desc(),
            Transaction.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    trade_list = []
    for tx, analysis in trades:
        trade_list.append(TradeData(
            sig=tx.sig,
            slot=tx.slot,
            block_time=tx.block_time,
            from_address=tx.from_address,
            to_address=tx.to_address,
            amount=tx.amount,
            token_mint=tx.token_mint,
            token_symbol=tx.token_symbol,
            transaction_type=tx.transaction_type,
            dex=tx.dex,
            pool_address=tx.pool_address,
            sol_spent=round(tx.sol_spent, 6) if tx.sol_spent is not None else None,
            fee=tx.fee,
            source=tx.source,
            net_sol_flow=analysis.net_sol_flow if analysis else 0.0,
            net_token_flow=analysis.net_token_flow if analysis else 0.0,
            price_per_token=analysis.price_per_token if analysis else 0.0,
            wallet_tag=analysis.wallet_tag if analysis else None,
            # 风险相关
            risk_score=tx.risk_score or 0,
            risk_verdict=tx.risk_verdict,
            risk_indicators=tx.risk_indicators,
            # Compute Unit
            priority_fee=tx.priority_fee,
            cu_consumed=tx.cu_consumed or 0,
            cu_limit=tx.cu_limit or 200000,
            cu_price=tx.cu_price or 0,
            # 指令统计
            instructions_count=tx.instructions_count or 0,
            inner_instructions_count=tx.inner_instructions_count or 0,
            total_instruction_count=tx.total_instruction_count or 0,
            account_keys_count=tx.account_keys_count or 0,
            uses_lookup_table=tx.uses_lookup_table or False,
            signers_count=tx.signers_count or 0,
            # 指令详情
            main_instructions=tx.main_instructions,
            inner_instructions=tx.inner_instructions,
            program_ids=tx.program_ids,
        ))

    return TradeListResponse(
        trades=trade_list,
        total=total,
        has_more=(page * page_size < total),
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
