"""庄家判定服务

职责：
- 维护 Redis 中的用户状态（retail / dealer / unknown）
- 管理检测任务队列
- 异步执行庄家判定逻辑（支持条件 C001-C005）

Redis 数据结构（统一使用 user:{address}）：
  user:{address}
    status: "retail" | "dealer" | "unknown"
    conditions: '["C001"]'
    {mint}_holdingQty: "1000"
    {mint}_holdingCost: "5.5"
    {mint}_avgPrice: "0.0055"
    ...

庄家检测条件：
  C001: 首笔交易 closeAccount（Helius API，最后检查）
  C002: uses_lookup_table == True（ALT 条件）
  C003: fee < 阈值
  C004: cu_consumed 在范围内
  C005: risk_score > 阈值
"""
import asyncio
import json
import logging
from typing import Optional

import httpx
import redis.asyncio as aioredis

from app.services import tx_redis
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

REDIS_URL = "redis://localhost:6379"
HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"

# 模块级变量
_redis: Optional[aioredis.Redis] = None
_dealer_check_queue: Optional[asyncio.Queue] = None
_dealer_semaphore: Optional[asyncio.Semaphore] = None
_dealer_consumer_task: Optional[asyncio.Task] = None
_retry_queue: Optional[asyncio.Queue] = None
_retry_consumer_task: Optional[asyncio.Task] = None


async def init_dealer_detector():
    """初始化：Redis 连接 + 队列 + 信号量"""
    global _redis, _dealer_check_queue, _dealer_semaphore, _retry_queue

    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    await _redis.ping()
    logger.info("[庄家判定] Redis 连接成功")

    _dealer_check_queue = asyncio.Queue()
    _dealer_semaphore = asyncio.Semaphore(1)
    _retry_queue = asyncio.Queue()
    logger.info("[庄家判定] 队列已就绪，等待首次触发")


def start_dealer_consumer():
    """启动庄家检测消费者（在首次回填完成时调用）"""
    global _dealer_consumer_task
    if _dealer_consumer_task is None or _dealer_consumer_task.done():
        _dealer_consumer_task = asyncio.create_task(_dealer_queue_consumer())
        logger.info("[庄家判定] 检测队列消费者已启动")
    
    start_retry_consumer()


async def close_dealer_detector():
    """关闭：停止消费者任务，关闭 Redis 连接"""
    global _dealer_consumer_task, _retry_consumer_task, _redis

    if _dealer_consumer_task and not _dealer_consumer_task.done():
        await _dealer_check_queue.put(None)
        try:
            await asyncio.wait_for(_dealer_consumer_task, timeout=3.0)
        except asyncio.TimeoutError:
            _dealer_consumer_task.cancel()
    
    if _retry_consumer_task and not _retry_consumer_task.done():
        await _retry_queue.put(None)
        try:
            await asyncio.wait_for(_retry_consumer_task, timeout=3.0)
        except asyncio.TimeoutError:
            _retry_consumer_task.cancel()
    
    if _redis:
        await _redis.aclose()
    logger.info("[庄家判定] 已关闭")


# ──────────────────────────────────────────────────────────
# 队列消费者
# ──────────────────────────────────────────────────────────

async def _dealer_queue_consumer():
    """常驻消费者：从队列取地址，执行检测"""
    logger.info("[庄家判定] 队列消费者运行中")
    while True:
        item = await _dealer_check_queue.get()
        if item is None:  # 毒丸信号
            break

        address, mint, sig = item

        # 再次检查状态，避免短时间内重复检测
        from app.services.trade_processor import get_trader_state, _get_redis as tp_get_redis
        redis = await tp_get_redis()
        state = await get_trader_state(redis, mint, address, sig)
        if state.get("status") in ("retail", "dealer"):
            continue

        await _run_dealer_check(address, mint, sig)


# ──────────────────────────────────────────────────────────
# 重试队列处理
# ──────────────────────────────────────────────────────────

