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


# 当前正在 backfill（全量计算）的 mint 集合
# backfill 期间 _calculate_index 不会向 WS 广播，避免前端 DOM 堆积导致 send_text 阻塞
_backfilling_mints: set = set()


def user_key(address: str) -> str:
    """获取用户 Redis Key"""
    return f"user:{address}"


async def _get_metrics_key(mint: str) -> str:
    """获取全局指标 Redis Key"""
    return f"metrics:{mint}"


async def get_trader_state(redis, mint: str, address: str) -> dict:
    """
    获取用户状态（合并两个 key）：
      user:{address}       — 全局信息（status/conditions/cluster_name/dealerExcluded）
      user:{mint}:{address} — per-mint 持仓数据
    """
    if not redis:
        return _default_trader_state()
    
    try:
        # 读取全局信息
        global_state = await redis.hgetall(user_key(address))
        
        # 读取 per-mint 持仓
        from app.services.cluster.redis_keys import user_mint_key
        mint_state = await redis.hgetall(user_mint_key(mint, address))
        
        if not global_state and not mint_state:
            return _default_trader_state()
        
        global_state = global_state or {}
        mint_state = mint_state or {}
        
        # 读取 status 和 conditions（来自全局）
        status = global_state.get("status", "unknown")
        try:
            conditions = json.loads(global_state.get("conditions", "[]"))
        except:
            conditions = []
        
        result = {
            "status": status,
            "conditions": conditions,
            "status_source": global_state.get("status_source", "system"),
            "cluster_name": global_state.get("cluster_name", ""),
            # per-mint 持仓数据
            f"{mint}_holdingQty": mint_state.get("holdingQty", "0"),
            f"{mint}_holdingCost": mint_state.get("holdingCost", "0"),
            f"{mint}_avgPrice": mint_state.get("avgPrice", "0"),
            f"{mint}_totalBuyAmount": mint_state.get("totalBuyAmount", "0"),
            f"{mint}_totalSellAmount": mint_state.get("totalSellAmount", "0"),
            f"{mint}_totalSellPrincipal": mint_state.get("totalSellPrincipal", "0"),
        }
        # dealerExcluded 标记（per-mint，存在全局 key 内）
        excluded = global_state.get(f"{mint}_dealerExcluded", "")
        if excluded:
            result[f"{mint}_dealerExcluded"] = excluded
        return result
    except Exception as e:
        logger.error(f"[交易员状态] 获取失败 address={address[:8]}...: {e}", exc_info=True)
        return _default_trader_state()


