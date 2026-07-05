"""簇组系统自动判定规则（已废弃）

⚠️ 此文件已废弃：
- tx_count / user_count 字段已从 ClusterData 移除
- tx_threshold / user_threshold 字段已从 ClusterSettings 移除
- 双阈值自动标记 dealer 功能暂未实现替代逻辑

C008 持仓占比自动判定仍保留（详见 settings.c008_*）。
"""
import logging

logger = logging.getLogger(__name__)