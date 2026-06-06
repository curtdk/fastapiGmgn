"""
策略相关 API 端点
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import logging
from typing import List

router = APIRouter(prefix="/api/strategy", tags=["策略"])

logger = logging.getLogger(__name__)

# 策略日志存储
_strategy_logs: List[str] = []
_current_state: str = "idle"


def add_strategy_log(message: str):
    """添加策略日志"""
    global _strategy_logs
    _strategy_logs.append(message)
    # 只保留最近100条日志
    if len(_strategy_logs) > 100:
        _strategy_logs = _strategy_logs[-100:]


def update_strategy_state(state: str):
    """更新策略执行状态"""
    global _current_state
    _current_state = state


@router.post("/select")
async def select_strategy(request: Request):
    """
    选择并启用策略
    """
    try:
        body = await request.json()
        strategy_name = body.get("strategy", "")
        mint = body.get("mint", "")
        enabled = body.get("enabled", True)
        params = body.get("params", {})
        
        from app.taskcl.manager import get_strategy_manager
        from app.taskcl.consumer import start_strategy_consumer
        
        manager = get_strategy_manager()
        
        if enabled:
            if manager.select(strategy_name, mint, params):
                manager.enable()
                await start_strategy_consumer()
                from app.taskcl.consumer import set_strategy_params
                set_strategy_params(params)
                from app.services.trade_tracer import set_strategy_state
                set_strategy_state(mint, strategy_name, True)
                add_strategy_log(f"✅ 策略已启用: {strategy_name}, 参数: {params}")
                return JSONResponse({
                    "success": True,
                    "message": f"策略 {strategy_name} 已启用",
                    "strategy": strategy_name,
                    "enabled": True
                })
            else:
                return JSONResponse({
                    "success": False,
                    "error": f"策略 {strategy_name} 不存在或加载失败"
                })
        else:
            # 获取当前策略的 mint
            current_mint = manager._mint if hasattr(manager, '_mint') else ""
            manager.disable()
            from app.taskcl.consumer import set_strategy_params
            set_strategy_params({})
            from app.services.trade_tracer import set_strategy_state
            set_strategy_state(current_mint, "", False)
            add_strategy_log("❌ 策略已禁用")
            return JSONResponse({
                "success": True,
                "message": "策略已禁用",
                "enabled": False
            })
    except Exception as e:
        logger.error(f"[策略API] 选择策略失败: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.post("/disable")
async def disable_strategy(request: Request):
    """禁用策略"""
    try:
        from app.taskcl.manager import get_strategy_manager
        from app.taskcl.consumer import stop_strategy_consumer, clear_strategy_queue, set_strategy_params
        
        manager = get_strategy_manager()
        current_mint = manager._mint if hasattr(manager, '_mint') else ""
        manager.disable()
        
        await stop_strategy_consumer()
        await clear_strategy_queue()
        set_strategy_params({})
        from app.services.trade_tracer import set_strategy_state
        set_strategy_state(current_mint, "", False)
        
        return JSONResponse({
            "success": True,
            "message": "策略已禁用"
        })
    except Exception as e:
        logger.error(f"[策略API] 禁用策略失败: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.get("/status")
async def get_strategy_status(request: Request):
    """获取策略状态"""
    try:
        from app.taskcl.manager import get_strategy_manager
        
        manager = get_strategy_manager()
        metrics = manager.get_metrics()
        
        return JSONResponse({
            "success": True,
            **metrics
        })
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)


@router.get("/logs")
async def get_strategy_logs():
    """获取策略日志"""
    return JSONResponse({
        "success": True,
        "logs": _strategy_logs.copy(),
        "state": _current_state
    })


@router.get("/state")
async def get_strategy_state():
    """获取策略执行状态"""
    return JSONResponse({
        "success": True,
        "state": _current_state
    })


@router.get("/trace/{mint}/download")
async def download_trace_report(mint: str):
    """下载交易流水追踪报告（Markdown 文件）"""
    from fastapi.responses import Response
    from app.services.trade_tracer import get_trace_report

    report = get_trace_report(mint)
    filename = f"trace_{mint[:12]}.md"
    return Response(
        content=report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/trace/{mint}/json")
async def get_trace_json(mint: str):
    """获取交易流水追踪数据（JSON）"""
    from app.services.trade_tracer import get_trace_json

    return JSONResponse(get_trace_json(mint))
