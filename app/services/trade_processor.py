"""交易处理引擎 - 指数计算流程（Redis 均价法）"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy.orm import Session

from app.services import tx_redis
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
        # 新用户：尝试本地快速判断庄家（C002-C005）
        if sig:
            tx_detail = await tx_redis.get_tx(sig)
            if tx_detail:
                from app.services.dealer_detector import _check_local_dealer_conditions
                is_dealer, conditions = _check_local_dealer_conditions(tx_detail, state)
                state["conditions"] = conditions
                if is_dealer:
                    state["status"] = "dealer"
                    await save_trader_state(redis, mint, address, state)
                    logger.info(f"[庄家判定] {address[:8]}... 新用户本地判断为庄家 (C002-C005)")
                    return state
                # 非庄家也要保存状态，记录所有用户
                await save_trader_state(redis, mint, address, state)
                logger.info(f"[庄家判定] {address[:8]}... 新用户本地判断为非庄家 (C002-C005)，入队等待 C001")
            # 本地判断不是庄家或无交易详情：入队等待 C001 检测
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
    """默认交易员状态（只保留 status 和 conditions，持仓数据从 Redis 读取）"""
    return {
        "status": "unknown",
        "conditions": [],
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
# 庄家条件详情
# ──────────────────────────────────────────────────────────

async def _get_dealer_conditions_detail(redis, address: str, tx_detail: dict) -> dict:
    """
    获取庄家条件详情（包含阈值对比信息）
    """
    from app.utils.database import SessionLocal
    from app.services.settings_service import get_setting, get_float_setting, get_int_setting
    
    key = user_key(address)
    state = await redis.hgetall(key)
    
    try:
        conditions = json.loads(state.get("conditions", "[]"))
    except:
        conditions = []
    
    result = {"conditions": conditions, "details": {}}
    db = SessionLocal()
    try:
        # C001: closeAccount
        if "C001" in conditions:
            result["details"]["C001"] = {
                "name": "首笔交易包含 closeAccount",
                "enabled": get_setting(db, "dealer_c001_enabled") == "true"
            }
        
        # C002: ALT
        if "C002" in conditions:
            result["details"]["C002"] = {
                "name": "使用 ALT（地址查找表）",
                "enabled": get_setting(db, "dealer_alt_enabled") == "true",
                "value": tx_detail.get("uses_lookup_table", False)
            }
        
        # C003: Gas 费
        if "C003" in conditions:
            gas_max = get_float_setting(db, "dealer_gas_max", 0.00001)
            result["details"]["C003"] = {
                "name": "Gas 费小于阈值",
                "enabled": get_setting(db, "dealer_gas_enabled") == "true",
                "threshold": f"< {gas_max:.8f} SOL",
                "value": f"{tx_detail.get('fee', 0):.8f} SOL"
            }
        
        # C004: CU
        if "C004" in conditions:
            cu_min = get_int_setting(db, "dealer_cu_min", 0)
            cu_max = get_int_setting(db, "dealer_cu_max", 200000)
            result["details"]["C004"] = {
                "name": "CU 在范围内",
                "enabled": get_setting(db, "dealer_cu_enabled") == "true",
                "threshold": f"{cu_min} - {cu_max}",
                "value": tx_detail.get("cu_consumed", 0)
            }
        
        # C005: Risk Score
        if "C005" in conditions:
            risk_min = get_int_setting(db, "dealer_risk_min", 0)
            result["details"]["C005"] = {
                "name": "风险分大于阈值",
                "enabled": get_setting(db, "dealer_risk_enabled") == "true",
                "threshold": f"> {risk_min}",
                "value": tx_detail.get("risk_score", 0)
            }
    finally:
        db.close()
    
    return result


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
        # 获取条件详情（阈值信息）
        conditions_detail = await _get_dealer_conditions_detail(redis, address, tx_detail)
        
        # 广播庄家信息到浏览器控制台
        await ws_manager.broadcast(mint, {
            "type": "dealer_detected",
            "data": {
                "address": address,
                "sig": sig,
                "tx_type": tx_type,
                "amount": amount,
                "sol_spent": sol_spent,
                "conditions": conditions_detail,
            }
        })
        return {"is_dealer": True}
    
    # 从 Redis 读取当前持仓数据（统一用 {mint}_xxx key）
    holding_qty = float(state.get(f"{mint}_holdingQty", "0"))
    holding_cost = float(state.get(f"{mint}_holdingCost", "0"))
    avg_price = float(state.get(f"{mint}_avgPrice", "0"))
    total_buy_amount = float(state.get(f"{mint}_totalBuyAmount", "0"))
    total_sell_amount = float(state.get(f"{mint}_totalSellAmount", "0"))
    total_sell_principal = float(state.get(f"{mint}_totalSellPrincipal", "0"))
    
    old_holding_cost = holding_cost
    old_realized = total_sell_amount - total_sell_principal
    
    if tx_type == "BUY":
        buy_amount = abs(sol_spent)
        buy_qty = amount
        
        holding_qty += buy_qty
        holding_cost += buy_amount
        total_buy_amount += buy_amount
        if holding_qty > 0:
            avg_price = holding_cost / holding_qty
        
        logger.debug(f"[计算] {address[:8]}... BUY {buy_qty} @ {buy_amount} SOL")
    
    elif tx_type == "SELL":
        sell_qty = abs(amount)
        sell_amount = abs(sol_spent)
        
        # 卖出本金 = 卖出数量 × 当时均价
        sell_principal = sell_qty * avg_price
        
        total_sell_principal += sell_principal
        total_sell_amount += sell_amount
        holding_qty -= sell_qty
        holding_cost -= sell_principal
        
        # 防负值
        if holding_qty < 0.001:
            holding_qty = 0
            holding_cost = 0
            avg_price = 0
        
        # 更新均价
        if holding_qty > 0:
            avg_price = holding_cost / holding_qty
        
        realized = sell_amount - sell_principal
        logger.debug(f"[计算] {address[:8]}... SELL {sell_qty} @ {sell_amount} SOL, 落袋: {realized:.6f} SOL")
    
    # 直接保存状态（传字典而不是修改 state）
    await save_trader_state(redis, mint, address, {
        "status": state["status"],
        "conditions": state["conditions"],
        "holdingQty": holding_qty,
        "holdingCost": holding_cost,
        "avgPrice": avg_price,
        "totalBuyAmount": total_buy_amount,
        "totalSellAmount": total_sell_amount,
        "totalSellPrincipal": total_sell_principal,
    })
    
    # 更新全局指标
    new_holding_cost = holding_cost
    new_realized = total_sell_amount - total_sell_principal
    
    delta_bet = new_holding_cost - old_holding_cost
    delta_profit = new_realized - old_realized
    
    await update_metrics_delta(redis, mint, delta_bet, delta_profit)
    
    # 更新 Redis 中的分析结果
    await tx_redis.update_tx_analysis(sig, {
        "net_sol_flow": sol_spent,
        "net_token_flow": amount,
        "price_per_token": avg_price,
        "wallet_tag": "dealer" if state["status"] == "dealer" else "unknown",
        "processed_at": datetime.utcnow().isoformat(),
    })
    
    return {
        "holdingQty": holding_qty,
        "holdingCost": holding_cost,
        "avgPrice": avg_price,
        "is_dealer": state["status"] == "dealer"
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
    logger.info(f"[消费者] 开始消化队列 mint={mint}")

    while True:
        tx_detail = await _trade_queue.get()
        if tx_detail is None:  # 毒丸信号，结束
            logger.info(f"[消费者] 收到停止信号 mint={mint}")
            break

        sig = tx_detail.get("sig", "")
        if not sig:
            continue

        try:
            # 庄家判定 + 指数计算 + 更新 wallet_tag
            metrics = await _calculate_index(tx_detail, mint)

            # 非庄家交易 → WebSocket 广播
            if not metrics.get("is_dealer"):
                await ws_manager.broadcast(mint, {
                    "type": "trade",
                    "data": {
                        **tx_detail,
                        "wallet_tag": "dealer" if metrics.get("is_dealer") else "unknown",
                    }
                })

            logger.debug(f"[消费者] {sig[:8]}... 完成")

        except Exception as e:
            logger.error(f"[消费者] {sig[:8]}... 异常: {e}", exc_info=True)


async def run_full_calculation(db: Session, mint: str):
    """全量指数计算（从 Redis 获取交易数据）"""
    logger.info(f"[全量计算] 开始计算 mint={mint}")

    # 从 Redis 有序集合获取交易列表（先 rpc_fill，再 ws）
    # rpc_fill 需要倒序获取（从最新到最旧），因为 backfill 时最新交易先存入
    # 然后反转得到从旧到新的顺序，保证指数计算从最早的交易开始
    rpc_sigs = list(reversed(await tx_redis.get_tx_list(mint, "rpc_fill")))
    ws_sigs = await tx_redis.get_tx_list(mint, "ws")
    all_sigs = rpc_sigs + ws_sigs

    logger.info(f"[全量计算] 共 {len(all_sigs)} 条交易待处理 (rpc_fill: {len(rpc_sigs)}, ws: {len(ws_sigs)})")

    for sig in all_sigs:
        try:
            # 从 Redis 获取交易详情
            tx_detail = await tx_redis.get_tx(sig)
            if not tx_detail:
                continue

            # 指数计算（_calculate_index 内部更新 wallet_tag）
            metrics = await _calculate_index(tx_detail, mint)

            # 非庄家交易 → WebSocket 广播
            if not metrics.get("is_dealer"):
                await ws_manager.broadcast(mint, {
                    "type": "trade",
                    "data": {
                        **tx_detail,
                        "wallet_tag": "dealer" if metrics.get("is_dealer") else "unknown",
                    }
                })

        except Exception as e:
            logger.error(f"[全量计算] 处理 {sig[:8]}... 失败: {e}")

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

    # 4. 删除 Redis 中该 mint 的交易数据（txlist 和 tx:*）
    from app.services.dealer_detector import _redis
    if _redis:
        await _redis.delete(f"txlist:rpc_fill:{mint}")
        await _redis.delete(f"txlist:ws:{mint}")
        logger.info(f"[重置] 已删除 Redis 交易列表: txlist:rpc_fill:{mint}, txlist:ws:{mint}")

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
    
    # 从 Redis 获取交易数量（两个有序集合的总和）
    rpc_count = await redis.zcard(f"txlist:rpc_fill:{mint}")
    ws_count = await redis.zcard(f"txlist:ws:{mint}")
    trade_count = rpc_count + ws_count
    
    total_bet = metrics.get("total_bet", 0)
    realized_profit = metrics.get("realized_profit", 0)
    
    return {
        "current_bet": total_bet,
        "realized_profit": realized_profit,
        "current_cost": total_bet - realized_profit,
        "trade_count": trade_count
    }
