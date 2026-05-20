"""
Jupiter 交易 API 路由
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.services.jupiter_service import get_jupiter_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jupiter", tags=["Jupiter交易"])


class BuyRequest(BaseModel):
    """买入请求"""
    mint: str = Field(..., description="代币 Mint 地址")
    sol_amount: float = Field(..., gt=0, description="购买的 SOL 数量")


class SellRequest(BaseModel):
    """卖出请求"""
    mint: str = Field(..., description="代币 Mint 地址")
    token_amount: Optional[float] = Field(default=None, description="直接传代币数量")
    percent: Optional[int] = Field(default=None, ge=1, le=100, description="或传百分比 (1-100)")


class BalanceResponse(BaseModel):
    """余额响应"""
    sol_balance: float
    token_balance: Optional[dict] = None
    wallet_address: str


@router.get("/wallet")
async def get_wallet_info():
    """获取钱包信息"""
    try:
        service = get_jupiter_service()
        return {
            "wallet_address": service.wallet_address,
            "sol_balance": service.get_sol_balance()
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/balance/{mint}")
async def get_token_balance(mint: str):
    """获取代币余额"""
    try:
        service = get_jupiter_service()
        return {
            "token_balance": service.get_token_balance(mint)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/buy")
async def buy_token(request: BuyRequest):
    """买入代币（滑点从设置中读取）
    
    Args:
        mint: 代币 Mint 地址
        sol_amount: 购买的 SOL 数量
    
    Returns:
        交易结果，包含 signature 和链接
    """
    try:
        logger.info(f"买入请求: mint={request.mint}, sol_amount={request.sol_amount}")
        service = get_jupiter_service()
        
        result = service.buy(
            mint=request.mint,
            sol_amount=request.sol_amount
        )
        
        if result["success"]:
            return {
                "success": True,
                "type": "BUY",
                "message": f"买入成功！使用了 {request.sol_amount} SOL",
                "signature": result.get("signature"),
                "status": result.get("status"),
                "in_amount": result.get("in_amount"),
                "out_amount": result.get("out_amount"),
                "tx_url": result.get("tx_url")
            }
        else:
            return {
                "success": False,
                "type": "BUY",
                "message": f"买入失败: {result.get('error')}",
                "error": result.get("error")
            }
    except Exception as e:
        logger.error(f"买入请求异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sell")
async def sell_token(request: SellRequest):
    """卖出代币（滑点从设置中读取）
    
    Args:
        mint: 代币 Mint 地址
        token_amount: 直接传代币数量（内部单位）
        percent: 或传百分比 (1-100)
    
    Returns:
        交易结果，包含 signature 和链接
    """
    try:
        service = get_jupiter_service()
        
        # 如果传入 token_amount，先获取余额计算百分比（优先使用缓存）
        percent = request.percent
        if request.token_amount is not None:
            cached = service._balance_cache.get(request.mint)
            if cached:
                token_info = cached
                logger.info(f"代币余额 (缓存): {token_info.get('balance_sol', 0)}")
            else:
                token_info = service.get_token_balance(request.mint)
                logger.info(f"代币余额: {token_info.get('balance_sol', 0)}")
            
            total_balance = token_info.get("balance", 0)
            if total_balance > 0:
                percent = min(100, int(request.token_amount / total_balance * 100))
            logger.info(f"卖出请求: mint={request.mint}, token_amount={request.token_amount}, 换算 percent={percent}%")
        else:
            if percent is None:
                percent = 100
            logger.info(f"卖出请求: mint={request.mint}, percent={percent}%")
        
        result = service.sell(
            mint=request.mint,
            percent=percent
        )
        
        if result["success"]:
            return {
                "success": True,
                "type": "SELL",
                "message": f"卖出成功！获得了 {result.get('out_amount_sol', 0):.6f} SOL",
                "signature": result.get("signature"),
                "status": result.get("status"),
                "in_amount": result.get("in_amount"),
                "out_amount": result.get("out_amount"),
                "out_amount_sol": result.get("out_amount_sol"),
                "tx_url": result.get("tx_url")
            }
        else:
            return {
                "success": False,
                "type": "SELL",
                "message": f"卖出失败: {result.get('error')}",
                "error": result.get("error")
            }
    except Exception as e:
        logger.error(f"卖出请求异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-settings")
async def update_jupiter_settings():
    """更新 Jupiter 服务设置（从数据库读取最新配置）"""
    from app.utils.database import get_db
    from app.services.settings_service import get_setting
    
    try:
        service = get_jupiter_service()
        db = next(get_db())
        
        # 更新 priority 设置
        priority = get_setting(db, "jupiter_priority") or "Medium"
        service._priority = priority
        logger.info(f"Jupiter priority 更新为: {priority}")
        
        # 更新滑点设置
        service._buy_slippage = service._load_slippage("buy")
        service._sell_slippage = service._load_slippage("sell")
        logger.info(f"Jupiter 滑点已更新: buy={service._buy_slippage}, sell={service._sell_slippage}")
        
        return {
            "success": True,
            "priority": priority,
            "buy_slippage": service._buy_slippage,
            "sell_slippage": service._sell_slippage
        }
    except Exception as e:
        logger.error(f"更新 Jupiter 设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