async def _handle_detection_failed(redis, address, mint, sig, current_count, reason):
    """处理检测失败：入重试队列或放弃"""
    retry_key = f"retry:{address}:{mint}"
    new_count = current_count + 1
    
    if new_count >= 3:
        # 重试 3 次都失败，放弃
        logger.warning(f"[庄家判定] {address[:8]}... 重试 {new_count} 次失败 ({reason})，放弃")
        
        # 广播给前端
        await ws_manager.broadcast(mint, {
            "type": "user_status",
            "data": {
                "address": address,
                "status": "unknown",
                "reason": "detection_failed",
                "sig": sig,
            }
        })
        
        # 清理重试计数
        await redis.delete(retry_key)
    else:
        # 入重试队列，等待 2s/4s/8s 后再试
        await redis.set(retry_key, str(new_count))
        wait_time = 2 ** new_count
        logger.info(f"[庄家判定] {address[:8]}... 第 {new_count} 次失败，{wait_time}s 后重试")
        await _retry_queue.put((address, mint, sig, wait_time))


async def _retry_queue_consumer():
    """重试队列消费者：只有 dealer_check_queue 为空时才处理"""
    logger.info("[重试] 消费者运行中")
    
    while True:
        item = await _retry_queue.get()
        if item is None:
            break
        
        address, mint, sig, wait_time = item
        
        await asyncio.sleep(wait_time)
        
        if not _dealer_check_queue.empty():
            logger.debug(f"[重试] 主队列非空，{address[:8]}... 放回队列等待")
            await _retry_queue.put((address, mint, sig, wait_time))
            continue
        
        await _run_dealer_check(address, mint, sig)


def start_retry_consumer():
    """启动重试消费者"""
    global _retry_consumer_task
    if _retry_consumer_task is None or _retry_consumer_task.done():
        _retry_consumer_task = asyncio.create_task(_retry_queue_consumer())
        logger.info("[重试] 消费者已启动")


# ──────────────────────────────────────────────────────────
# 庄家判定核心
# ──────────────────────────────────────────────────────────

