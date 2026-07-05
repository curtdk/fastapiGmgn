"""簇组 Redis 数据结构定义和操作

Redis 数据结构：
  cluster:data:{cluster_name}  # Hash，存储簇组完整信息
  cluster:index               # 有序集合，按 created_at 排序，用于快速查询

簇组 Hash 字段（精简版）：
  - name: 簇组名称（首个钱包地址）
  - folder: 所属文件夹（默认空）
  - enabled: "true"/"false"
  - cluster_type: "unknown"/"retail"/"dealer"
  - judgment_type: "system"/"manual"
  - base_transaction_type: 交易类型（BUY/SELL）
  - base_programs: JSON，程序ID列表（保序）
  - base_main_route: 主指令路由字符串（programId 顺序）
  - base_inner_route: 内部指令路由字符串（(stackHeight,programId) 顺序）
  - base_signature: 16 字节 hash（用于快速匹配）
  - created_at: 创建时间戳

用户数（user_count）通过 SCAN user:* 实时计算，不再持久化。
"""
import json
import logging
from typing import Optional, List, Dict, Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = "redis://localhost:6379"

# 模块级变量
_redis: Optional[aioredis.Redis] = None


async def init_cluster_redis():
    """初始化 Redis 连接"""
    global _redis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    await _redis.ping()
    logger.info("[cluster:redis] Redis 连接成功")


async def close_cluster_redis():
    """关闭 Redis 连接"""
    global _redis
    if _redis:
        await _redis.aclose()
    logger.info("[cluster:redis] Redis 已关闭")


def _get_redis_sync() -> aioredis.Redis:
    """同步获取 Redis 连接（在已有事件循环中）"""
    global _redis
    if _redis is None:
        # 在已有事件循环中初始化
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(init_cluster_redis())
    return _redis


async def _get_redis() -> aioredis.Redis:
    """获取 Redis 连接"""
    global _redis
    if _redis is None:
        await init_cluster_redis()
    return _redis


# ──────────────────────────────────────────────────────────
# Key 生成
# ──────────────────────────────────────────────────────────

def cluster_data_key(name: str) -> str:
    """获取簇组数据 Hash 的 key"""
    return f"cluster:data:{name}"


CLUSTER_INDEX_KEY = "cluster:index"


# ──────────────────────────────────────────────────────────
# 数据结构定义
# ──────────────────────────────────────────────────────────

class ClusterData:
    """簇组数据结构（精简版）

    匹配完全由 signature + programs 保序决定：
    - base_signature: 16 字节 hash（tx_type + main_route + inner_route）
    - base_main_route: 主指令路由字符串
    - base_inner_route: 内部指令路由字符串
    - base_programs: 程序 ID 列表（保序）

    其他历史字段（base_cu / *_count / *_offset）已移除，不再保留。
    """

    def __init__(
        self,
        name: str,
        folder: str = "",
        enabled: bool = True,
        cluster_type: str = "unknown",  # unknown/retail/dealer
        judgment_type: str = "system",    # system/manual
        base_transaction_type: str = "",
        base_programs: List[str] = None,
        base_main_route: str = "",
        base_inner_route: str = "",
        base_signature: str = "",
        created_at: float = None,
    ):
        self.name = name
        self.folder = folder
        self.enabled = enabled
        self.cluster_type = cluster_type
        self.judgment_type = judgment_type
        self.base_transaction_type = base_transaction_type
        self.base_programs = base_programs or []
        self.base_main_route = base_main_route
        self.base_inner_route = base_inner_route
        self.base_signature = base_signature
        self.created_at = created_at or 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "folder": self.folder,
            "enabled": "true" if self.enabled else "false",
            "cluster_type": self.cluster_type,
            "judgment_type": self.judgment_type,
            "base_transaction_type": self.base_transaction_type,
            "base_programs": json.dumps(self.base_programs),
            "base_main_route": self.base_main_route,
            "base_inner_route": self.base_inner_route,
            "base_signature": self.base_signature,
            "created_at": str(self.created_at),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ClusterData":
        """从字典创建（兼容旧字段：旧字段一律忽略）"""
        return cls(
            name=data.get("name", ""),
            folder=data.get("folder", ""),
            enabled=data.get("enabled", "true") == "true",
            cluster_type=data.get("cluster_type", "unknown"),
            judgment_type=data.get("judgment_type", "system"),
            base_transaction_type=data.get("base_transaction_type", ""),
            base_programs=json.loads(data.get("base_programs", "[]")),
            base_main_route=data.get("base_main_route", ""),
            base_inner_route=data.get("base_inner_route", ""),
            base_signature=data.get("base_signature", ""),
            created_at=float(data.get("created_at", "0")),
        )

    def get_user_count(self) -> int:
        """实时计算当前簇组下的用户数（从 user:{addr}.cluster_name 查询）。"""
        try:
            from app.services.cluster.redis_keys import _get_sync_redis
            r = _get_sync_redis()
            count = 0
            for key in r.scan_iter(match="user:*", count=500):
                data = r.hgetall(key)
                if data.get("cluster_name") == self.name:
                    count += 1
            return count
        except Exception:
            return 0


# ──────────────────────────────────────────────────────────
# Redis 操作
# ──────────────────────────────────────────────────────────

