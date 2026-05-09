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

from app.services.cluster.redis_keys import (
    ClusterData,
    save_cluster,
    get_cluster,
    get_all_clusters,
    get_enabled_clusters,
    delete_cluster,
    add_tx_to_cluster,
    set_cluster_type as redis_set_cluster_type,
)
from app.services.cluster.matcher import TxFeatures, extract_features_from_tx_detail
from app.services.cluster.rules import create_rules, ClusterRules
from app.services.cluster.settings import get_cluster_settings

logger = logging.getLogger(__name__)


class ClusterManager:
    """簇组管理器"""
    
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_cluster_settings(db)
        self.rules = create_rules(db)
    
    async def create_cluster(
        self,
        name: str,
        features: TxFeatures,
        folder: str = "",
    ) -> ClusterData:
        """
        创建新簇组
        
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
            base_cu=features.cu,
            base_cu_offset=self.settings.cu_offset,
            base_program_count=features.program_count,
            base_program_offset=self.settings.program_offset,
            base_main_instruction_count=features.main_instruction_count,
            base_main_offset=self.settings.main_instruction_offset,
            base_inner_instruction_count=features.inner_instruction_count,
            base_inner_offset=self.settings.inner_instruction_offset,
            base_programs=features.programs,
            base_main_instructions=features.main_instructions,
            base_inner_instructions=features.inner_instructions,
            txs=[features.sig],
            users=[features.user_address],
            tx_count=1,
            user_count=1,
            created_at=time.time(),
        )
        
        await save_cluster(cluster)
        logger.info(f"[cluster:manager] 创建新簇组 {name[:8]}... (CU={features.cu}, 程序数={features.program_count})")
        
        return cluster
    
    async def add_tx(
        self,
        cluster_name: str,
        sig: str,
        user_address: str,
    ) -> bool:
        """为簇组添加交易"""
        await add_tx_to_cluster(cluster_name, sig, user_address)
        
        # 更新基准特征（合并，新来的交易也要考虑）
        cluster = await get_cluster(cluster_name)
        if cluster:
            # 检查是否需要自动更新类型
            await self.rules.auto_update_cluster_type(cluster)
        
        return True
    
    async def get_cluster_info(self, name: str) -> Optional[ClusterData]:
        """获取簇组信息"""
        return await get_cluster(name)
    
    async def get_all_clusters(self) -> List[ClusterData]:
        """获取所有簇组"""
        return await get_all_clusters()
    
    async def get_enabled_clusters(self) -> List[ClusterData]:
        """获取已启用的簇组"""
        return await get_enabled_clusters()
    
    async def delete_cluster(self, name: str) -> bool:
        """删除簇组"""
        return await delete_cluster(name)
    
    async def set_cluster_enabled(self, name: str, enabled: bool) -> bool:
        """设置簇组启用/禁用"""
        from app.services.cluster.redis_keys import update_cluster_field
        await update_cluster_field(name, "enabled", "true" if enabled else "false")
        logger.info(f"[cluster:manager] 设置簇组 {name[:8]}... 启用状态: {enabled}")
        return True
    
    async def set_cluster_folder(self, name: str, folder: str) -> bool:
        """设置簇组文件夹"""
        from app.services.cluster.redis_keys import update_cluster_field
        await update_cluster_field(name, "folder", folder)
        logger.info(f"[cluster:manager] 设置簇组 {name[:8]}... 文件夹: {folder}")
        return True
    
    async def set_cluster_type(
        self,
        name: str,
        cluster_type: str,
        judgment_type: str = "manual",
    ) -> bool:
        """
        设置簇组类型（手动锁定）
        
        Args:
            name: 簇组名称
            cluster_type: 簇组类型 ("undefined"/"retail"/"dealer")
            judgment_type: 判定类型 ("manual" 表示手动锁定)
        """
        await redis_set_cluster_type(name, cluster_type, judgment_type)
        logger.info(f"[cluster:manager] 设置簇组 {name[:8]}... 类型: {cluster_type}, 判定: {judgment_type}")
        return True
    
    def match_cluster(
        self,
        tx_detail: Dict[str, Any],
    ) -> Optional[tuple]:
        """
        匹配簇组（遍历所有已启用簇组）
        
        Returns:
            (matched_cluster, reason) 或 None
        """
        from app.services.cluster.matcher import ClusterMatcher
        from app.services.cluster.redis_keys import get_enabled_clusters
        import asyncio
        
        # 提取特征
        features = extract_features_from_tx_detail(tx_detail)
        
        # 创建匹配器
        matcher = ClusterMatcher(self.settings)
        
        # 获取已启用簇组
        clusters = asyncio.get_event_loop().run_until_complete(get_enabled_clusters())
        
        # 匹配
        result = matcher.match_any(clusters, features)
        
        if result:
            logger.debug(f"[cluster:manager] Tx {features.sig[:8]}... 匹配簇组 {result[0].name[:8]}... ({result[1]})")
        
        return result
    
    async def match_cluster_async(
        self,
        tx_detail: Dict[str, Any],
    ) -> Optional[tuple]:
        """
        异步版本：匹配簇组
        
        Returns:
            (matched_cluster, reason) 或 None
        """
        from app.services.cluster.matcher import ClusterMatcher
        
        # 提取特征
        features = extract_features_from_tx_detail(tx_detail)
        
        # 创建匹配器
        matcher = ClusterMatcher(self.settings)
        
        # 获取已启用簇组
        clusters = await get_enabled_clusters()
        
        # 匹配
        result = matcher.match_any(clusters, features)
        
        if result:
            logger.debug(f"[cluster:manager] Tx {features.sig[:8]}... 匹配簇组 {result[0].name[:8]}... ({result[1]})")
        
        return result


def create_manager(db: Session) -> ClusterManager:
    """创建簇组管理器实例"""
    return ClusterManager(db)