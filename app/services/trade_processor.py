"""交易处理引擎 - 指数计算流程（Redis 均价法）"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import case

from app.models.trade import TradeAnalysis
from app.models.models import Transaction
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

# 模块级变量
_trade_queue: asyncio.Queue = None
_consumer_task: Optional[asyncio.Task] = None
_mint: str = ""


# ──────────────────────────────────────────────────────────
# Redis 辅助方法
# ──────────────────────────────────────────────────────────

async def _get_redis():
    """获取 Redis 连接"""
    from app.services.dealer_detector import _redis
    return _redis


def user_key(address: str) -> str:
    """获取用户 Redis Key"""
    return f"user:{address}"


async def _get_metrics_key(mint: str) -> str:
    """获取全局指标 Redis Key"""
    return f"metrics:{mint}"


async def get_trader_state(redis, mint: str, address: str, sig: str = None) -> dict:
    """
    获取用户状态（从 user:{address} 读取）
    如果 status=unknown 且有 sig，自动入队进行庄家检测
    
    Redis 数据结构：
      user:{address}
        status: "unknown" | "dealer" | "retail"
        conditions: '["C001"]'
        {mint}_holdingQty: "1000"
        {mint}_holdingCost: "5.5"
        {mint}_avgPrice: "0.0055"
        {mint}_totalBuyAmount: "10"
        {mint}_totalSellAmount: "3"
        {mint}_totalSellPrincipal: "2.5"
    """
    if not redis:
        return _default_trader_state()
    
    key = user_key(address)
    state = await redis.hgetall(key)
    
    if not state:
        state = _default_trader_state()
        # 如果是新用户且有 sig，自动入队检测
        if sig:
            await _enqueue_dealer_check(address, mint, sig)
    else:
        # 读取 status 和 conditions
        status = state.get("status", "unknown")
        try:
            conditions = json.loads(state.get("conditions", "[]"))
        except:
            conditions = []
        state["status"] = status
        state["conditions"] = conditions
        
        # 读取 mint 相关字段
        state["holdingQty"] = float(state.get(f"{mint}_holdingQty", "0"))
        state["holdingCost"] = float(state.get(f"{mint}_holdingCost", "0"))
        state["avgPrice"] = float(state.get(f"{mint}_avgPrice", "0"))
        state["totalBuyAmount"] = float(state.get(f"{mint}_totalBuyAmount", "0"))
        state["totalSellAmount"] = float(state.get(f"{mint}_totalSellAmount", "0"))
        state["totalSellPrincipal"] = float(state.get(f"{mint}_totalSellPrincipal", "0"))
        
        # 如果是 unknown 且有 sig，自动入队检测
        if status == "unknown" and sig:
            await _enqueue_dealer_check(address, mint, sig)
    
    return state


async def _enqueue_dealer_check(address: str, mint: str, sig: str):
    """入队庄家检测"""
    try:
        from app.services.dealer_detector import _dealer_check_queue
        if _dealer_check_queue is not None:
            await _dealer_check_queue.put((address, mint, sig))
            logger.debug(f"[庄家检测] 入队 {address[:8]}... sig={sig[:8]}...")
    except Exception as e:
        logger.warning(f"[庄家检测] 入队失败: {e}")


def _default_trader_state() -> dict:
    """默认交易员状态"""
    return {
        "status": "unknown",
        "conditions": [],
        "holdingQty": 0.0,
        "holdingCost": 0.0,
        "avgPrice": 0.0,
        "totalBuyAmount": 0.0,
        "totalSellAmount": 0.0,
        "totalSellPrincipal": 0.0,
    }


async def save_trader_state(redis, mint: str, address: str, state: dict):
    """
    保存用户状态（到 user:{address}）
    """
    if not redis:
        return
    
    key = user_key(address)
    save_data = {
        "status": state.get("status", "unknown"),
        "conditions": json.dumps(state.get("conditions", [])),
        f"{mint}_holdingQty": str(state.get("holdingQty", 0)),
        f"{mint}_holdingCost": str(state.get("holdingCost", 0)),
        f"{mint}_avgPrice": str(state.get("avgPrice", 0)),
        f"{mint}_totalBuyAmount": str(state.get("totalBuyAmount", 0)),
        f"{mint}_totalSellAmount": str(state.get("totalSellAmount", 0)),
        f"{mint}_totalSellPrincipal": str(state.get("totalSellPrincipal", 0)),
    }
    await redis.hset(key, mapping=save_data)


async def get_metrics(redis, mint: str) -> dict:
    """获取全局指标"""
    if not redis:
        return {
            "total_bet": 0.0,
            "realized_profit": 0.0,
            "dealer_count": 0
        }
    
    key = await _get_metrics_key(mint)
    metrics = await redis.hgetall(key)
    
    if not metrics:
        metrics = {
            "total_bet": 0.0,
            "realized_profit": 0.0,
            "dealer_count": 0
        }
    else:
        for k in ["total_bet", "realized_profit", "dealer_count"]:
            metrics[k] = float(metrics.get(k, "0"))
    
    return metrics


async def update_metrics_delta(redis, mint: str, delta_bet: float, delta_profit: float):
    """更新全局指标增量"""
    if not redis:
        return
    
    key = await _get_metrics_key(mint)
    if delta_bet != 0:
        await redis.hincrbyfloat(key, "total_bet", delta_bet)
    if delta_profit != 0:
        await redis.hincrbyfloat(key, "realized_profit", delta_profit)


async def clear_mint_redis(mint: str):
    """清理指定 mint 的指标数据（不清理用户数据）"""
    redis = await _get_redis()
    if not redis:
        logger.warning("[清理] Redis 未连接")
        return
    
    try:
        # 只删除 metrics:{mint}
        metrics_key = await _get_metrics_key(mint)
        await redis.delete(metrics_key)
        logger.info(f"[清理] 删除指标 key: {metrics_key}")
        
    except Exception as e:
        logger.error(f"[清理] Redis 数据失败: {e}")


# ──────────────────────────────────────────────────────────
# 指数计算核心
# ──────────────────────────────────────────────────────────

async def _calculate_index(tx_detail: Dict[str, Any], mint: str) -> Dict[str, Any]:
    """
    均价法计算指数
    
    买入逻辑：
        holdingQty += buyQty
        holdingCost += buyAmount
        totalBuyAmount += buyAmount
        avgPrice = holdingCost / holdingQty
    
    卖出逻辑：
        sellPrincipal = sellQty × avgPrice
        totalSellPrincipal += sellPrincipal
        totalSellAmount += sellAmount
        holdingQty -= sellQty
        holdingCost -= sellPrincipal
    """
    redis = await _get_redis()
    
    address = tx_detail.get("from_address", "")
    sig = tx_detail.get("sig", "")
    tx_type = tx_detail.get("transaction_type", "")
    sol_spent = tx_detail.get("sol_spent", 0) or 0.0
    amount = tx_detail.get("amount", 0) or 0.0
    
    if not address:
        return {}
    
    # 获取用户状态（传入 sig 用于 unknown 时自动入队检测）
    state = await get_trader_state(redis, mint, address, sig)
    
    # 庄家直接跳过计算
    if state.get("status") == "dealer":
        return {"is_dealer": True}
    
    old_holding_cost = state.get("holdingCost", 0)
    old_realized = state.get("totalSellAmount", 0) - state.get("totalSellPrincipal", 0)
    
    if tx_type == "BUY":
        buy_amount = abs(sol_spent)
        buy_qty = amount
        
        state["holdingQty"] += buy_qty
        state["holdingCost"] += buy_amount
        state["totalBuyAmount"] += buy_amount
        if state["holdingQty"] > 0:
            state["avgPrice"] = state["holdingCost"] / state["holdingQty"]
        
        logger.debug(f"[计算] {address[:8]}... BUY {buy_qty} @ {buy_amount} SOL")
    
    elif tx_type == "SELL":
        sell_qty = abs(amount)
        sell_amount = abs(sol_spent)
        
        # 卖出本金 = 卖出数量 × 当时均价
        sell_principal = sell_qty * state.get("avgPrice", 0)
        
        state["totalSellPrincipal"] += sell_principal
        state["totalSellAmount"] += sell_amount
        state["holdingQty"] -= sell_qty
        state["holdingCost"] -= sell_principal
        
        # 防负值
        if state["holdingQty"] < 0.001:
            state["holdingQty"] = 0
            state["holdingCost"] = 0
            state["avgPrice"] = 0
        
        # 更新均价
        if state["holdingQty"] > 0:
            state["avgPrice"] = state["holdingCost"] / state["holdingQty"]
        
        realized = sell_amount - sell_principal
        logger.debug(f"[计算] {address[:8]}... SELL {sell_qty} @ {sell_amount} SOL, 落袋: {realized:.6f} SOL")
    
    # 保存状态
    await save_trader_state(redis, mint, address, state)
    
    # 更新全局指标
    new_holding_cost = state.get("holdingCost", 0)
    new_realized = state.get("totalSellAmount", 0) - state.get("totalSellPrincipal", 0)
    
    delta_bet = new_holding_cost - old_holding_cost
    delta_profit = new_realized - old_realized
    
    await update_metrics_delta(redis, mint, delta_bet, delta_profit)
    
    return {
        "holdingQty": state.get("holdingQty", 0),
        "holdingCost": state.get("holdingCost", 0),
        "avgPrice": state.get("avgPrice", 0),
        "is_dealer": False
    }


async def exclude_dealer(mint: str, address: str):
    """排除庄家：从汇总中减去该用户贡献"""
    redis = await _get_redis()
    
    if not redis:
        return
    
    state = await get_trader_state(redis, mint, address)
    
    # 从汇总中减去持仓成本
    await update_metrics_delta(redis, mint, -state.get("holdingCost", 0), 0)
    
    # 更新庄家计数
    metrics_key = await _get_metrics_key(mint)
    await redis.hincrbyfloat(metrics_key, "dealer_count", 1)
    
    logger.info(f"[庄家排除] {address[:8]}... 已从汇总中排除")


# ──────────────────────────────────────────────────────────
# 队列消费
# ──────────────────────────────────────────────────────────

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
    from app.utils.database import SessionLocal
    
    logger.info(f"[消费者] 开始消化队列 mint={mint}")

    while True:
        tx_detail = await _trade_queue.get()
        if tx_detail is None:  # 毒丸信号，结束
            logger.info(f"[消费者] 收到停止信号 mint={mint}")
            break

        sig = tx_detail.get("sig", "")
        if not sig:
            continue

        db = SessionLocal()
        try:
            # 去重检查
            existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == sig).first()
            if existing:
                continue

            # 庄家判定 + 指数计算
            metrics = await _calculate_index(tx_detail, mint)

            # 写入 TradeAnalysis
            analysis = TradeAnalysis(
                sig=sig,
                net_sol_flow=tx_detail.get("sol_spent", 0) or 0.0,
                net_token_flow=tx_detail.get("amount", 0) or 0.0,
                price_per_token=metrics.get("avgPrice", 0),
                wallet_tag="dealer" if metrics.get("is_dealer") else "unknown",
                processed_at=datetime.utcnow(),
            )
            db.add(analysis)
            db.commit()
            
            # 非庄家交易 → WebSocket 广播
            if not metrics.get("is_dealer"):
                await ws_manager.broadcast(mint, {
                    "type": "trade",
                    "data": {
                        **tx_detail,
                        "wallet_tag": "unknown",
                    }
                })

            logger.debug(f"[消费者] {sig[:8]}... 完成")

        except Exception as e:
            logger.error(f"[消费者] {sig[:8]}... 异常: {e}", exc_info=True)
            db.rollback()
        finally:
            db.close()


async def process_trade(db: Session, tx_detail: Dict[str, Any]):
    """处理单条交易（保留兼容）"""
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
    """全量指数计算"""
    logger.info(f"[全量计算] 开始计算 mint={mint}")

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
            existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == tx.sig).first()
            if existing:
                continue

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
                "fee": tx.fee,
                "source": tx.source,
                # 风险相关
                "risk_score": tx.risk_score or 0,
                "risk_verdict": tx.risk_verdict,
                "risk_indicators": tx.risk_indicators,
                # Compute Unit
                "priority_fee": tx.priority_fee,
                "cu_consumed": tx.cu_consumed or 0,
                "cu_limit": tx.cu_limit or 200000,
                "cu_price": tx.cu_price or 0,
                # 指令统计
                "instructions_count": tx.instructions_count or 0,
                "inner_instructions_count": tx.inner_instructions_count or 0,
                "total_instruction_count": tx.total_instruction_count or 0,
                "account_keys_count": tx.account_keys_count or 0,
                "uses_lookup_table": tx.uses_lookup_table or False,
                "signers_count": tx.signers_count or 0,
                # 指令详情
                "main_instructions": tx.main_instructions,
                "inner_instructions": tx.inner_instructions,
                "program_ids": tx.program_ids,
            }

            # 指数计算（get_trader_state 会自动检测 unknown 状态并入队）
            metrics = await _calculate_index(tx_detail, mint)

            analysis = TradeAnalysis(
                sig=tx.sig,
                net_sol_flow=tx.sol_spent or 0.0,
                net_token_flow=tx.amount or 0.0,
                price_per_token=metrics.get("avgPrice", 0),
                wallet_tag="dealer" if metrics.get("is_dealer") else "unknown",
                processed_at=datetime.utcnow(),
            )
            db.add(analysis)
            db.commit()

            if not metrics.get("is_dealer"):
                await ws_manager.broadcast(mint, {
                    "type": "trade",
                    "data": {
                        **tx_detail,
                        "wallet_tag": "unknown",
                    }
                })

        except Exception as e:
            logger.error(f"[全量计算] 处理 {tx.sig[:8]}... 失败: {e}")
            db.rollback()

    logger.info(f"[全量计算] 完成 mint={mint}")


async def reset_processor(mint: str, db: Session):
    """关闭按钮触发：清理指定 mint 的状态"""
    global _trade_queue, _consumer_task, _mint

    logger.info(f"[重置] 开始清理 mint={mint}")

    # 1. 停止消费者任务
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

    # 3. 清理 Redis 指标数据（不清理用户数据）
    await clear_mint_redis(mint)

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

    # 5. 重置全局变量
    _trade_queue = asyncio.Queue()
    _consumer_task = None
    _mint = ""

    logger.info(f"[重置] 清理完成 mint={mint}")


async def calculate_metrics(db: Session, mint: str) -> Dict[str, float]:
    """计算四个核心指标"""
    redis = await _get_redis()
    
    if not redis:
        return {"current_bet": 0, "current_cost": 0, "realized_profit": 0, "trade_count": 0}

    metrics = await get_metrics(redis, mint)
    trade_count = db.query(Transaction).filter(Transaction.token_mint == mint).count()
    
    total_bet = metrics.get("total_bet", 0)
    realized_profit = metrics.get("realized_profit", 0)
    
    return {
        "current_bet": total_bet,
        "realized_profit": realized_profit,
        "current_cost": total_bet - realized_profit,
        "trade_count": trade_count
    }
