"""簇组检测器 - C006 入口

C006 执行流程：
1. 检查簇组总开关是否开启
2. 遍历所有已启用簇组，尝试匹配
3. 匹配成功：
   - 已定义簇组（retail/dealer）→ 确定身份，标记 C006
   - 未定义簇组 → 确定所属簇组，继续走 C001-C005
4. 无匹配 → 创建新簇组，第一笔 Tx 保持 unknown

返回值：
- matched_cluster: 匹配到的簇组 或 None
- should_continue_detection: 是否继续执行 C001-C005
- user_status: 用户状态（来自簇组类型）或 None
"""
import logging
from typing import Optional, Tuple, Dict, Any

from sqlalchemy.orm import Session

from app.services.cluster.settings import get_cluster_settings, ClusterSettings
from app.services.cluster.matcher import extract_features_from_tx_detail
from app.services.cluster.manager import create_manager, ClusterManager
from app.services.cluster.redis_keys import ClusterData
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)


class ClusterDetectionResult:
    """簇组检测结果"""
    
    def __init__(
        self,
        matched: bool = False,
        cluster: Optional[ClusterData] = None,
        cluster_type: Optional[str] = None,  # unknown/retail/dealer
        judgment_type: str = "system",        # system/manual
        should_continue_detection: bool = True,  # 是否继续 C001-C005
        created_new_cluster: bool = False,
        reason: str = "",
        new_cluster_broadcast: Optional[Dict] = None,  # 新簇组创建时的广播数据
    ):
        self.matched = matched
        self.cluster = cluster
        self.cluster_type = cluster_type
        self.judgment_type = judgment_type
        self.should_continue_detection = should_continue_detection
        self.created_new_cluster = created_new_cluster
        self.reason = reason
        self.new_cluster_broadcast = new_cluster_broadcast  # 用于统一广播
    
    @property
    def user_status(self) -> Optional[str]:
        """从簇组类型推断用户状态"""
        if self.cluster_type == "dealer":
            return "dealer"
        elif self.cluster_type == "retail":
            return "retail"
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "matched": self.matched,
            "cluster_name": self.cluster.name if self.cluster else None,
            "cluster_type": self.cluster_type,
            "judgment_type": self.judgment_type,
            "should_continue_detection": self.should_continue_detection,
            "created_new_cluster": self.created_new_cluster,
            "reason": self.reason,
        }


def run_cluster_detection(
    db: Session,
    tx_detail: Dict[str, Any],
    mint: str,
) -> ClusterDetectionResult:
    """
    同步版本：执行 C006 簇组检测
    
    Args:
        db: 数据库 session
        tx_detail: 交易详情（来自 _extract_trade_info）
        mint: 代币地址
    
    Returns:
        ClusterDetectionResult
    """
    settings = get_cluster_settings(db)
    
    # 检查总开关
    if not settings.enabled:
        logger.debug("[C006] 簇组功能未启用")
        return ClusterDetectionResult(reason="簇组功能未启用")
    
    # 提取特征
    features = extract_features_from_tx_detail(tx_detail)
    sig = features.sig
    user_address = features.user_address
    
    logger.debug(f"[C006] 检测 Tx {sig[:8]}... user={user_address[:8]}...")
    
    # 创建管理器
    manager = create_manager(db)
    
    # 尝试匹配已有簇组（同步版本）
    match_result = manager.match_cluster(tx_detail)
    
    if match_result:
        cluster, reason = match_result
        logger.info(f"[C006] Tx {sig[:8]}... 匹配到簇组 {cluster.name[:8]}... ({reason})")
        
        # 先添加 Tx 和用户（无论什么类型都要更新）
        from app.services.cluster.redis_keys import add_tx_to_cluster_sync
        add_tx_to_cluster_sync(cluster.name, sig, user_address)
        
        # 簇组类型决定后续流程
        if cluster.cluster_type in ("retail", "dealer"):
            # 已定义簇组 → 确定身份，不再执行 C001-C005
            return ClusterDetectionResult(
                matched=True,
                cluster=cluster,
                cluster_type=cluster.cluster_type,
                judgment_type=cluster.judgment_type,
                should_continue_detection=False,
                created_new_cluster=False,
                reason=reason,
            )
        else:
            # 未定义簇组 → 确定所属，继续执行 C001-C005
            return ClusterDetectionResult(
                matched=True,
                cluster=cluster,
                cluster_type=cluster.cluster_type,  # undefined
                judgment_type=cluster.judgment_type,
                should_continue_detection=True,  # 继续 C001-C005
                created_new_cluster=False,
                reason=f"匹配未定义簇组: {reason}",
            )
    
    # 无匹配 → 创建新簇组
    # 新簇组名称：使用钱包地址
    new_cluster_name = user_address
    
    new_cluster = manager.create_cluster_sync(
        name=new_cluster_name,
        features=features,
    )
    
    logger.info(f"[C006] Tx {sig[:8]}... 无匹配，创建新簇组 {new_cluster_name[:8]}...")
    
    # 返回广播数据，由上层统一广播
    broadcast_data = {
        "type": "cluster_created",
        "data": {
            "cluster_name": new_cluster_name,
            "sig": sig,
            "user_address": user_address,
            "features": features.to_dict(),
        }
    }
    
    # 第一笔 Tx 保持 unknown，继续走 C001-C005
    return ClusterDetectionResult(
        matched=True,
        cluster=new_cluster,
        cluster_type="unknown",
        judgment_type="system",
        should_continue_detection=True,  # 继续 C001-C005
        created_new_cluster=True,
        reason="新簇组创建，第一笔 Tx 继续检测",
        new_cluster_broadcast=broadcast_data,
    )


async def get_cluster_stats(db: Session) -> Dict[str, Any]:
    """获取簇组统计信息"""
    manager = create_manager(db)
    clusters = await manager.get_all_clusters()
    
    stats = {
        "total_clusters": len(clusters),
        "by_type": {
            "unknown": 0,
            "retail": 0,
            "dealer": 0,
        },
        "by_judgment": {
            "system": 0,
            "manual": 0,
        },
        "total_txs": sum(c.tx_count for c in clusters),
        "total_users": sum(c.user_count for c in clusters),
    }
    
    for cluster in clusters:
        stats["by_type"][cluster.cluster_type] = stats["by_type"].get(cluster.cluster_type, 0) + 1
        stats["by_judgment"][cluster.judgment_type] = stats["by_judgment"].get(cluster.judgment_type, 0) + 1
    
    return stats