async def get_trader_state_with_sig(redis, mint: str, address: str, sig: str) -> dict:
    """
    获取用户状态（C002-C006 统一检测入口）
    
    返回结构：
        {
            "state": {...},           # 用户状态
            "broadcasts": [...],       # 待广播消息列表
            "cluster_info": {...}      # 簇组信息
        }
    
    处理流程：
        1. C002-C005 + C006 统一检测（通过 _check_local_dealer_conditions）
        2. 返回 { state, broadcasts, cluster_info }
    """
    from app.utils.database import SessionLocal
    from app.services.dealer_detector import _check_local_dealer_conditions
    
    if not redis:
        return {"state": _default_trader_state(), "broadcasts": [], "cluster_info": None, "new_cluster_broadcast": None}
    
    try:
        key = user_key(address)
        state = await redis.hgetall(key)
        broadcasts = []
        cluster_info = None
        new_cluster_broadcast = None
        
        if not state:
            state = _default_trader_state()
            # 新用户：尝试本地快速判断庄家（C002-C006）
            tx_detail = await tx_redis.get_tx(sig)
            if tx_detail:
                db = SessionLocal()
                try:
                    detected_status, conditions, cluster_info, new_cluster_broadcast = _check_local_dealer_conditions(tx_detail, state, db, mint)
                    state["conditions"] = conditions
                    state["status"] = detected_status
                    state["status_source"] = "system"
                    
                    if detected_status == "dealer":
                        await save_trader_state(redis, mint, address, state)
                        logger.info(f"[庄家判定] {address[:8]}... 新用户本地判断为庄家 (C002-C006)")
                        return {"state": state, "broadcasts": [], "cluster_info": cluster_info, "new_cluster_broadcast": new_cluster_broadcast}
                    
                    # retail 也要保存状态
                    await save_trader_state(redis, mint, address, state)
                    logger.info(f"[庄家判定] {address[:8]}... 新用户本地判断为 {detected_status} (C006)，入队等待 C001")
                finally:
                    db.close()
            # 本地判断不是庄家或无交易详情：入队等待 C001 检测
            await _enqueue_dealer_check(address, mint, sig)
        else:
            # 读取 status 和 conditions
            status = state.get("status", "unknown")
            status_source = state.get("status_source", "system")
            try:
                conditions = json.loads(state.get("conditions", "[]"))
            except:
                conditions = []
            state["status"] = status
            state["status_source"] = status_source
            state["conditions"] = conditions

            # 从 Redis 恢复 cluster_info（已存入的用户）
            stored_cluster_name = state.get("cluster_name", "")
            if stored_cluster_name:
                # 从簇组 Redis 读取最新 cluster_type（单一数据源）
                stored_cluster_type = "unknown"
                from app.services.cluster.redis_keys import get_cluster_sync
                latest = get_cluster_sync(stored_cluster_name)
                if latest:
                    stored_cluster_type = latest.cluster_type
                
                cluster_info = {
                    "address": address,
                    "sig": sig,
                    "cluster_name": stored_cluster_name,
                    "cluster_type": stored_cluster_type,
                }

                # 簇组类型变更 → 同步到用户状态（非手动修改才允许覆盖）
                if status_source != "manual" and stored_cluster_type != "unknown" and status != stored_cluster_type:
                    state["status"] = stored_cluster_type
                    logger.info(f"[簇组同步] {address[:8]}... 用户状态 {status} → {stored_cluster_type}（簇组自动同步）")
            
            # 如果是 unknown 且有 sig，自动入队检测
            # if status == "unknown":
            #     await _enqueue_dealer_check(address, mint, sig)
        
        return {"state": state, "broadcasts": broadcasts, "cluster_info": cluster_info, "new_cluster_broadcast": new_cluster_broadcast}
    except Exception as e:
        logger.error(f"[交易员状态] 获取失败 sig={sig[:8]}...: {e}", exc_info=True)
        return {"state": _default_trader_state(), "broadcasts": [], "cluster_info": None, "new_cluster_broadcast": None}


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
        "status_source": "system",
    }


async def save_trader_state(redis, mint: str, address: str, state: dict):
    """
    保存用户状态（拆分为两个 key）：
      user:{address}       — 全局信息（status/conditions/cluster_name/dealerExcluded）
      user:{mint}:{address} — per-mint 持仓数据
    """
    if not redis:
        return
    
    try:
        # 全局信息 → user:{address}
        global_data = {
            "status": state.get("status", "unknown"),
            "conditions": json.dumps(state.get("conditions", [])),
            "status_source": state.get("status_source", "system"),
            "cluster_name": state.get("cluster_name", ""),
        }
        # 庄家排除标记（per-mint）
        if state.get(f"{mint}_dealerExcluded"):
            global_data[f"{mint}_dealerExcluded"] = "true"
        await redis.hset(user_key(address), mapping=global_data)
        
        # per-mint 持仓 → user:{mint}:{address}
        from app.services.cluster.redis_keys import user_mint_key
        mint_data = {
            "holdingQty": str(state.get("holdingQty", 0)),
            "holdingCost": str(state.get("holdingCost", 0)),
            "avgPrice": str(state.get("avgPrice", 0)),
            "totalBuyAmount": str(state.get("totalBuyAmount", 0)),
            "totalSellAmount": str(state.get("totalSellAmount", 0)),
            "totalSellPrincipal": str(state.get("totalSellPrincipal", 0)),
        }
        await redis.hset(user_mint_key(mint, address), mapping=mint_data)
    except Exception as e:
        logger.error(f"[保存状态] 失败 address={address[:8]}...: {e}", exc_info=True)


async def get_metrics(redis, mint: str) -> dict:
    """获取全局指标"""
    if not redis:
        return {
            "total_bet": 0.0,
            "realized_profit": 0.0,
            "dealer_count": 0,
            "total_holdingQty": 0.0,
        }
    
    try:
        key = await _get_metrics_key(mint)
        metrics = await redis.hgetall(key)
        
        if not metrics:
            metrics = {
                "total_bet": 0.0,
                "realized_profit": 0.0,
                "dealer_count": 0,
                "total_holdingQty": 0.0,
            }
        else:
            for k in ["total_bet", "realized_profit", "dealer_count", "total_holdingQty"]:
                metrics[k] = float(metrics.get(k, "0"))
        
        return metrics
    except Exception as e:
        logger.error(f"[指标] 获取失败 mint={mint[:8]}...: {e}", exc_info=True)
        return {
            "total_bet": 0.0,
            "realized_profit": 0.0,
            "dealer_count": 0,
            "total_holdingQty": 0.0,
        }


