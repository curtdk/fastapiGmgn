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
    slippage_bps: int = Field(default=500, ge=1, le=10000, description="滑点 (bps)")


class SellRequest(BaseModel):
    """卖出请求"""
    mint: str = Field(..., description="代币 Mint 地址")
    percent: int = Field(default=100, ge=1, le=100, description="卖出百分比 (1-100)")
    slippage_bps: int = Field(default=500, ge=1, le=10000, description="滑点 (bps)")


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
            "wallet_address": service.wallet_address,
            "sol_balance": service.get_sol_balance(),
            "token_balance": service.get_token_balance(mint)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/buy")
async def buy_token(request: BuyRequest):
    """买入代币
    
    Args:
        mint: 代币 Mint 地址
        sol_amount: 购买的 SOL 数量
        slippage_bps: 滑点 (默认 500 = 5%)
    
    Returns:
        交易结果，包含 signature 和链接
    """
    try:
        logger.info(f"买入请求: mint={request.mint}, sol_amount={request.sol_amount}")
        service = get_jupiter_service()
        
        result = service.buy(
            mint=request.mint,
            sol_amount=request.sol_amount,
            slippage_bps=request.slippage_bps
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
    """卖出代币
    
    Args:
        mint: 代币 Mint 地址
        percent: 卖出百分比 (1-100)
        slippage_bps: 滑点 (默认 500 = 5%)
    
    Returns:
        交易结果，包含 signature 和链接
    """
    try:
        logger.info(f"卖出请求: mint={request.mint}, percent={request.percent}%")
        service = get_jupiter_service()
        
        result = service.sell(
            mint=request.mint,
            percent=request.percent,
            slippage_bps=request.slippage_bps
        )
        
        if result["success"]:
            return {
                "success": True,
                "type": "SELL",
                "message": f"卖出成功！卖出了 {request.percent}% 的代币，获得 {result.get('out_amount_sol', 0):.6f} SOL",
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
