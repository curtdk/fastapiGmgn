"""簇组路由构建器

将 main/inner instructions 压缩为精简字符串，用于：
1. 减少 Redis 存储（25KB → 2-3KB）
2. 快速 hash 比较（signature 16 字节）
3. 严格顺序匹配（程序执行流程）

数据格式：
- main_route:  "M:ComputeBudget|M:ComputeBudget|M:Pump|..."
- inner_route: "2:11111|2:Tokenz...|2:11111|..."
- signature:   16 字节 hex（main+inner+tx_type 的 sha256 前 16 字符）
"""
import json
import hashlib
from typing import List, Dict, Optional


def build_main_route(main_instructions_json: str) -> str:
    """从 JSON 字符串构建主指令路由。

    只取 programId（去重按顺序），忽略 parsed/data 字段。
    """
    if not main_instructions_json:
        return ""
    try:
        items = json.loads(main_instructions_json) if isinstance(main_instructions_json, str) else main_instructions_json
    except (json.JSONDecodeError, TypeError):
        return ""
    if not items:
        return ""
    return "|".join("M:" + (it.get("program_id") or it.get("programId") or "") for it in items)


def build_inner_route(inner_instructions_json: str) -> str:
    """从 JSON 字符串构建内部指令路由。

    格式：stackHeight:programId（如 "2:11111"）。
    按 group_index + index 顺序拼接。
    """
    if not inner_instructions_json:
        return ""
    try:
        items = json.loads(inner_instructions_json) if isinstance(inner_instructions_json, str) else inner_instructions_json
    except (json.JSONDecodeError, TypeError):
        return ""
    if not items:
        return ""
    # 按 group_index + index 排序
    sorted_items = sorted(items, key=lambda x: (x.get("group_index", 0), x.get("index", 0)))
    parts = []
    for it in sorted_items:
        sh = it.get("stackHeight", it.get("stack_height", 2))
        pid = it.get("program_id") or it.get("programId") or ""
        parts.append(f"{sh}:{pid}")
    return "|".join(parts)


def build_signature(transaction_type: str, main_route: str, inner_route: str) -> str:
    """生成 16 字节 hex signature（用于快速 hash 比较）。

    输入：tx_type + main_route + inner_route
    输出：sha256 前 16 字符（hex）
    """
    raw = f"{transaction_type}|{main_route}|{inner_route}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_features(transaction_type: str,
                   main_instructions_json: str,
                   inner_instructions_json: str) -> Dict[str, str]:
    """一站式：从 tx_detail 字段构建 (main_route, inner_route, signature)。

    返回 dict：{ "main_route": str, "inner_route": str, "signature": str }
    """
    main_route = build_main_route(main_instructions_json)
    inner_route = build_inner_route(inner_instructions_json)
    signature = build_signature(transaction_type, main_route, inner_route)
    return {
        "main_route": main_route,
        "inner_route": inner_route,
        "signature": signature,
    }


# 向后兼容别名
build_main_instructions = build_main_route
build_inner_instructions = build_inner_route