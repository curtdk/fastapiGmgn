"""交易处理引擎 - 逐条处理交易，计算净流入等指标"""
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert

from app.models.trade import TradeAnalysis
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


async def process_trade(db: Session, tx_detail: Dict[str, Any]):
    """
    处理单条交易
    计算净流入、价格、钱包标记等
    TODO: 具体处理逻辑待补充
    """
    sig = tx_detail.get("sig", "")
    if not sig:
        return

    try:
        # 检查是否已处理
        existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == sig).first()
        if existing:
            return

        # ===== 处理逻辑框架（待补充） =====
        # 根据交易详情计算：
        # - net_sol_flow: SOL净流入
        # - net_token_flow: Token净流入
        # - price_per_token: 单价
        # - wallet_tag: 钱包标记

        net_sol_flow = 0.0
        net_token_flow = 0.0
        price_per_token = 0.0
        wallet_tag = None

        # 从交易详情中提取
        tx_type = tx_detail.get("transaction_type", "")
        amount = tx_detail.get("amount", 0) or 0.0

        if tx_type == "SWAP":
            # TODO: 根据 swap 信息计算净流入
            pass
        elif tx_type == "TRANSFER":
            # TODO: 根据 transfer 信息计算净流入
            pass

        # 入库
        analysis = TradeAnalysis(
            sig=sig,
            net_sol_flow=net_sol_flow,
            net_token_flow=net_token_flow,
            price_per_token=price_per_token,
            wallet_tag=wallet_tag,
            processed_at=datetime.utcnow(),
        )
        db.add(analysis)
        db.commit()

        # 推送处理结果到前端
        await ws_manager.broadcast(tx_detail.get("token_mint", ""), {
            "type": "trade",
            "data": {
                **tx_detail,
                "net_sol_flow": net_sol_flow,
                "net_token_flow": net_token_flow,
                "price_per_token": price_per_token,
                "wallet_tag": wallet_tag,
            }
        })

        logger.debug(f"[处理] {sig[:8]}... 完成")

    except Exception as e:
        logger.error(f"[处理] {sig[:8]}... 异常: {e}", exc_info=True)
        db.rollback()


async def calculate_metrics(db: Session, mint: str) -> Dict[str, float]:
    """
    计算四个核心指标
    TODO: 具体计算逻辑待补充
    """
    # 获取该 mint 的所有已处理交易
    from sqlalchemy import func
    from app.models.models import Transaction

    trades = (
        db.query(Transaction, TradeAnalysis)
        .join(TradeAnalysis, Transaction.sig == TradeAnalysis.sig)
        .filter(Transaction.token_mint == mint)
        .order_by(Transaction.block_time.desc())
        .all()
    )

    # ===== 指标计算框架（待补充） =====
    current_bet = 0.0       # 本轮下注
    current_cost = 0.0      # 本轮成本
    realized_profit = 0.0   # 已落袋

    for tx, analysis in trades:
        # TODO: 根据每笔交易的净流入计算指标
        pass

    return {
        "current_bet": current_bet,
        "current_cost": current_cost,
        "realized_profit": realized_profit,
        "trade_count": len(trades),
    }
