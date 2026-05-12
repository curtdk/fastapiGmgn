"""簇组 Redis 数据结构定义和操作

Redis 数据结构：
  cluster:data:{cluster_name}  # Hash，存储簇组完整信息
  cluster:index               # 有序集合，按 tx_count 排序，用于快速查询

簇组 Hash 字段：
  - name: 簇组名称（首个钱包地址）
  - folder: 所属文件夹（默认空）
  - enabled: "true"/"false"
  - cluster_type: "undefined"/"retail"/"dealer"
  - judgment_type: "system"/"manual"
  - base_cu: 基准 CU 消耗
  - base_cu_offset: CU 偏移量
  - base_program_count: 基准程序ID数量
  - base_program_offset: 程序ID偏移量
  - base_main_instruction_count: 基准主指令数量
  - base_main_offset: 主指令偏移量
  - base_inner_instruction_count: 基准内部指令数量
  - base_inner_offset: 内部指令偏移量
  - base_programs: JSON，完整程序ID列表（含地址）
  - base_main_instructions: JSON，完整主指令列表
  - base_inner_instructions: JSON，完整内部指令列表
  - txs: JSON，Tx签名列表
  - users: JSON，钱包地址列表
  - tx_count: 总Tx数
  - user_count: 总用户数
  - created_at: 创建时间戳
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
    """簇组数据结构"""
    
    def __init__(
        self,
        name: str,
        folder: str = "",
        enabled: bool = True,
        cluster_type: str = "undefined",  # undefined/retail/dealer
        judgment_type: str = "system",    # system/manual
        base_cu: int = 0,
        base_cu_offset: int = 0,
        base_program_count: int = 0,
        base_program_offset: int = 0,
        base_main_instruction_count: int = 0,
        base_main_offset: int = 0,
        base_inner_instruction_count: int = 0,
        base_inner_offset: int = 0,
        base_programs: List[str] = None,
        base_main_instructions: List[Dict] = None,
        base_inner_instructions: List[Dict] = None,
        txs: List[str] = None,
        users: List[str] = None,
        tx_count: int = 0,
        user_count: int = 0,
        created_at: float = None,
    ):
        self.name = name
        self.folder = folder
        self.enabled = enabled
        self.cluster_type = cluster_type
        self.judgment_type = judgment_type
        self.base_cu = base_cu
        self.base_cu_offset = base_cu_offset
        self.base_program_count = base_program_count
        self.base_program_offset = base_program_offset
        self.base_main_instruction_count = base_main_instruction_count
        self.base_main_offset = base_main_offset
        self.base_inner_instruction_count = base_inner_instruction_count
        self.base_inner_offset = base_inner_offset
        self.base_programs = base_programs or []
        self.base_main_instructions = base_main_instructions or []
        self.base_inner_instructions = base_inner_instructions or []
        self.txs = txs or []
        self.users = users or []
        self.tx_count = tx_count
        self.user_count = user_count
        self.created_at = created_at or 0
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "folder": self.folder,
            "enabled": "true" if self.enabled else "false",
            "cluster_type": self.cluster_type,
            "judgment_type": self.judgment_type,
            "base_cu": str(self.base_cu),
            "base_cu_offset": str(self.base_cu_offset),
            "base_program_count": str(self.base_program_count),
            "base_program_offset": str(self.base_program_offset),
            "base_main_instruction_count": str(self.base_main_instruction_count),
            "base_main_offset": str(self.base_main_offset),
            "base_inner_instruction_count": str(self.base_inner_instruction_count),
            "base_inner_offset": str(self.base_inner_offset),
            "base_programs": json.dumps(self.base_programs),
            "base_main_instructions": json.dumps(self.base_main_instructions),
            "base_inner_instructions": json.dumps(self.base_inner_instructions),
            "txs": json.dumps(self.txs),
            "users": json.dumps(self.users),
            "tx_count": str(self.tx_count),
            "user_count": str(self.user_count),
            "created_at": str(self.created_at),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ClusterData":
        """从字典创建"""
        return cls(
            name=data.get("name", ""),
            folder=data.get("folder", ""),
            enabled=data.get("enabled", "true") == "true",
            cluster_type=data.get("cluster_type", "undefined"),
            judgment_type=data.get("judgment_type", "system"),
            base_cu=int(data.get("base_cu", "0")),
            base_cu_offset=int(data.get("base_cu_offset", "0")),
            base_program_count=int(data.get("base_program_count", "0")),
            base_program_offset=int(data.get("base_program_offset", "0")),
            base_main_instruction_count=int(data.get("base_main_instruction_count", "0")),
            base_main_offset=int(data.get("base_main_offset", "0")),
            base_inner_instruction_count=int(data.get("base_inner_instruction_count", "0")),
            base_inner_offset=int(data.get("base_inner_offset", "0")),
            base_programs=json.loads(data.get("base_programs", "[]")),
            base_main_instructions=json.loads(data.get("base_main_instructions", "[]")),
            base_inner_instructions=json.loads(data.get("base_inner_instructions", "[]")),
            txs=json.loads(data.get("txs", "[]")),
            users=json.loads(data.get("users", "[]")),
            tx_count=int(data.get("tx_count", "0")),
            user_count=int(data.get("user_count", "0")),
            created_at=float(data.get("created_at", "0")),
        )


# ──────────────────────────────────────────────────────────
# Redis 操作
# ──────────────────────────────────────────────────────────

async def save_cluster(cluster: ClusterData) -> bool:
    """保存簇组到 Redis"""
    redis = await _get_redis()
    key = cluster_data_key(cluster.name)
    
    data = cluster.to_dict()
    await redis.hset(key, mapping=data)
    
    # 更新索引（按 tx_count 排序）
    await redis.zadd(CLUSTER_INDEX_KEY, {cluster.name: cluster.tx_count})
    
    logger.debug(f"[cluster:redis] 保存簇组 {cluster.name[:8]}... tx_count={cluster.tx_count}")
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


async def add_tx_to_cluster(cluster_name: str, sig: str, user_address: str) -> bool:
    """为簇组添加 Tx 和用户（去重）"""
    redis = await _get_redis()
    key = cluster_data_key(cluster_name)
    
    # 获取现有数据
    txs_json = await redis.hget(key, "txs")
    users_json = await redis.hget(key, "users")
    
    txs = json.loads(txs_json) if txs_json else []
    users = json.loads(users_json) if users_json else []
    
    # 去重添加
    if sig not in txs:
        txs.append(sig)
    if user_address not in users:
        users.append(user_address)
    
    # 更新
    await redis.hset(key, "txs", json.dumps(txs))
    await redis.hset(key, "users", json.dumps(users))
    await redis.hset(key, "tx_count", str(len(txs)))
    await redis.hset(key, "user_count", str(len(users)))
    
    # 更新索引
    await redis.zadd(CLUSTER_INDEX_KEY, {cluster_name: len(txs)})
    
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
# ──────────────────────────────────────────────────────────

def save_cluster_sync(cluster: ClusterData) -> bool:
    """同步版本：保存簇组到 Redis（直接使用 _redis，假设已初始化）"""
    global _redis
    if _redis is None:
        logger.warning("[cluster:redis:sync] Redis 未初始化，跳过保存")
        return False
    
    key = cluster_data_key(cluster.name)
    data = cluster.to_dict()
    
    import asyncio
    asyncio.get_event_loop().run_until_complete(_redis.hset(key, mapping=data))
    asyncio.get_event_loop().run_until_complete(_redis.zadd(CLUSTER_INDEX_KEY, {cluster.name: cluster.tx_count}))
    
    logger.debug(f"[cluster:redis:sync] 保存簇组 {cluster.name[:8]}... tx_count={cluster.tx_count}")
    return True


def get_cluster_sync(name: str) -> Optional[ClusterData]:
    """同步版本：获取簇组数据（直接使用 _redis）"""
    global _redis
    if _redis is None:
        return None
    
    key = cluster_data_key(name)
    
    import asyncio
    data = asyncio.get_event_loop().run_until_complete(_redis.hgetall(key))
    if not data:
        return None
    
    return ClusterData.from_dict(data)


def get_all_clusters_sync() -> List[ClusterData]:
    """同步版本：获取所有簇组（直接使用 _redis）"""
    global _redis
    if _redis is None:
        return []
    
    import asyncio
    loop = asyncio.get_event_loop()
    
    # 从索引获取所有簇组名（按 tx_count 降序）
    names = loop.run_until_complete(_redis.zrevrange(CLUSTER_INDEX_KEY, 0, -1))
    
    clusters = []
    for name in names:
        cluster = get_cluster_sync(name)
        if cluster:
            clusters.append(cluster)
    
    return clusters


def get_enabled_clusters_sync() -> List[ClusterData]:
    """同步版本：获取所有已启用的簇组"""
    all_clusters = get_all_clusters_sync()
    return [c for c in all_clusters if c.enabled]


def add_tx_to_cluster_sync(cluster_name: str, sig: str, user_address: str) -> bool:
    """同步版本：为簇组添加 Tx 和用户（直接使用 _redis）"""
    global _redis
    if _redis is None:
        logger.warning("[cluster:redis:sync] Redis 未初始化，跳过添加交易")
        return False
    
    key = cluster_data_key(cluster_name)
    
    import asyncio
    loop = asyncio.get_event_loop()
    
    # 获取现有数据
    txs_json = loop.run_until_complete(_redis.hget(key, "txs"))
    users_json = loop.run_until_complete(_redis.hget(key, "users"))
    
    txs = json.loads(txs_json) if txs_json else []
    users = json.loads(users_json) if users_json else []
    
    # 去重添加
    if sig not in txs:
        txs.append(sig)
    if user_address not in users:
        users.append(user_address)
    
    # 更新
    loop.run_until_complete(_redis.hset(key, "txs", json.dumps(txs)))
    loop.run_until_complete(_redis.hset(key, "users", json.dumps(users)))
    loop.run_until_complete(_redis.hset(key, "tx_count", str(len(txs))))
    loop.run_until_complete(_redis.hset(key, "user_count", str(len(users))))
    loop.run_until_complete(_redis.zadd(CLUSTER_INDEX_KEY, {cluster_name: len(txs)}))
    
    return True