async def save_cluster(cluster: ClusterData) -> bool:
    """保存簇组到 Redis"""
    redis = await _get_redis()
    key = cluster_data_key(cluster.name)
    
    data = cluster.to_dict()
    await redis.hset(key, mapping=data)
    
    # 更新索引（按创建时间排序）
    await redis.zadd(CLUSTER_INDEX_KEY, {cluster.name: cluster.created_at})
    
    logger.debug(f"[cluster:redis] 保存簇组 {cluster.name[:8]}...")
    return True


async def get_cluster(name: str) -> Optional[ClusterData]:
    """获取簇组数据"""
    redis = await _get_redis()
    key = cluster_data_key(name)
    
    data = await redis.hgetall(key)
    if not data:
        return None
    
    return ClusterData.from_dict(data)


async def delete_cluster(name: str) -> bool:
    """删除簇组"""
    redis = await _get_redis()
    key = cluster_data_key(name)
    
    await redis.delete(key)
    await redis.zrem(CLUSTER_INDEX_KEY, name)
    
    logger.debug(f"[cluster:redis] 删除簇组 {name[:8]}...")
    return True


async def get_all_clusters() -> List[ClusterData]:
    """获取所有簇组（按 tx_count 从大到小）"""
    redis = await _get_redis()
    
    # 从索引获取所有簇组名（按 tx_count 降序）
    names = await redis.zrevrange(CLUSTER_INDEX_KEY, 0, -1)
    
    clusters = []
    for name in names:
        cluster = await get_cluster(name)
        if cluster:
            clusters.append(cluster)
    
    return clusters


async def get_enabled_clusters() -> List[ClusterData]:
    """获取所有已启用的簇组"""
    all_clusters = await get_all_clusters()
    return [c for c in all_clusters if c.enabled]


async def update_cluster_field(name: str, field: str, value: str) -> bool:
    """更新簇组单个字段"""
    redis = await _get_redis()
    key = cluster_data_key(name)
    
    await redis.hset(key, field, value)
    return True


async def set_cluster_type(cluster_name: str, cluster_type: str, judgment_type: str = "system") -> bool:
    """设置簇组类型和判定类型"""
    redis = await _get_redis()
    key = cluster_data_key(cluster_name)
    
    await redis.hset(key, "cluster_type", cluster_type)
    await redis.hset(key, "judgment_type", judgment_type)
    
    return True


# ──────────────────────────────────────────────────────────
# 同步版本 Redis 操作（用于同步上下文中调用异步 Redis）
# 使用 redis-py 的同步客户端，避免 event loop 冲突
# ──────────────────────────────────────────────────────────

import redis

# 同步 Redis 连接
_sync_redis = None


def _get_sync_redis():
    """获取同步 Redis 连接（懒加载）"""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = redis.from_url(REDIS_URL, decode_responses=True)
        logger.info("[cluster:redis:sync] 同步 Redis 连接已创建")
    return _sync_redis


def init_cluster_redis_sync():
    """初始化同步 Redis 连接（用于同步上下文）"""
    return _get_sync_redis()


def save_cluster_sync(cluster: ClusterData) -> bool:
    """同步版本：保存簇组到 Redis（使用同步客户端）"""
    try:
        r = _get_sync_redis()
        key = cluster_data_key(cluster.name)
        data = cluster.to_dict()
        
        r.hset(key, mapping=data)
        r.zadd(CLUSTER_INDEX_KEY, {cluster.name: cluster.created_at})
        
        logger.info(f"[cluster:redis:sync] 保存簇组 {cluster.name[:8]}..., key={key}")
        return True
    except Exception as e:
        logger.warning(f"[cluster:redis:sync] 保存簇组失败: {e}")
        return False


def get_cluster_sync(name: str) -> Optional[ClusterData]:
    """同步版本：获取簇组数据（使用同步客户端）"""
    try:
        r = _get_sync_redis()
        key = cluster_data_key(name)
        data = r.hgetall(key)
        if not data:
            return None
        return ClusterData.from_dict(data)
    except Exception as e:
        logger.warning(f"[cluster:redis:sync] 获取簇组失败: {e}")
        return None


def get_all_clusters_sync() -> List[ClusterData]:
    """同步版本：获取所有簇组（使用同步客户端）"""
    try:
        r = _get_sync_redis()
        names = r.zrevrange(CLUSTER_INDEX_KEY, 0, -1)
        
        clusters = []
        for name in names:
            cluster = get_cluster_sync(name)
            if cluster:
                clusters.append(cluster)
        
        return clusters
    except Exception as e:
        logger.warning(f"[cluster:redis:sync] 获取所有簇组失败: {e}")
        return []


def get_enabled_clusters_sync() -> List[ClusterData]:
    """同步版本：获取所有已启用的簇组"""
    all_clusters = get_all_clusters_sync()
    return [c for c in all_clusters if c.enabled]


def user_mint_key(mint: str, address: str) -> str:
    """per-mint 用户持仓 key"""
    return f"user:{mint}:{address}"


def set_cluster_type_sync(cluster_name: str, cluster_type: str, judgment_type: str = "system") -> bool:
    """同步版本：设置簇组类型（用于同步上下文）"""
    try:
        r = _get_sync_redis()
        key = cluster_data_key(cluster_name)
        r.hset(key, mapping={
            "cluster_type": cluster_type,
            "judgment_type": judgment_type,
        })
        logger.info(f"[cluster:redis:sync] 簇组 {cluster_name[:8]}... 类型更新为 {cluster_type}")
        return True
    except Exception as e:
        logger.warning(f"[cluster:redis:sync] 设置簇组类型失败: {e}")
        return False