async def _run_dealer_check(address: str, mint: str, sig: str):
    """
    异步执行庄家判定，完成后：
    - 庄家：修改 status=dealer，排除计算，WS 广播隐藏
    - 散户：修改 status=retail，WS 广播标记
    
    执行顺序：
    1. C002: ALT 条件（uses_lookup_table）
    2. C003: Gas 费条件
    3. C004: CU 条件
    4. C005: 风险分条件
    5. C001: 首笔交易 closeAccount（最后，Helius API 耗时长）
    """
    from app.services.trade_processor import get_trader_state, save_trader_state, exclude_dealer, _get_redis as tp_get_redis
    from app.utils.database import SessionLocal
    from app.services.settings_service import get_setting, get_float_setting, get_int_setting

    redis = await tp_get_redis()
    retry_key = f"retry:{address}:{mint}"
    retry_count = await redis.get(retry_key)
    retry_count = int(retry_count) if retry_count else 0
    
    try:
        state = await get_trader_state(redis, mint, address)
        conditions = state.get("conditions", [])
        
        # 获取交易详情（从 Redis tx:{sig}）
        tx_detail = await tx_redis.get_tx(sig)
        
        is_dealer = False
        
        # ── 条件 C002：使用 ALT（地址查找表） ──
        if "C002" not in conditions and tx_detail:
            db = SessionLocal()
            try:
                alt_enabled = get_setting(db, "dealer_alt_enabled")
                if alt_enabled == "true" and tx_detail.get("uses_lookup_table"):
                    conditions.append("C002")
                    is_dealer = True
                    logger.info(f"[庄家判定] {address[:8]}... 满足 C002 (ALT)")
            finally:
                db.close()
        
        # ── 条件 C003：Gas 费小于阈值 ──
        if "C003" not in conditions and tx_detail:
            db = SessionLocal()
            try:
                gas_enabled = get_setting(db, "dealer_gas_enabled")
                if gas_enabled == "true":
                    gas_max = get_float_setting(db, "dealer_gas_max", 0.00001)
                    fee = tx_detail.get("fee", 0)
                    if fee < gas_max:
                        conditions.append("C003")
                        is_dealer = True
                        logger.info(f"[庄家判定] {address[:8]}... 满足 C003 (Gas: {fee} < {gas_max})")
            finally:
                db.close()
        
        # ── 条件 C004：CU 在范围内 ──
        if "C004" not in conditions and tx_detail:
            db = SessionLocal()
            try:
                cu_enabled = get_setting(db, "dealer_cu_enabled")
                if cu_enabled == "true":
                    cu_min = get_int_setting(db, "dealer_cu_min", 0)
                    cu_max = get_int_setting(db, "dealer_cu_max", 200000)
                    cu_consumed = tx_detail.get("cu_consumed", 0)
                    if cu_min <= cu_consumed <= cu_max:
                        conditions.append("C004")
                        is_dealer = True
                        logger.info(f"[庄家判定] {address[:8]}... 满足 C004 (CU: {cu_min} <= {cu_consumed} <= {cu_max})")
            finally:
                db.close()
        
        # ── 条件 C005：风险分大于阈值 ──
        if "C005" not in conditions and tx_detail:
            db = SessionLocal()
            try:
                risk_enabled = get_setting(db, "dealer_risk_enabled")
                if risk_enabled == "true":
                    risk_min = get_int_setting(db, "dealer_risk_min", 0)
                    risk_score = tx_detail.get("risk_score", 0)
                    if risk_score > risk_min:
                        conditions.append("C005")
                        is_dealer = True
                        logger.info(f"[庄家判定] {address[:8]}... 满足 C005 (Risk: {risk_score} > {risk_min})")
            finally:
                db.close()
        
        # 如果前面条件已判定为庄家，直接跳过 C001
        if is_dealer:
            # 修改用户状态为 dealer
            state["status"] = "dealer"
            state["conditions"] = conditions
            await save_trader_state(redis, mint, address, state)
            
            # 排除庄家：从汇总中减去该用户贡献
            await exclude_dealer(mint, address)
            
            # WS 广播：前端隐藏该用户的交易
            await ws_manager.broadcast(mint, {
                "type": "user_status",
                "data": {
                    "address": address,
                    "status": "dealer",
                    "conditions": conditions,
                    "sig": sig,
                }
            })
            logger.info(f"[庄家判定] {address[:8]}... 判定为庄家，已排除并广播")
            return
        
        # ── 条件 C001：首笔交易包含 closeAccount（Helius API，最后检查） ──
        c001_enabled = get_setting(SessionLocal(), "dealer_c001_enabled")
        if c001_enabled == "true":
            api_key = await _get_helius_api_key()
            if not api_key:
                await _handle_detection_failed(redis, address, mint, sig, retry_count, "API Key 未配置")
                return

            # 获取钱包最旧一笔交易（sortOrder asc, limit 1）
            first_tx = await _fetch_first_transaction(address, api_key)
            if not first_tx:
                await _handle_detection_failed(redis, address, mint, sig, retry_count, "无历史交易")
                return

            if "C001" not in conditions:
                if _check_c001(first_tx, address):
                    conditions.append("C001")
                    is_dealer = True
                    logger.info(f"[庄家判定] {address[:8]}... 满足 C001")

        if is_dealer:
            # 修改用户状态为 dealer
            state["status"] = "dealer"
            state["conditions"] = conditions
            await save_trader_state(redis, mint, address, state)
            
            # 排除庄家：从汇总中减去该用户贡献
            await exclude_dealer(mint, address)
            
            # WS 广播：前端隐藏该用户的交易
            await ws_manager.broadcast(mint, {
                "type": "user_status",
                "data": {
                    "address": address,
                    "status": "dealer",
                    "conditions": conditions,
                    "sig": sig,
                }
            })
            logger.info(f"[庄家判定] {address[:8]}... 判定为庄家，已排除并广播")
        else:
            # 修改用户状态为 retail（已知非庄家）
            state["status"] = "retail"
            state["conditions"] = conditions
            await save_trader_state(redis, mint, address, state)
            
            # WS 广播：前端标记该用户为散户
            await ws_manager.broadcast(mint, {
                "type": "user_status",
                "data": {
                    "address": address,
                    "status": "retail",
                    "conditions": conditions,
                    "sig": sig,
                }
            })
            logger.info(f"[庄家判定] {address[:8]}... 判定为散户，已广播")

    except Exception as e:
        logger.error(f"[庄家判定] {address[:8]}... 判定异常: {e}", exc_info=True)
        await _handle_detection_failed(redis, address, mint, sig, retry_count, str(e))
    finally:
        if _dealer_semaphore:
            _dealer_semaphore.release()


