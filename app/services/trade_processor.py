"""交易处理引擎 - 指数计算流程"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import case

from app.models.trade import TradeAnalysis
from app.models.models import Transaction
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


class IndexState:
    """内存中的指数累计状态"""
    current_bet: float = 0.0       # 本轮下注
    dealer_profit: float = 0.0     # 庄家利润
    realized_profit: float = 0.0   # 已落袋
    unrealized_pnl: float = 0.0    # 浮盈浮亏


# 模块级变量
_trade_queue: asyncio.Queue = None
_index_state: Optional[IndexState] = None
_consumer_task: Optional[asyncio.Task] = None
_mint: str = ""


async def _calculate_index(tx_detail: Dict[str, Any], state: IndexState, mint: str) -> Dict[str, Any]:
    """
    根据当前交易更新指数状态，同时判定用户是否为庄家
    返回 metrics dict，包含 user_status 和 is_dealer 标志
    """
    from app.services.dealer_detector import check_and_classify_user

    address = tx_detail.get("from_address", "")
    sig = tx_detail.get("sig", "")
    tx_type = tx_detail.get("transaction_type", "")
    sol_spent = tx_detail.get("sol_spent", 0) or 0.0

    # 庄家判定（查 Redis，未知则异步触发检测）
    user_status = await check_and_classify_user(address, mint, sig)

    # 指数计算（BUY/SELL/TRANSFER）
    if tx_type == "BUY":
        state.current_bet += sol_spent
    elif tx_type == "SELL":
        pass  # TODO: 计算卖出逻辑
    elif tx_type == "TRANSFER":
        pass  # TODO: 处理转账逻辑

    return {
        "current_bet": state.current_bet,
        "dealer_profit": state.dealer_profit,
        "realized_profit": state.realized_profit,
        "unrealized_pnl": state.unrealized_pnl,
        "user_status": user_status,
        "is_dealer": user_status == "dealer",
    }


async def enqueue_trade(tx_detail: Dict[str, Any]):
    """WS 消息到达时调用 — 只入队，不处理"""
    global _trade_queue
    if _trade_queue is None:
        _trade_queue = asyncio.Queue()
    await _trade_queue.put(tx_detail)


async def start_consumer(mint: str):
    """历史 tx 处理完后，启动消费者消化队列"""
    global _consumer_task, _mint
    _mint = mint
    _consumer_task = asyncio.create_task(_consumer_loop(mint))
    logger.info(f"[消费者] 已启动 mint={mint}")


async def _consumer_loop(mint: str):
    """单一消费者，持续从队列取 tx 串行处理"""
    global _index_state
    logger.info(f"[消费者] 开始消化队列 mint={mint}")

    from app.utils.database import SessionLocal

    while True:
        tx_detail = await _trade_queue.get()
        if tx_detail is None:  # 毒丸信号，结束
            logger.info(f"[消费者] 收到停止信号 mint={mint}")
            break

        sig = tx_detail.get("sig", "")
        if not sig:
            continue

        try:
            db = SessionLocal()
            try:
                # 去重检查
                existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == sig).first()
                if existing:
                    continue

                # 庄家判定 + 指数计算
                metrics = await _calculate_index(tx_detail, _index_state, mint)

                # 写入 TradeAnalysis（dealer 也写，保证数据完整）
                analysis = TradeAnalysis(
                    sig=sig,
                    net_sol_flow=tx_detail.get("sol_spent", 0) or 0.0,
                    net_token_flow=tx_detail.get("amount", 0) or 0.0,
                    price_per_token=0.0,
                    wallet_tag=metrics["user_status"],
                    processed_at=datetime.utcnow(),
                )
                db.add(analysis)
                db.commit()
            finally:
                db.close()

            # dealer 不推送实时交易列表，retail/unknown 正常推送
            if not metrics["is_dealer"]:
                await ws_manager.broadcast(mint, {
                    "type": "trade",
                    "data": {
                        **tx_detail,
                        **{k: v for k, v in metrics.items() if k != "is_dealer"},
                    }
                })

            logger.debug(f"[消费者] {sig[:8]}... 完成 user={metrics['user_status']}")

        except Exception as e:
            logger.error(f"[消费者] {sig[:8]}... 异常: {e}", exc_info=True)


async def process_trade(db: Session, tx_detail: Dict[str, Any]):
    """
    处理单条交易（保留兼容，新流程使用 enqueue_trade）
    """
    sig = tx_detail.get("sig", "")
    if not sig:
        return

    try:
        existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == sig).first()
        if existing:
            return

        analysis = TradeAnalysis(
            sig=sig,
            net_sol_flow=0.0,
            net_token_flow=0.0,
            price_per_token=0.0,
            wallet_tag=None,
            processed_at=datetime.utcnow(),
        )
        db.add(analysis)
        db.commit()

        await ws_manager.broadcast(tx_detail.get("token_mint", ""), {
            "type": "trade",
            "data": tx_detail,
        })

        logger.debug(f"[处理] {sig[:8]}... 完成")

    except Exception as e:
        logger.error(f"[处理] {sig[:8]}... 异常: {e}", exc_info=True)
        db.rollback()


async def run_full_calculation(db: Session, mint: str):
    """
    全量指数计算
    历史 tx 直接串行处理，更新 _index_state
    处理完后由调用方启动消费者
    """
    global _index_state
    logger.info(f"[全量计算] 开始计算 mint={mint}")

    # 初始化指数状态
    _index_state = IndexState()

    # 按全量排序获取所有交易
    trades = (
        db.query(Transaction)
        .filter(Transaction.token_mint == mint)
        .order_by(
            case((Transaction.source == "rpc_fill", 0), else_=1),
            Transaction.id.asc()
        )
        .all()
    )

    logger.info(f"[全量计算] 共 {len(trades)} 条交易待处理")

    for tx in trades:
        try:
            # 去重检查
            existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == tx.sig).first()
            if existing:
                continue

            # 构建 tx_detail
            tx_detail = {
                "sig": tx.sig,
                "slot": tx.slot,
                "block_time": tx.block_time,
                "from_address": tx.from_address,
                "to_address": tx.to_address,
                "amount": tx.amount,
                "token_mint": tx.token_mint,
                "token_symbol": tx.token_symbol,
                "transaction_type": tx.transaction_type,
                "dex": tx.dex,
                "pool_address": tx.pool_address,
                "sol_spent": tx.sol_spent or 0.0,
            }

            # 庄家判定 + 指数计算
            metrics = await _calculate_index(tx_detail, _index_state, mint)

            # 写入 TradeAnalysis（dealer 也写）
            analysis = TradeAnalysis(
                sig=tx.sig,
                net_sol_flow=tx.sol_spent or 0.0,
                net_token_flow=tx.amount or 0.0,
                price_per_token=0.0,
                wallet_tag=metrics["user_status"],
                processed_at=datetime.utcnow(),
            )
            db.add(analysis)
            db.commit()

            # dealer 不推送实时交易列表
            if not metrics["is_dealer"]:
                await ws_manager.broadcast(mint, {
                    "type": "trade",
                    "data": {
                        **tx_detail,
                        **{k: v for k, v in metrics.items() if k != "is_dealer"},
                    }
                })

        except Exception as e:
            logger.error(f"[全量计算] 处理 {tx.sig[:8]}... 失败: {e}")
            db.rollback()

    logger.info(f"[全量计算] 完成 mint={mint}")


async def reset_processor(mint: str, db: Session):
    """
    关闭按钮触发：完整清理指定 mint 的所有状态
    - 停止消费者任务
    - 清空队列
    - 重置所有全局变量和 IndexState
    - 删除 DB 中该 mint 的 Transaction 和 TradeAnalysis 记录
    注意：Redis 用户数据永久保留，不在此清理
    """
    global _trade_queue, _index_state, _consumer_task, _mint

    logger.info(f"[重置] 开始清理 mint={mint}")

    # 1. 停止消费者任务（发送毒丸信号）
    if _consumer_task and not _consumer_task.done():
        if _trade_queue is not None:
            await _trade_queue.put(None)
        try:
            await asyncio.wait_for(_consumer_task, timeout=3.0)
        except asyncio.TimeoutError:
            _consumer_task.cancel()
            try:
                await _consumer_task
            except asyncio.CancelledError:
                pass
        logger.info(f"[重置] 消费者任务已停止")

    # 2. 清空队列
    if _trade_queue is not None:
        cleared = 0
        while not _trade_queue.empty():
            try:
                _trade_queue.get_nowait()
                cleared += 1
            except asyncio.QueueEmpty:
                break
        logger.info(f"[重置] 队列已清空，丢弃 {cleared} 条消息")

    # 3. 重置全局变量
    _trade_queue = asyncio.Queue()
    _index_state = None
    _consumer_task = None
    _mint = ""

    # 4. 删除 DB 中该 mint 的记录
    try:
        sigs = [row.sig for row in db.query(Transaction.sig).filter(Transaction.token_mint == mint).all()]
        if sigs:
            deleted_analysis = db.query(TradeAnalysis).filter(TradeAnalysis.sig.in_(sigs)).delete(synchronize_session=False)
            logger.info(f"[重置] 删除 TradeAnalysis {deleted_analysis} 条")
        deleted_tx = db.query(Transaction).filter(Transaction.token_mint == mint).delete(synchronize_session=False)
        logger.info(f"[重置] 删除 Transaction {deleted_tx} 条")
        db.commit()
    except Exception as e:
        logger.error(f"[重置] 删除 DB 记录失败: {e}", exc_info=True)
        db.rollback()

    logger.info(f"[重置] 清理完成 mint={mint}")


async def calculate_metrics(db: Session, mint: str) -> Dict[str, float]:
    """
    计算四个核心指标
    TODO: 具体计算逻辑待补充
    """
    trades = (
        db.query(Transaction, TradeAnalysis)
        .join(TradeAnalysis, Transaction.sig == TradeAnalysis.sig)
        .filter(Transaction.token_mint == mint)
        .order_by(
            case((Transaction.source == "rpc_fill", 0), else_=1),
            Transaction.id.asc()
        )
        .all()
    )

    current_bet = 0.0
    current_cost = 0.0
    realized_profit = 0.0

    for tx, analysis in trades:
        pass

    return {
        "current_bet": current_bet,
        "current_cost": current_cost,
        "realized_profit": realized_profit,
        "trade_count": len(trades),
    }