async def update_metrics_delta(redis, mint: str, delta_bet: float, delta_profit: float):
    """更新全局指标增量"""
    if not redis:
        return
    
    try:
        key = await _get_metrics_key(mint)
        if delta_bet != 0:
            await redis.hincrbyfloat(key, "total_bet", delta_bet)
        if delta_profit != 0:
            await redis.hincrbyfloat(key, "realized_profit", delta_profit)
    except Exception as e:
        logger.error(f"[指标] 更新失败 mint={mint[:8]}...: {e}", exc_info=True)


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
    
    try:
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
            
            # C005: 交易程序类型判定
            if "C005" in conditions:
                trigger_programs = []
                for c in conditions:
                    if c.startswith("C005:"):
                        parts = c[5:].rsplit(":", 1)
                        if len(parts) == 2 and parts[1] == "retail":
                            trigger_programs.append(parts[0])
                        elif len(parts) == 1:
                            trigger_programs.append(parts[0])
                        else:
                            trigger_programs.append(c[5:])
                result["details"]["C005"] = {
                    "name": "交易程序类型判定",
                    "enabled": get_setting(db, "dealer_risk_enabled") == "true",
                    "programs": trigger_programs,
                    "is_retail": state.get("status") == "retail",
                }
        finally:
            db.close()
        
        return result
    except Exception as e:
        logger.error(f"[庄家条件] 获取失败 address={address[:8]}...: {e}", exc_info=True)
        return {"conditions": [], "details": {}}


# ──────────────────────────────────────────────────────────
# 指数计算核心
# ──────────────────────────────────────────────────────────

