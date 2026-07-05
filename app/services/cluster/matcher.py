"""簇组特征匹配器

精简版：匹配完全由 ClusterManager.match_cluster（基于 _sig_index）完成。
本文件仅保留 TxFeatures 数据结构 + extract_features_from_tx_detail 提取函数。
"""
import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class TxFeatures:
    """交易特征（从 tx_detail 提取）

    关键字段：
    - main_route: 主指令路由字符串（programId 顺序）
    - inner_route: 内部指令路由字符串（stackHeight:programId 顺序）
    - signature: 16 字节 hash（tx_type + main_route + inner_route）
    - programs: 程序 ID 列表（用于最终保序比较）
    """

    def __init__(
        self,
        sig: str,
        user_address: str,
        transaction_type: str = "",
        programs: List[str] = None,
        main_route: str = "",
        inner_route: str = "",
        signature: str = "",
    ):
        self.sig = sig
        self.user_address = user_address
        self.transaction_type = transaction_type
        self.programs = programs or []
        self.main_route = main_route
        self.inner_route = inner_route
        self.signature = signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sig": self.sig,
            "user_address": self.user_address,
            "transaction_type": self.transaction_type,
        }


def extract_features_from_tx_detail(tx_detail: Dict[str, Any]) -> TxFeatures:
    """从交易详情提取特征。

    tx_detail 字段：
    - sig, from_address
    - program_ids: JSON 字符串
    - main_instructions: JSON 字符串
    - inner_instructions: JSON 字符串
    - transaction_type: str
    """
    from app.services.cluster.route import build_main_route, build_inner_route, build_signature

    sig = tx_detail.get("sig", "")
    user_address = tx_detail.get("from_address", "")

    # 解析 program_ids
    programs_json = tx_detail.get("program_ids", "[]")
    programs = json.loads(programs_json) if programs_json else []

    # main/inner instructions 原始 JSON 字符串
    main_instructions_json = tx_detail.get("main_instructions", "[]") or "[]"
    inner_instructions_json = tx_detail.get("inner_instructions", "[]") or "[]"

    # 构建精简路由字符串
    main_route = build_main_route(main_instructions_json)
    inner_route = build_inner_route(inner_instructions_json)

    transaction_type = tx_detail.get("transaction_type", "")

    # signature = sha256(tx_type + main_route + inner_route) 前 16 字符
    signature = build_signature(transaction_type, main_route, inner_route)

    return TxFeatures(
        sig=sig,
        user_address=user_address,
        transaction_type=transaction_type,
        programs=programs,
        main_route=main_route,
        inner_route=inner_route,
        signature=signature,
    )


def compare_programs_order(cluster_programs, features_programs) -> bool:
    """保序比较 program_ids 列表（signature 内不含 program_ids 顺序）。"""
    return tuple(cluster_programs or []) == tuple(features_programs or [])