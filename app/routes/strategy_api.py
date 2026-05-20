"""
策略相关 API 端点
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import logging

router = APIRouter(prefix="/api/strategy", tags=["策略"])

logger = logging.getLogger(__name__)


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
        
        from app.taskcl.manager import get_strategy_manager
        from app.taskcl.consumer import start_strategy_consumer
        
        manager = get_strategy_manager()
        
        if enabled:
            if manager.select(strategy_name, mint):
                manager.enable()
                await start_strategy_consumer()
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
            manager.disable()
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
        from app.taskcl.consumer import stop_strategy_consumer, clear_strategy_queue
        
        manager = get_strategy_manager()
        manager.disable()
        
        await stop_strategy_consumer()
        await clear_strategy_queue()
        
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