def _check_c001(tx_data: dict, address: str) -> bool:
    """条件 C001：首笔交易包含 closeAccount 指令 且 交易后该地址 SOL 余额为 0

    tx_data 为 Helius getTransactionsForAddress 返回的单笔交易 dict。
    """
    try:
        meta = tx_data.get("meta", {})
        if meta.get("err"):
            return False

        # 检测 closeAccount 指令
        has_close = False
        for ix_group in meta.get("innerInstructions", []):
            for ix in ix_group.get("instructions", []):
                ix_type = ix.get("parsed", {}).get("type", "")
                if ix_type in ("closeAccount", "CloseAccount"):
                    has_close = True
                    break
            if has_close:
                break

        if not has_close:
            return False

        # 检测交易后该地址 SOL 余额是否为 0
        inner_tx = tx_data.get("transaction", {})
        message = inner_tx.get("message", {})
        account_keys = message.get("accountKeys", [])

        # 找 address 在 accountKeys 中的 index
        addr_index = None
        for i, key in enumerate(account_keys):
            pubkey = key.get("pubkey", "") if isinstance(key, dict) else key
            if pubkey == address:
                addr_index = i
                break

        if addr_index is None:
            return False

        post_balances = meta.get("postBalances", [])
        if addr_index < len(post_balances):
            return post_balances[addr_index] == 0

    except Exception as e:
        logger.warning(f"[庄家判定] C001 检测异常: {e}")

    return False


# ──────────────────────────────────────────────────────────
# Helius 请求
# ──────────────────────────────────────────────────────────

async def _fetch_first_transaction(address: str, api_key: str) -> Optional[dict]:
    """获取钱包最旧一笔成功交易

    优化：
    - 超时调整为 20s（回填批次是 60s，dealer 请求更轻量应更快响应）
    - 最多重试 3 次，指数退避（2s → 4s → 8s），避免在回填高峰期一次超时就放弃
    """
    logger.info(f"[庄家判定] >>> _fetch_first_transaction 开始, address={address[:8]}...")
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            address,
            {
                "transactionDetails": "full",
                "sortOrder": "asc",
                "limit": 1,
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "filters": {"status": "succeeded"},
            },
        ],
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"[庄家判定] 发送请求到 {HELIUS_RPC_URL}（第 {attempt + 1} 次）")
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    f"{HELIUS_RPC_URL}/?api-key={api_key}",
                    json=body,
                )
            logger.info(f"[庄家判定] 收到响应 status={resp.status_code}")
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.warning(f"[庄家判定] Helius 错误: {data['error']}")
                return None

            txs = data.get("result", {}).get("data", [])
            return txs[0] if txs else None

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[庄家判定] HTTP 状态码异常（第 {attempt + 1} 次）: "
                f"{e.response.status_code}, 错误详情: {e.response.text}"
            )
            # 4xx 客户端错误不重试
            if e.response.status_code < 500:
                return None
        except (httpx.TimeoutException, httpx.RequestError) as e:
            logger.warning(
                f"[庄家判定] 网络请求异常（第 {attempt + 1} 次）: {type(e).__name__} -> {e}"
            )

        # 指数退避后重试：2s, 4s, 8s
        if attempt < max_retries - 1:
            wait = 2 ** (attempt + 1)
            logger.info(f"[庄家判定] {wait}s 后重试...")
            await asyncio.sleep(wait)

    logger.error(f"[庄家判定] {address[:8]}... 重试 {max_retries} 次后仍失败，放弃")
    return None


async def _get_helius_api_key() -> str:
    """从 SQLite settings 表读取 Helius API Key（独立 session，用完关闭）"""
    try:
        from app.utils.database import SessionLocal
        from app.services.settings_service import get_setting
        db = SessionLocal()
        try:
            return get_setting(db, "helius_api_key") or ""
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[庄家判定] 读取 API Key 失败: {e}")
        return ""
