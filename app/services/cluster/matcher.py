"""簇组特征匹配器

基于交易特征（CU、程序ID、指令数量）对交易进行聚类匹配。

匹配条件：
- CU（计算单元）：基准值 ± 偏移量
- 程序ID数量：基准数 ± 偏移量
- 主指令数量：基准数 ± 偏移量
- 内部指令数量：基准数 ± 偏移量

已开启的条件必须全部满足才算匹配成功。
"""
import json
import logging
from typing import Dict, Any, Optional, Tuple, List

from app.services.cluster.settings import ClusterSettings
from app.services.cluster.redis_keys import ClusterData

logger = logging.getLogger(__name__)


class TxFeatures:
    """交易特征（从 tx_detail 提取）"""
    
    def __init__(
        self,
        sig: str,
        user_address: str,
        cu: int,
        program_count: int,
        main_instruction_count: int,
        inner_instruction_count: int,
        programs: List[str] = None,
        main_instructions: List[Dict] = None,
        inner_instructions: List[Dict] = None,
    ):
        self.sig = sig
        self.user_address = user_address
        self.cu = cu
        self.program_count = program_count
        self.main_instruction_count = main_instruction_count
        self.inner_instruction_count = inner_instruction_count
        self.programs = programs or []
        self.main_instructions = main_instructions or []
        self.inner_instructions = inner_instructions or []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sig": self.sig,
            "user_address": self.user_address,
            "cu": self.cu,
            "program_count": self.program_count,
            "main_instruction_count": self.main_instruction_count,
            "inner_instruction_count": self.inner_instruction_count,
        }


def extract_features_from_tx_detail(tx_detail: Dict[str, Any]) -> TxFeatures:
    """
    从交易详情提取特征
    
    tx_detail 格式（来自 _extract_trade_info）：
    - cu_consumed: int
    - program_ids: JSON 字符串
    - instructions_count: int
    - inner_instructions_count: int
    - main_instructions: JSON 字符串
    - inner_instructions: JSON 字符串
    """
    sig = tx_detail.get("sig", "")
    user_address = tx_detail.get("from_address", "")
    cu = tx_detail.get("cu_consumed", 0)
    
    # 解析 program_ids
    programs_json = tx_detail.get("program_ids", "[]")
    programs = json.loads(programs_json) if programs_json else []
    program_count = len(programs)
    
    # 主指令数量
    main_instruction_count = tx_detail.get("instructions_count", 0)
    
    # 内部指令数量
    inner_instruction_count = tx_detail.get("inner_instructions_count", 0)
    
    # 完整列表
    main_instructions_json = tx_detail.get("main_instructions", "[]")
    main_instructions = json.loads(main_instructions_json) if main_instructions_json else []
    
    inner_instructions_json = tx_detail.get("inner_instructions", "[]")
    inner_instructions = json.loads(inner_instructions_json) if inner_instructions_json else []
    
    return TxFeatures(
        sig=sig,
        user_address=user_address,
        cu=cu,
        program_count=program_count,
        main_instruction_count=main_instruction_count,
        inner_instruction_count=inner_instruction_count,
        programs=programs,
        main_instructions=main_instructions,
        inner_instructions=inner_instructions,
    )


class ClusterMatcher:
    """簇组匹配器"""
    
    def __init__(self, settings: ClusterSettings):
        self.settings = settings
    
    def match(self, cluster: ClusterData, features: TxFeatures) -> Tuple[bool, str]:
        """
        检查交易特征是否匹配簇组
        
        Returns:
            (is_matched: bool, reason: str)
        """
        if not cluster.enabled:
            return False, "簇组未启用"
        
        # 检查每个开启的条件
        conditions_passed = []
        conditions_failed = []
        
        # ── 条件 1：CU ──
        if self.settings.match_cu_enabled:
            cu_min = cluster.base_cu - cluster.base_cu_offset
            cu_max = cluster.base_cu + cluster.base_cu_offset
            if cu_min <= features.cu <= cu_max:
                conditions_passed.append(f"CU: {features.cu} 在 [{cu_min}, {cu_max}]")
            else:
                conditions_failed.append(f"CU: {features.cu} 不在 [{cu_min}, {cu_max}]")
        
        # ── 条件 2：程序ID数量 ──
        if self.settings.match_program_enabled:
            prog_min = cluster.base_program_count - cluster.base_program_offset
            prog_max = cluster.base_program_count + cluster.base_program_offset
            if prog_min <= features.program_count <= prog_max:
                conditions_passed.append(f"程序数: {features.program_count} 在 [{prog_min}, {prog_max}]")
            else:
                conditions_failed.append(f"程序数: {features.program_count} 不在 [{prog_min}, {prog_max}]")
        
        # ── 条件 3：主指令数量 ──
        if self.settings.match_main_instruction_enabled:
            main_min = cluster.base_main_instruction_count - cluster.base_main_offset
            main_max = cluster.base_main_instruction_count + cluster.base_main_offset
            if main_min <= features.main_instruction_count <= main_max:
                conditions_passed.append(f"主指令: {features.main_instruction_count} 在 [{main_min}, {main_max}]")
            else:
                conditions_failed.append(f"主指令: {features.main_instruction_count} 不在 [{main_min}, {main_max}]")
        
        # ── 条件 4：内部指令数量 ──
        if self.settings.match_inner_instruction_enabled:
            inner_min = cluster.base_inner_instruction_count - cluster.base_inner_offset
            inner_max = cluster.base_inner_instruction_count + cluster.base_inner_offset
            if inner_min <= features.inner_instruction_count <= inner_max:
                conditions_passed.append(f"内部指令: {features.inner_instruction_count} 在 [{inner_min}, {inner_max}]")
            else:
                conditions_failed.append(f"内部指令: {features.inner_instruction_count} 不在 [{inner_min}, {inner_max}]")
        
        # 所有开启的条件都必须满足
        if conditions_failed:
            return False, "; ".join(conditions_failed)
        
        # 至少有一个条件开启才算匹配
        if not conditions_passed:
            return False, "没有开启任何匹配条件"
        
        return True, "; ".join(conditions_passed)
    
    def match_any(self, clusters: List[ClusterData], features: TxFeatures) -> Optional[Tuple[ClusterData, str]]:
        """
        遍历所有簇组，找到第一个匹配的
        
        Returns:
            (matched_cluster, reason) 或 None
        """
        for cluster in clusters:
            is_matched, reason = self.match(cluster, features)
            if is_matched:
                return cluster, reason
        
        return None


def create_matcher(db) -> ClusterMatcher:
    """创建匹配器实例"""
    from app.services.cluster.settings import get_cluster_settings
    settings = get_cluster_settings(db)
    return ClusterMatcher(settings)