async def _calculate_index(
    tx_detail: Dict[str, Any],
    mint: str,
    is_backfill: bool = False,
) -> Dict[str, Any]:
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
    sig = tx_detail.get("sig", "")
    try:
        redis = await _get_redis()

        address = tx_detail.get("from_address", "")
        tx_type = tx_detail.get("transaction_type", "")
        sol_spent = tx_detail.get("sol_spent", 0) or 0.0
        amount = tx_detail.get("amount", 0) or 0.0

        if not address:
            return {}

        # ── C002-C005 + C006 庄家检测（统一入口） ──
        # 获取用户状态（内部包含 C002-C005 本地检测 + C006 簇组检测）
        result = await get_trader_state_with_sig(redis, mint, address, sig)
        state = result["state"]
        broadcasts = result.get("broadcasts", [])
        cluster_info = result.get("cluster_info")
        new_cluster_broadcast = result.get("new_cluster_broadcast")

        from app.services.trade_tracer import trace
        trader_status = state.get("status", "unknown")
        tracer_conditions = state.get("conditions", [])
        trace(mint, sig, "④ 庄家判定", f"结果={trader_status}, 条件={tracer_conditions}")
        
        is_dealer = (state.get("status") == "dealer")
        metrics_key = await _get_metrics_key(mint)
        
        # ========== 共用：持仓数据读取（从 user:{mint}:{address} 读取） ==========
        from app.services.cluster.redis_keys import user_mint_key
        mint_state = await redis.hgetall(user_mint_key(mint, address))
        if mint_state:
            holding_qty = float(mint_state.get("holdingQty", "0"))
            holding_cost = float(mint_state.get("holdingCost", "0"))
            avg_price = float(mint_state.get("avgPrice", "0"))
            total_buy_amount = float(mint_state.get("totalBuyAmount", "0"))
            total_sell_amount = float(mint_state.get("totalSellAmount", "0"))
            total_sell_principal = float(mint_state.get("totalSellPrincipal", "0"))
        else:
            holding_qty = holding_cost = avg_price = 0.0
            total_buy_amount = total_sell_amount = total_sell_principal = 0.0
        
        old_holding_cost = holding_cost
        old_realized = total_sell_amount - total_sell_principal

        # ── ①② 交易头 + 庄家判定 + 交易前持仓 ──
        short_addr = address[:8] + "..." if len(address) > 8 else address
        logger.info("=" * 54)
        logger.info(
            "📊 [指数计算] mint=%s sig=%s type=%s address=%s",
            mint[:8] + "...", sig, tx_type, short_addr,
        )
        logger.info("-" * 54)
        logger.info(
            "① 庄家判定: status=%s, is_dealer=%s, conditions=%s",
            trader_status, is_dealer, tracer_conditions,
        )
        logger.info(
            "② 交易前用户持仓: holdingQty=%.6f  holdingCost=%.6f  avgPrice=%.12f",
            holding_qty, holding_cost, avg_price,
        )
        logger.info(
            "   totalBuyAmount=%.6f  totalSellAmount=%.6f  totalSellPrincipal=%.6f",
            total_buy_amount, total_sell_amount, total_sell_principal,
        )
        logger.info(
            "   old_holding_cost=%.6f  old_realized=%.6f",
            old_holding_cost, old_realized,
        )
        
        # ========== 共用：BUY/SELL 计算 ==========
        if tx_type == "BUY":
            buy_amount = abs(sol_spent)
            buy_qty = amount
            
            old_qty = holding_qty
            old_cost = holding_cost
            old_avg = avg_price
            
            holding_qty += buy_qty
            holding_cost += buy_amount
            total_buy_amount += buy_amount
            if holding_qty > 0:
                avg_price = holding_cost / holding_qty
            
            logger.info(
                "③ 用户指数 [BUY]: buy_qty=%.6f  buy_amount=%.6f SOL",
                buy_qty, buy_amount,
            )
            logger.info(
                "   holdingQty:   %.6f → %.6f",
                old_qty, holding_qty,
            )
            logger.info(
                "   holdingCost:  %.6f → %.6f",
                old_cost, holding_cost,
            )
            logger.info(
                "   avgPrice:     %.12f → %.12f",
                old_avg, avg_price,
            )
        
        elif tx_type == "SELL":
            sell_qty = abs(amount)
            sell_amount = abs(sol_spent)
            
            sell_principal = sell_qty * avg_price
            
            old_qty = holding_qty
            old_cost = holding_cost
            old_avg = avg_price
            
            total_sell_principal += sell_principal
            total_sell_amount += sell_amount
            holding_qty -= sell_qty
            holding_cost -= sell_principal
            
            if holding_qty < 0.001:
                holding_qty = 0
                holding_cost = 0
                avg_price = 0
            
            if holding_qty > 0:
                avg_price = holding_cost / holding_qty
            
            realized = sell_amount - sell_principal
            logger.info(
                "③ 用户指数 [SELL]: sell_qty=%.6f  sell_amount=%.6f SOL  sell_principal=%.6f(=sell_qty×avgPrice)",
                sell_qty, sell_amount, sell_principal,
            )
            logger.info("   落袋收益(realized)=%.6f SOL", realized)
            logger.info(
                "   holdingQty:   %.6f → %.6f",
                old_qty, holding_qty,
            )
            logger.info(
                "   holdingCost:  %.6f → %.6f",
                old_cost, holding_cost,
            )
            logger.info(
                "   avgPrice:     %.12f → %.12f",
                old_avg, avg_price,
            )
        
        # ========== 共用：保存状态 ==========
        save_data = {
            "status": state["status"],
            "conditions": state["conditions"],
            "status_source": state.get("status_source", "system"),
            "cluster_name": cluster_info.get("cluster_name", "") if cluster_info else "",
            "holdingQty": holding_qty,
            "holdingCost": holding_cost,
            "avgPrice": avg_price,
            "totalBuyAmount": total_buy_amount,
            "totalSellAmount": total_sell_amount,
            "totalSellPrincipal": total_sell_principal,
        }
        if is_dealer:
            save_data[f"{mint}_dealerExcluded"] = "true"
        await save_trader_state(redis, mint, address, save_data)
        
        # C008: 更新全局总持仓数量
        old_total_holding_qty_before = float(
            await redis.hget(metrics_key, "total_holdingQty") or 0
        )
        if tx_type == "BUY":
            await redis.hincrbyfloat(metrics_key, "total_holdingQty", buy_qty)
        elif tx_type == "SELL":
            await redis.hincrbyfloat(metrics_key, "total_holdingQty", -sell_qty)
        new_total_holding_qty = float(
            await redis.hget(metrics_key, "total_holdingQty") or 0
        )
        logger.info(
            "⑥ 全局 total_holdingQty(含庄家): %.6f → %.6f",
            old_total_holding_qty_before, new_total_holding_qty,
        )
        
        # ========== 分支：散户更新指标 / 庄家首次排除 ==========
        new_holding_cost = holding_cost
        new_realized = total_sell_amount - total_sell_principal
        delta_bet = new_holding_cost - old_holding_cost
        delta_profit = new_realized - old_realized

        logger.info("-" * 54)
        logger.info(
            "④ delta: delta_bet=%.6f (=new_holding_cost %.6f - old %.6f)",
            delta_bet, new_holding_cost, old_holding_cost,
        )
        logger.info(
            "   delta_profit=%.6f (=new_realized %.6f - old %.6f)",
            delta_profit, new_realized, old_realized,
        )

        old_total_bet = float(await redis.hget(metrics_key, "total_bet") or 0)
        old_realized_profit = float(await redis.hget(metrics_key, "realized_profit") or 0)
        
        if not is_dealer:
            await update_metrics_delta(redis, mint, delta_bet, delta_profit)
            new_total_bet = old_total_bet + delta_bet
            new_realized_profit = old_realized_profit + delta_profit
            logger.info(
                "⑤ 全局散户指数 [更新]: total_bet %.6f → %.6f, realized_profit %.6f → %.6f",
                old_total_bet, new_total_bet, old_realized_profit, new_realized_profit,
            )
        else:
            dealer_excluded = state.get(f"{mint}_dealerExcluded", "")
            logger.info(
                "⑤ 全局散户指数 [跳过-庄家]: total_bet=%.6f(不变), realized_profit=%.6f(不变), dealerExcluded=%s",
                old_total_bet, old_realized_profit, dealer_excluded,
            )
            if dealer_excluded != "true":
                await exclude_dealer(mint, address)
                trace(mint, sig, "⑤ 庄家排除", f"address={address[:8]}..., 首次排除")
                logger.info("   ⚠ 首次排除庄家，执行 exclude_dealer()")
            else:
                logger.info("   已排除过庄家，跳过")
        
        # 更新 Redis 中的分析结果
        await tx_redis.update_tx_analysis(sig, {
            "net_sol_flow": sol_spent,
            "net_token_flow": amount,
            "price_per_token": avg_price,
            "wallet_tag": state["status"],
            "processed_at": datetime.utcnow().isoformat(),
        })

        # ── 广播 ──
        # 读取当前指标，随 trade 广播下发，前端无需额外 HTTP 请求
        current_total_bet = float(await redis.hget(metrics_key, "total_bet") or 0)
        current_realized_profit = float(await redis.hget(metrics_key, "realized_profit") or 0)
        rpc_count = await redis.zcard(f"txlist:rpc_fill:{mint}")
        ws_count = await redis.zcard(f"txlist:ws:{mint}")

        # backfill 期间不发广播（避免前端 DOM 堆积 + TCP backpressure 阻塞 send_text）
        if mint not in _backfilling_mints:
            await ws_manager.broadcast(mint, {
                "type": "trade",
                "data": {
                    **tx_detail,
                    "wallet_tag": state["status"],
                    "current_bet": current_total_bet,
                    "realized_profit": current_realized_profit,
                    "current_cost": current_total_bet - current_realized_profit,
                    "trade_count": rpc_count + ws_count,
                }
            })

            # 2. 再广播 cluster_matched（更新前端同一行的簇组标签）
            if cluster_info:
                await ws_manager.broadcast(mint, {
                    "type": "cluster_matched",
                    "data": cluster_info
                })

            # 3. 最后广播新簇组创建
            if new_cluster_broadcast:
                await ws_manager.broadcast(mint, new_cluster_broadcast)

            # 4. 广播 user_status 更新前端用户列表
            cluster_name = state.get("cluster_name", "")
            cluster_type = cluster_info.get("cluster_type", "unknown") if cluster_info else "unknown"
            cluster_tx_count = 0
            cluster_user_count = 0

            await ws_manager.broadcast(mint, {
                "type": "user_status",
                "data": {
                    "address": address,
                    "status": state["status"],
                    "status_source": state.get("status_source", "system"),
                    "conditions": state.get("conditions", []),
                    "cluster_name": cluster_name,
                    "cluster_type": cluster_type,
                    "cluster_tx_count": cluster_tx_count,
                    "cluster_user_count": cluster_user_count,
                    "holding_qty": holding_qty,
                    "holding_cost": holding_cost,
                    "total_buy_amount": total_buy_amount,
                    "total_sell_amount": total_sell_amount,
                    "total_sell_principal": total_sell_principal,
                }
            })

        # ── 策略入队（根据 ifNeedDealer 过滤庄家） ──
        from app.taskcl.consumer import enqueue_trade_for_strategy, get_strategy_params

        params = get_strategy_params()
        if_need_dealer = str(params.get("ifNeedDealer", "1"))
        is_dealer = state.get("status") == "dealer"

        if not is_dealer or if_need_dealer == "1":
            await enqueue_trade_for_strategy(tx_detail)
            trace(mint, sig, "⑥ 策略入队", f"ifNeedDealer={if_need_dealer}, is_dealer={is_dealer} → 已入队策略队列")
        else:
            trace(mint, sig, "⑥ 策略入队→跳过", f"ifNeedDealer={ifNeedDealer}, is_dealer={is_dealer} → 庄家不入队")

        return {
            "holdingQty": holding_qty,
            "holdingCost": holding_cost,
            "avgPrice": avg_price,
            "status": state["status"],
            "cluster_info": cluster_info
        }
    except Exception as e:
        logger.error(f"[计算] 指数计算异常 sig={sig[:8]}...: {e}", exc_info=True)
        return {}


