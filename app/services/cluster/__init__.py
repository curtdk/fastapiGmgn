"""簇组（Cluster）模块 - C006 庄家检测

职责：
- 基于交易特征的智能聚类
- 支持可配置的匹配条件（CU、程序ID、指令数量）
- 自动判定 + 手动锁定
- 与现有 C001-C005 庄家判断并行执行
"""
from app.services.cluster.detector import (
    run_cluster_detection,
)
from app.services.cluster.manager import ClusterManager
from app.services.cluster.matcher import ClusterMatcher

__all__ = [
    "run_cluster_detection",
    "ClusterManager",
    "ClusterMatcher",
]