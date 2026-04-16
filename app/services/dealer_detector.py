"""庄家判定服务

职责：
- 维护 Redis 中的用户状态（retail / dealer / unknown）
- 管理检测任务队列（最多 20 并发）
- 异步执行庄家判定逻辑（当前支持条件 C001，后续可扩展）

Redis 数据结构：
  user:{address}  →  Hash
      status:     "retail" | "dealer" | "unknown"
      conditions: JSON list，如 '["C001"]'
"""
import asyncio
import json
import logging
from typing import Optional

import httpx
import redis.asyncio as aioredis

from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

REDIS_URL = "redis://localhost:6379"
HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"

# 模块级变量
_redis: Optional[aioredis.Redis] = None
_dealer_check_queue: Optional[asyncio.Queue] = None
_dealer_semaphore: Optional[asyncio.Semaphore] = None
_dealer_consumer_task: Optional[asyncio.Task] = None


async def init_dealer_detector():
    """初始化：Redis 连接 + 队列 + 信号量 + 启动消费者
    在 app startup 时调用一次。
    """
    global _redis, _dealer_check_queue, _dealer_semaphore, _dealer_consumer_task

    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    await _redis.ping()
    logger.info("[庄家判定] Redis 连接成功")

    _dealer_check_queue = asyncio.Queue()
    _dealer_semaphore = asyncio.Semaphore(1)
    _dealer_consumer_task = asyncio.create_task(_dealer_queue_consumer())
    logger.info("[庄家判定] 检测队列消费者已启动")


async def close_dealer_detector():
    """关闭：停止消费者任务，关闭 Redis 连接
    在 app shutdown 时调用。
    """
    global _dealer_consumer_task, _redis

    if _dealer_consumer_task and not _dealer_consumer_task.done():
        await _dealer_check_queue.put(None)  # 毒丸信号
        try:
            await asyncio.wait_for(_dealer_consumer_task, timeout=3.0)
        except asyncio.TimeoutError:
            _dealer_consumer_task.cancel()
    if _redis:
        await _redis.aclose()
    logger.info("[庄家判定] 已关闭")


# ──────────────────────────────────────────────────────────
# Redis 辅助
# ──────────────────────────────────────────────────────────

def _user_key(address: str) -> str:
    return f"user:{address}"


async def get_user_status(address: str) -> Optional[str]:
    """从 Redis 读取用户状态，不存在返回 None"""
    if not _redis:
        return None
    return await _redis.hget(_user_key(address), "status")


async def _set_user_status(address: str, status: str, conditions: list = None):
    """写入用户状态到 Redis"""
    if not _redis:
        return
    data = {"status": status, "conditions": json.dumps(conditions or [])}
    await _redis.hset(_user_key(address), mapping=data)


async def _get_user_conditions(address: str) -> list:
    """读取用户已满足的条件列表"""
    if not _redis:
        return []
    raw = await _redis.hget(_user_key(address), "conditions")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


# ──────────────────────────────────────────────────────────
# 对外接口：供 _calculate_index 调用
# ──────────────────────────────────────────────────────────

async def check_and_classify_user(address: str, mint: str, sig: str) -> str:
    """查询并返回用户当前状态，触发异步检测（如需要）

    返回值："retail" | "dealer" | "unknown"
    """
    if not address:
        return "unknown"

    status = await get_user_status(address)

    if status in ("retail", "dealer"):
        # 状态已确定，直接复用
        return status

    if status == "unknown":
        # 重新入队检测（可能上次检测未满足任何条件）
        await _dealer_check_queue.put((address, mint, sig))
        return "unknown"

    # 首次见到此地址：新建记录，入队检测
    await _set_user_status(address, "unknown")
    await _dealer_check_queue.put((address, mint, sig))
    return "unknown"


# ──────────────────────────────────────────────────────────
# 队列消费者
# ──────────────────────────────────────────────────────────

async def _dealer_queue_consumer():
    """常驻消费者：从队列取地址，控制并发 ≤ 20 个检测任务"""
    logger.info("[庄家判定] 队列消费者运行中")
    while True:
        item = await _dealer_check_queue.get()
        if item is None:  # 毒丸信号
            break

        address, mint, sig = item

        # 再次检查状态，避免短时间内重复检测
        current = await get_user_status(address)
        if current in ("retail", "dealer"):
            continue


        await _run_dealer_check(address, mint, sig)
        # 获取信号量后 create_task，不等待
        # await _dealer_semaphore.acquire()
        # asyncio.create_task(_run_dealer_check(address, mint, sig))


# ──────────────────────────────────────────────────────────
# 庄家判定核心
# ──────────────────────────────────────────────────────────

async def _run_dealer_check(address: str, mint: str, sig: str):
    """异步执行庄家判定，完成后通过 WS 广播结果"""
    try:
        api_key = await _get_helius_api_key()
        if not api_key:
            logger.warning("[庄家判定] 未配置 Helius API Key，跳过判定")
            return

        # 获取钱包最旧一笔交易（sortOrder asc, limit 1）
        first_tx = await _fetch_first_transaction(address, api_key)
        if not first_tx:
            logger.debug(f"[庄家判定] {address[:8]}... 无历史交易")
            return

        conditions = await _get_user_conditions(address)
        is_dealer = False

        # ── 条件 C001：首笔交易包含 closeAccount 且交易后 SOL 余额为 0 ──
        if "C001" not in conditions:
            if _check_c001(first_tx, address):
                conditions.append("C001")
                is_dealer = True
                logger.info(f"[庄家判定] {address[:8]}... 满足 C001")

        # 后续条件在此追加（C002, C003 ...）

        if is_dealer:
            await _set_user_status(address, "dealer", conditions)
            # WS 广播：前端按 sig 匹配条目更新用户状态
            await ws_manager.broadcast(mint, {
                "type": "user_status",
                "data": {
                    "address": address,
                    "status": "dealer",
                    "conditions": conditions,
                    "sig": sig,
                }
            })
            logger.info(f"[庄家判定] {address[:8]}... 判定为庄家，已广播")
        else:
            # 更新条件列表（即使没新条件，保持 unknown）
            await _set_user_status(address, "unknown", conditions)

    except Exception as e:
        logger.error(f"[庄家判定] {address[:8]}... 判定异常: {e}", exc_info=True)
    finally:
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
    """获取钱包最旧一笔成功交易"""
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
    try:
        logger.info(f"[庄家判定] 发送请求到 {HELIUS_RPC_URL}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{HELIUS_RPC_URL}/?api-key={api_key[:10]}...",
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

    except Exception as e:
        logger.warning(f"[庄家判定] 请求 Helius 失败: {e}")
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
