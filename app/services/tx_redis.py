"""交易数据 Redis 服务

职责：
- 管理交易数据的 Redis 存储
- 维护 rpc_fill 和 ws 两套有序集合的排序

Redis 数据结构：
  txlist:rpc_fill:{mint}    # 有序集合，score=序号, member=sig
  txlist:ws:{mint}         # 有序集合，score=序号, member=sig
  tx:{sig}                 # Hash，存储完整交易信息
"""
import json
import logging
from typing import Optional, List, Dict, Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = "redis://localhost:6379"

# 模块级变量
_redis: Optional[aioredis.Redis] = None


async def init_tx_redis():
    """初始化 Redis 连接"""
    global _redis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    await _redis.ping()
    logger.info("[tx_redis] Redis 连接成功")


async def close_tx_redis():
    """关闭 Redis 连接"""
    global _redis
    if _redis:
        await _redis.aclose()
    logger.info("[tx_redis] Redis 已关闭")


async def _get_redis() -> aioredis.Redis:
    """获取 Redis 连接"""
    global _redis
    if _redis is None:
        await init_tx_redis()
    return _redis


# ──────────────────────────────────────────────────────────
# 有序集合操作
# ──────────────────────────────────────────────────────────

def _txlist_key(mint: str, source: str) -> str:
    """获取有序集合的 key"""
    return f"txlist:{source}:{mint}"


async def add_tx_to_list(mint: str, sig: str, source: str, score: int = None) -> int:
    """
    添加 sig 到有序集合
    
    Args:
        mint: 代币地址
        sig: 交易签名
        source: 数据来源 ("rpc_fill" 或 "ws")
        score: 可选，指定序号。如果不提供则自动递增
    
    Returns:
        使用的 score 值
    """
    redis = await _get_redis()
    key = _txlist_key(mint, source)
    
    if score is None:
        # 自动生成序号
        counter_key = f"txlist:{source}:{mint}:counter"
        score = await redis.incr(counter_key)
    
    # 添加到有序集合
    await redis.zadd(key, {sig: score})
    
    logger.debug(f"[tx_redis] 添加 sig 到 {key}, score={score}")
    return score


async def get_tx_list(mint: str, source: str, start: int = 0, end: int = -1) -> List[str]:
    """
    获取有序集合中的 sig 列表
    
    Args:
        mint: 代币地址
        source: 数据来源 ("rpc_fill" 或 "ws")
        start: 起始索引
        end: 结束索引 (-1 表示全部)
    
    Returns:
        sig 列表（按 score 从小到大排序）
    """
    redis = await _get_redis()
    key = _txlist_key(mint, source)
    
    sigs = await redis.zrange(key, start, end)
    return sigs


async def get_tx_count(mint: str, source: str) -> int:
    """获取有序集合中的 sig 数量"""
    redis = await _get_redis()
    key = _txlist_key(mint, source)
    return await redis.zcard(key)


async def clear_tx_list(mint: str, source: str):
    """清空指定 mint 的有序集合"""
    redis = await _get_redis()
    key = _txlist_key(mint, source)
    counter_key = f"txlist:{source}:{mint}:counter"
    await redis.delete(key)
    await redis.delete(counter_key)
    logger.info(f"[tx_redis] 清空 {key}")


# ──────────────────────────────────────────────────────────
# 交易详情操作
# ──────────────────────────────────────────────────────────

def _tx_key(sig: str) -> str:
    """获取交易详情的 key"""
    return f"tx:{sig}"


async def save_tx(tx_detail: Dict[str, Any]):
    """
    保存交易详情到 Redis
    
    Args:
        tx_detail: 交易详情字典
    """
    redis = await _get_redis()
    key = _tx_key(tx_detail.get("sig", ""))
    
    if not key or key == "tx:":
        logger.warning("[tx_redis] save_tx: sig 为空，跳过")
        return
    
    # 序列化并存储
    data = json.dumps(tx_detail, ensure_ascii=False, default=str)
    await redis.set(key, data)
    
    logger.debug(f"[tx_redis] 保存交易 {key[:20]}...")


async def get_tx(sig: str) -> Optional[Dict[str, Any]]:
    """
    获取单笔交易详情
    
    Args:
        sig: 交易签名
    
    Returns:
        交易详情字典，如果不存在返回 None
    """
    redis = await _get_redis()
    key = _tx_key(sig)
    
    data = await redis.get(key)
    if not data:
        return None
    
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning(f"[tx_redis] 解析交易 {sig[:8]}... 失败")
        return None


async def update_tx_analysis(sig: str, analysis: Dict[str, Any]):
    """
    更新交易的分析结果
    
    Args:
        sig: 交易签名
        analysis: 分析结果（net_sol_flow, net_token_flow, price_per_token, wallet_tag, processed_at）
    """
    redis = await _get_redis()
    key = _tx_key(sig)
    
    # 获取现有数据
    tx = await get_tx(sig)
    if not tx:
        logger.warning(f"[tx_redis] update_tx_analysis: 交易 {sig[:8]}... 不存在")
        return
    
    # 更新分析结果
    tx.update(analysis)
    
    # 重新保存
    data = json.dumps(tx, ensure_ascii=False, default=str)
    await redis.set(key, data)
    
    logger.debug(f"[tx_redis] 更新分析结果 {sig[:8]}...")


# ──────────────────────────────────────────────────────────
# 批量操作
# ──────────────────────────────────────────────────────────

async def save_tx_batch(tx_details: List[Dict[str, Any]], mint: str, source: str):
    """
    批量保存交易详情
    
    Args:
        tx_details: 交易详情列表
        mint: 代币地址
        source: 数据来源
    """
    redis = await _get_redis()
    counter_key = f"txlist:{source}:{mint}:counter"
    key = _txlist_key(mint, source)
    
    # 获取当前计数
    current_count = await redis.get(counter_key)
    current_count = int(current_count) if current_count else 0
    
    # 批量写入
    pipe = redis.pipeline()
    for i, tx_detail in enumerate(tx_details):
        sig = tx_detail.get("sig", "")
        if not sig:
            continue
        
        current_count += 1
        data = json.dumps(tx_detail, ensure_ascii=False, default=str)
        pipe.set(_tx_key(sig), data)
        pipe.zadd(key, {sig: current_count})
    
    pipe.set(counter_key, current_count)
    await pipe.execute()
    
    logger.info(f"[tx_redis] 批量保存 {len(tx_details)} 条交易")


async def get_tx_batch(sigs: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    批量获取交易详情
    
    Args:
        sigs: 交易签名列表
    
    Returns:
        {sig: tx_detail} 字典
    """
    if not sigs:
        return {}
    
    redis = await _get_redis()
    
    # 使用 pipeline 批量获取
    pipe = redis.pipeline()
    for sig in sigs:
        pipe.get(_tx_key(sig))
    
    results = await pipe.execute()
    
    tx_map = {}
    for sig, data in zip(sigs, results):
        if data:
            try:
                tx_map[sig] = json.loads(data)
            except json.JSONDecodeError:
                pass
    
    return tx_map
