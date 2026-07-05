"""簇组管理器 - CRUD 操作

职责：
- 创建新簇组
- 更新簇组（添加 Tx、用户）
- 查询簇组
- 删除簇组
- 修改簇组类型（手动锁定）
"""
import time
import logging
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session

import asyncio
from app.services.cluster.redis_keys import (
    ClusterData,
    save_cluster_sync,
    get_cluster_sync,
    get_all_clusters_sync,
    get_enabled_clusters_sync,
    get_all_clusters,
    delete_cluster,
    set_cluster_type as redis_set_cluster_type,
    get_cluster,
)
from app.services.cluster.matcher import TxFeatures, extract_features_from_tx_detail
# rules.py 已废弃（双阈值自动判定功能）
from app.services.cluster.settings import get_cluster_settings

logger = logging.getLogger(__name__)


class ClusterManager:
    """簇组管理器"""

    # 类级别共享缓存
    _clusters_cache: List = []
    _sig_index: Dict = {}
    _cache_ts: float = 0
    _CACHE_TTL: float = 30.0  # 30s 缓存失效

    def __init__(self, db: Session):
        self.db = db
        self.settings = get_cluster_settings(db)
        # rules 已废弃，不再创建
        # 刷新内存缓存
        self._refresh_clusters_cache()

    @classmethod
    def _refresh_clusters_cache(cls):
        """刷新内存中的簇组列表和 signature 索引。"""
        try:
            cls._clusters_cache = get_enabled_clusters_sync()
            cls._sig_index = {}
            for c in cls._clusters_cache:
                sig = getattr(c, "base_signature", "")
                if sig:
                    cls._sig_index.setdefault(sig, []).append(c)
            cls._cache_ts = time.time()
        except Exception as e:
            logger.warning(f"[cluster:manager] 缓存刷新失败: {e}")

    def create_cluster(
        self,
        name: str,
        features: TxFeatures,
        folder: str = "",
    ) -> ClusterData:
        """
        创建新簇组（同步版本）

        Args:
            name: 簇组名称（通常使用首个钱包地址）
            features: 第一笔交易的特征
            folder: 所属文件夹
        """
        cluster = ClusterData(
            name=name,
            folder=folder,
            enabled=True,
            cluster_type="unknown",  # 新簇组默认未知状态
            judgment_type="system",
            base_transaction_type=features.transaction_type,
            base_programs=features.programs,
            base_main_route=features.main_route,
            base_inner_route=features.inner_route,
            base_signature=features.signature,
            created_at=time.time(),
        )

        # 使用同步版本保存
        save_cluster_sync(cluster)
        logger.info(f"[cluster:manager] 创建新簇组 {name[:8]}... (sig={features.signature[:8]}, 程序数={len(features.programs)})")

        # 刷新内存缓存（包含新的簇组）
        ClusterManager._refresh_clusters_cache()

        return cluster

    def match_cluster(
        self,
        features,
    ) -> Optional[tuple]:
        """匹配簇组（基于内存缓存 + signature 索引 + programs 保序）。

        Args:
            features: TxFeatures（已在外层提取）

        Returns:
            (matched_cluster, reason) 或 None
        """
        from app.services.cluster.matcher import compare_programs_order

        # 缓存过期自动刷新
        if time.time() - ClusterManager._cache_ts > ClusterManager._CACHE_TTL:
            ClusterManager._refresh_clusters_cache()

        # signature 索引已包含 tx_type + main_route + inner_route
        # 取出的 candidates 必然 tx_type/main_route/inner_route 一致
        candidates = ClusterManager._sig_index.get(features.signature, [])

        # # 仅保留 user_count > 10 的簇组
        # candidates = [c for c in candidates if c.get_user_count() > 10]

        # if not candidates:
        #     return None

        # programs 保序比较（signature 不含 program_ids 顺序）
        for c in candidates:
            if compare_programs_order(c.base_programs, features.programs):
                logger.debug(f"[cluster:manager] Tx {features.sig[:8]}... 匹配簇组 {c.name[:8]}... (signature+programs)")
                return (c, "signature+programs 匹配")

        return None
    
    async def get_all_clusters(self) -> List[ClusterData]:
        """获取所有簇组"""
        return await get_all_clusters()
    
    async def delete_cluster(self, name: str) -> bool:
        """删除簇组"""
        return await delete_cluster(name)
    
    async def set_cluster_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用簇组"""
        redis = await _get_redis()
        key = cluster_data_key(name)
        await redis.hset(key, "enabled", "true" if enabled else "false")
        logger.info(f"[cluster:manager] 簇组 {name[:8]}... {'启用' if enabled else '禁用'}")
        return True
    
    async def set_cluster_folder(self, name: str, folder: str) -> bool:
        """修改簇组文件夹"""
        cluster = await get_cluster(name)
        if not cluster:
            return False
        redis = await _get_redis()
        key = cluster_data_key(name)
        await redis.hset(key, "folder", folder)
        logger.info(f"[cluster:manager] 簇组 {name[:8]}... 文件夹修改为: {folder}")
        return True
    
    async def set_cluster_type(self, name: str, cluster_type: str, judgment_type: str = "manual") -> bool:
        """修改簇组类型（手动锁定）"""
        return await redis_set_cluster_type(name, cluster_type, judgment_type)


def _get_redis():
    """获取异步 Redis 连接"""
    from app.services.cluster.redis_keys import _get_redis
    return _get_redis()


def cluster_data_key(name: str) -> str:
    """获取簇组数据 Hash 的 key"""
    return f"cluster:data:{name}"


def create_manager(db: Session) -> ClusterManager:
    """创建簇组管理器实例"""
    return ClusterManager(db)