async def exclude_dealer(mint: str, address: str):
    """排除庄家：从汇总中减去该用户贡献（读取 per-mint 持仓数据）"""
    redis = await _get_redis()
    
    if not redis:
        return
    
    try:
        state = await get_trader_state(redis, mint, address)
        
        # 从汇总中减去持仓成本
        await update_metrics_delta(redis, mint, -state.get("holdingCost", 0), 0)
        
        # 更新庄家计数
        metrics_key = await _get_metrics_key(mint)
        await redis.hincrbyfloat(metrics_key, "dealer_count", 1)
        
        logger.info(f"[庄家排除] {address[:8]}... 已从汇总中排除")
    except Exception as e:
        logger.error(f"[庄家排除] 失败 address={address[:8]}...: {e}", exc_info=True)


async def include_retail(mint: str, address: str):
    """恢复散户：将庄家数据重新加入汇总（手动改回散户时调用）"""
    redis = await _get_redis()
    
    if not redis:
        return
    
    try:
        state = await get_trader_state(redis, mint, address)
        
        # 将持仓成本加回汇总
        await update_metrics_delta(redis, mint, state.get("holdingCost", 0), 0)
        
        # 减少庄家计数
        metrics_key = await _get_metrics_key(mint)
        dealer_count = await redis.hget(metrics_key, "dealer_count")
        if dealer_count and int(dealer_count) > 0:
            await redis.hincrbyfloat(metrics_key, "dealer_count", -1)
        
        logger.info(f"[散户恢复] {address[:8]}... 已重新加入汇总")
    except Exception as e:
        logger.error(f"[散户恢复] 失败 address={address[:8]}...: {e}", exc_info=True)


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
    global _trade_queue
    logger.info(f"[消费者] 开始消化队列 mint={mint}")

    while True:
        try:
            # # 确保队列已初始化
            # if _trade_queue is None:
            #     _trade_queue = asyncio.Queue()
            
            queue = _trade_queue
            if queue is None:
                logger.error(f"[消费者] 队列未初始化，退出")
                return
            
            tx_detail = await _trade_queue.get()
            if tx_detail is None:  # 毒丸信号，结束
                logger.info(f"[消费者] 收到停止信号 mint={mint}")
                break

            sig = tx_detail.get("sig", "")
            if not sig:
                continue

            try:
                # 庄家判定 + 指数计算 + 统一广播（内部完成）
                await _calculate_index(tx_detail, mint)

                logger.debug(f"[消费者] {sig[:8]}... 完成")

            except Exception as e:
                logger.error(f"[消费者] {sig[:8]}... 异常: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"[消费者] 队列获取异常: {e}", exc_info=True)


async def run_full_calculation(db: Session, mint: str):
    """全量指数计算（从 Redis 获取交易数据）"""
    logger.info(f"[全量计算] 开始计算 mint={mint}")

    # backfill 期间不发 WS 广播（避免前端 DOM 堆积 + TCP backpressure 阻塞 send_text）
    _backfilling_mints.add(mint)

    # 推送 backfill 启动状态（前端显示）
    try:
        await ws_manager.broadcast(mint, {"type": "backfill_start", "data": {"mint": mint}})
    except Exception:
        pass

    try:
        # 从 Redis 有序集合获取交易列表（先 rpc_fill，再 ws）
        # rpc_fill 需要倒序获取（从最新到最旧），因为 backfill 时最新交易先存入
        # 然后反转得到从旧到新的顺序，保证指数计算从最早的交易开始
        rpc_sigs = list(reversed(await tx_redis.get_tx_list(mint, "rpc_fill")))
        ws_sigs = await tx_redis.get_tx_list(mint, "ws")
        all_sigs = rpc_sigs + ws_sigs
        total = len(all_sigs)

        logger.info(f"[全量计算] 共 {total} 条交易待处理 (rpc_fill: {len(rpc_sigs)}, ws: {len(ws_sigs)})")

        # 推送总条数
        try:
            await ws_manager.broadcast(mint, {"type": "backfill_progress", "data": {"count": 0, "total": total}})
        except Exception:
            pass

        _last_progress = 0
        for idx, sig in enumerate(all_sigs, start=1):
            try:
                # 从 Redis 获取交易详情
                tx_detail = await tx_redis.get_tx(sig)
                if not tx_detail:
                    continue

                # 指数计算 + 统一广播（内部完成）
                # backfill 交易：标记 is_backfill=True，不发 WS 广播（避免前端 DOM 堆积）
                await _calculate_index(tx_detail, mint, is_backfill=True)

                # 每 50 条或末尾推一次进度
                if idx - _last_progress >= 50 or idx == total:
                    try:
                        await ws_manager.broadcast(mint, {"type": "backfill_progress", "data": {"count": idx, "total": total}})
                    except Exception:
                        pass
                    _last_progress = idx

            except Exception as e:
                logger.error(f"[全量计算] 处理 {sig[:8]}... 失败: {e}")
        abc=2

        logger.info(f"[全量计算] 完成 mint={mint}")

        # 推送 backfill 完成
        try:
            await ws_manager.broadcast(mint, {"type": "backfill_done", "data": {"count": total, "mint": mint}})
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[全量计算] 异常 mint={mint[:8]}...: {e}", exc_info=True)
    finally:
        _backfilling_mints.discard(mint)


async def reset_processor(mint: str, db: Session):
    """关闭按钮触发：清理指定 mint 的状态"""
    global _trade_queue, _consumer_task, _mint

    logger.info(f"[重置] 开始清理 mint={mint}")

    try:
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

        # 3. 清理 Redis 指标数据（不清理用户数据）—— 测试期间保留
        # await clear_mint_redis(mint)

        # 4. 删除 Redis 中该 mint 的交易数据（txlist 和 tx:*）—— 测试期间保留
        # from app.services.dealer_detector import _redis
        # if _redis:
        #     await _redis.delete(f"txlist:rpc_fill:{mint}")
        #     await _redis.delete(f"txlist:ws:{mint}")
        #     logger.info(f"[重置] 已删除 Redis 交易列表: txlist:rpc_fill:{mint}, txlist:ws:{mint}")

        # 5. 重置全局变量
        _trade_queue = asyncio.Queue()
        _consumer_task = None
        _mint = ""

        # 6. 清理 C007 dev 缓存
        from app.services.dealer_detector import _c007_dev_cache
        _c007_dev_cache.pop(mint, None)

        logger.info(f"[重置] 清理完成 mint={mint}")
    except Exception as e:
        logger.error(f"[重置] 清理异常 mint={mint[:8]}...: {e}", exc_info=True)


async def calculate_metrics(db: Session, mint: str) -> Dict[str, float]:
    """计算四个核心指标"""
    redis = await _get_redis()
    
    if not redis:
        return {"current_bet": 0, "current_cost": 0, "realized_profit": 0, "trade_count": 0}

    try:
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
    except Exception as e:
        logger.error(f"[指标计算] 失败 mint={mint[:8]}...: {e}", exc_info=True)
        return {"current_bet": 0, "current_cost": 0, "realized_profit": 0, "trade_count": 0}