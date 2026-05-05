"""簇组系统自动判定规则

规则：
- 未手动锁定的簇组，满足双阈值（Tx数 > N，用户数 > M）→ 自动标记为「庄家」
- 手动锁定后，系统不再自动覆盖
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.services.cluster.redis_keys import ClusterData, set_cluster_type
from app.services.cluster.settings import get_cluster_settings

logger = logging.getLogger(__name__)


class ClusterRules:
    """簇组判定规则"""
    
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_cluster_settings(db)
    
    def should_auto_mark_dealer(self, cluster: ClusterData) -> bool:
        """
        检查是否应该自动标记为庄家
        
        条件：
        1. 簇组未被手动锁定（judgment_type == "system"）
        2. 当前类型不是 dealer
        3. 满足双阈值
        """
        # 手动锁定的簇组不参与自动判定
        if cluster.judgment_type == "manual":
            return False
        
        # 已经是庄家簇组，不需要重复标记
        if cluster.cluster_type == "dealer":
            return False
        
        # 检查双阈值
        tx_threshold = self.settings.tx_threshold
        user_threshold = self.settings.user_threshold
        
        return cluster.tx_count > tx_threshold and cluster.user_count > user_threshold
    
    async def auto_update_cluster_type(self, cluster: ClusterData) -> Optional[str]:
        """
        自动更新簇组类型
        
        Returns:
            新类型 或 None（未更新）
        """
        if self.should_auto_mark_dealer(cluster):
            new_type = "dealer"
            await set_cluster_type(cluster.name, new_type, "system")
            logger.info(f"[cluster:rules] 自动标记簇组 {cluster.name[:8]}... 为庄家 (tx={cluster.tx_count}, users={cluster.user_count})")
            return new_type
        
        return None
    
    async def check_and_update_clusters(self, clusters: list) -> list:
        """
        检查并更新多个簇组的类型
        
        Returns:
            更新类型的簇组列表
        """
        updated = []
        for cluster in clusters:
            new_type = await self.auto_update_cluster_type(cluster)
            if new_type:
                cluster.cluster_type = new_type
                updated.append(cluster)
        
        return updated


def create_rules(db: Session) -> ClusterRules:
    """创建规则实例"""
    return ClusterRules(db)