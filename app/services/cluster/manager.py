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
    save_cluster_sync,
    get_cluster_sync,
    get_all_clusters_sync,
    get_enabled_clusters_sync,
    add_tx_to_cluster_sync,
)
from app.services.cluster.matcher import TxFeatures, extract_features_from_tx_detail
from app.services.cluster.rules import create_rules
from app.services.cluster.settings import get_cluster_settings

logger = logging.getLogger(__name__)


class ClusterManager:
    """簇组管理器"""
    
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_cluster_settings(db)
        self.rules = create_rules(db)
    
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
        
        # 使用同步版本保存
        save_cluster_sync(cluster)
        logger.info(f"[cluster:manager] 创建新簇组 {name[:8]}... (CU={features.cu}, 程序数={features.program_count})")
        
        return cluster
    
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
        
        # 提取特征
        features = extract_features_from_tx_detail(tx_detail)
        
        # 创建匹配器
        matcher = ClusterMatcher(self.settings)
        
        # 获取已启用簇组（同步版本）
        clusters = get_enabled_clusters_sync()
        
        # 匹配
        result = matcher.match_any(clusters, features)
        
        if result:
            logger.debug(f"[cluster:manager] Tx {features.sig[:8]}... 匹配簇组 {result[0].name[:8]}... ({result[1]})")
        
        return result


def create_manager(db: Session) -> ClusterManager:
    """创建簇组管理器实例"""
    return ClusterManager(db)