"""
交易流水追踪模块

在每个关键环节记录交易处理过程, 支持按 mint 分组, 按 sig 串联,
最终导出为中文 Markdown 文档供人工核查。

使用方式:
    from app.services.trade_tracer import trace, start_session, end_session, get_trace_report

    # 页面"开始"时
    session_id = start_session(mint)

    # 各环节
    trace(mint, sig, "WS收到", f"from={addr}, type={tx_type}")

    # 页面"停止"或下载时
    report = get_trace_report(mint)
"""
import datetime
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_TRACE_PER_MINT = 2000

_traces: Dict[str, List[dict]] = {}
_sessions: Dict[str, dict] = {}


def _now() -> str:
    return datetime.datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]


def start_session(mint: str):
    """页面点击"开始"时调用, 初始化追踪会话"""
    _traces[mint] = []
    _sessions[mint] = {
        "mint": mint,
        "started_at": datetime.datetime.utcnow().isoformat(),
        "page_state": "waiting_for_tx",
    }
    logger.info(f"[流水追踪] 会话开始 mint={mint[:12]}...")


def end_session(mint: str):
    """页面"停止"时调用"""
    if mint in _sessions:
        _sessions[mint]["ended_at"] = datetime.datetime.utcnow().isoformat()
        _sessions[mint]["page_state"] = "stopped"
    logger.info(f"[流水追踪] 会话结束 mint={mint[:12]}...")


def set_strategy_state(mint: str, strategy_name: str = "", enabled: bool = False):
    """策略状态变更时调用"""
    if mint not in _sessions:
        _sessions[mint] = {"mint": mint, "started_at": datetime.datetime.utcnow().isoformat()}
    _sessions[mint]["strategy_name"] = strategy_name
    _sessions[mint]["strategy_enabled"] = enabled
    _sessions[mint]["page_state"] = "strategy_running" if enabled else "watching"


def trace(mint: str, sig: str, stage: str, detail: str = "", extra: dict = None):
    """
    记录一条追踪日志

    Args:
        mint: 代币地址
        sig: 交易签名
        stage: 阶段标签 (如 "WS收到", "Redis保存", "指数计算", "庄家判定", "策略入队", "策略处理")
        detail: 中文描述
        extra: 附加数据
    """
    if mint not in _traces:
        return

    entry = {
        "time": _now(),
        "sig": sig[:16] + "..." if sig else "-",
        "sig_full": sig,
        "stage": stage,
        "detail": detail,
    }
    if extra:
        entry["extra"] = extra

    _traces[mint].append(entry)

    if len(_traces[mint]) > MAX_TRACE_PER_MINT:
        _traces[mint] = _traces[mint][-MAX_TRACE_PER_MINT:]


def get_trace_report(mint: str) -> str:
    """
    生成中文 Markdown 流水报告
    """
    traces = _traces.get(mint, [])
    session = _sessions.get(mint, {})

    started = session.get("started_at", "-")
    ended = session.get("ended_at", "-")
    strategy = session.get("strategy_name", "未启用")
    strategy_enabled = session.get("strategy_enabled", False)

    lines = []
    lines.append(f"# 交易流水追踪报告")
    lines.append("")
    lines.append(f"**代币地址**: `{mint}`")
    lines.append(f"**会话开始**: {started}")
    lines.append(f"**会话结束**: {ended}")
    lines.append(f"**策略名称**: {strategy}")
    lines.append(f"**策略状态**: {'已启用' if strategy_enabled else '未启用'}")
    lines.append(f"**记录条数**: {len(traces)}")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 数据流架构图")
    lines.append("")
    lines.append("```")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  WebSocket (Helius)                                      │")
    lines.append("│  trade_stream.py  _listen_ws() → _handle_message()       │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ① WS收到原始交易数据")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  _handle_message()                                       │")
    lines.append("│  ├─ 解析 JSON, 提取签名/meta                             │")
    lines.append("│  ├─ 跳过错误交易 (meta.err)                               │")
    lines.append("│  ├─ _extract_trade_info() → tx_detail                   │")
    lines.append("│  └─ 去重检查: tx_redis.get_tx(sig)                       │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ② 去重通过, 存入 Redis")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  Redis 写入                                              │")
    lines.append("│  ├─ tx_redis.save_tx(tx_detail)         → tx:{sig}      │")
    lines.append("│  └─ tx_redis.add_tx_to_list(mint,sig)   → txlist:{mint} │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ③ 入队指数计算")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  trade_processor.py  enqueue_trade(tx_detail)            │")
    lines.append("│  → _calculate_index(tx_detail, mint)                    │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ④ 庄家判定")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  get_trader_state_with_sig()                             │")
    lines.append("│  ├─ 新用户 → _check_local_dealer_conditions()            │")
    lines.append("│  │   ├─ C006 簇组检测 (cluster_enabled?)                  │")
    lines.append("│  │   ├─ C002 ALT 条件 (dealer_alt_enabled?)               │")
    lines.append("│  │   ├─ C003 Gas 费条件 (dealer_gas_enabled?)             │")
    lines.append("│  │   ├─ C004 CU 范围条件 (dealer_cu_enabled?)             │")
    lines.append("│  │   └─ C005 程序类型判定 (dealer_risk_enabled?)          │")
    lines.append("│  │       正常用户合约优先, 再判定庄家合约                   │")
    lines.append("│  ├─ 未知 → 入队 _dealer_check_queue (C001 Helius API)    │")
    lines.append("│  └─ 已知 → 直接使用已保存状态                             │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ⑤ 指数计算 + 庄家排除")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  _calculate_index() 继续                                 │")
    lines.append("│  ├─ 读取持仓: holdingQty/holdingCost/avgPrice            │")
    lines.append("│  ├─ BUY: 加仓, 更新持仓均价                              │")
    lines.append("│  ├─ SELL: 减仓, 计算卖出本金+落袋                        │")
    lines.append("│  ├─ 更新全局指标 (bet/profit)                            │")
    lines.append("│  ├─ 庄家排除: state.status==dealer → exclude_dealer()   │")
    lines.append("│  ├─ 保存分析结果到 Redis                                 │")
    lines.append("│  └─ WS广播: trade / cluster_matched                      │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ⑥ ifNeedDealer 检查")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  ifNeedDealer 过滤                                       │")
    lines.append("│  ├─ ifNeedDealer=1 或 非庄家 → enqueue_trade_for_strategy│")
    lines.append("│  └─ ifNeedDealer=0 且 庄家 → 跳过, 不入队                │")
    lines.append("└──────────────────────┬───────────────────────────────────┘")
    lines.append("                       │ ⑦ 策略消费者")
    lines.append("                       ▼")
    lines.append("┌──────────────────────────────────────────────────────────┐")
    lines.append("│  consumer.py  _consumer_loop()                           │")
    lines.append("│  ├─ 检查策略是否启用 (manager.is_enabled())               │")
    lines.append("│  ├─ 获取当前策略 (manager.get_current_strategy())        │")
    lines.append("│  └─ strategy.on_trade(tx_detail)                         │")
    lines.append("└──────────────────────────────────────────────────────────┘")
    lines.append("```")
    lines.append("")

    if traces:
        lines.append("---")
        lines.append("")
        lines.append("## 交易流水明细")
        lines.append("")
        lines.append("| 时间 | 阶段 | 交易签名 | 详情 |")
        lines.append("|------|------|----------|------|")

        for t in traces:
            time_str = t["time"]
            stage = t["stage"]
            sig_short = t["sig"]
            detail = t.get("detail", "")
            extra = t.get("extra", {})
            if extra:
                detail += " " + json.dumps(extra, ensure_ascii=False)
            lines.append(f"| {time_str} | {stage} | {sig_short} | {detail} |")

        lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## 按交易签名的完整链路")
        lines.append("")

        sig_groups: Dict[str, list] = {}
        for t in traces:
            s = t["sig_full"]
            if s not in sig_groups:
                sig_groups[s] = []
            sig_groups[s].append(t)

        for idx, (sig_full, steps) in enumerate(sig_groups.items(), 1):
            lines.append(f"### 交易 {idx}: `{sig_full[:32]}...`")
            lines.append("")
            lines.append("| 时间 | 阶段 | 详情 |")
            lines.append("|------|------|------|")
            for s in steps:
                extra_str = ""
                if s.get("extra"):
                    extra_str = " " + json.dumps(s["extra"], ensure_ascii=False)
                lines.append(f"| {s['time']} | {s['stage']} | {s.get('detail', '')}{extra_str} |")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## 阶段统计")
        lines.append("")

        stage_count: Dict[str, int] = {}
        for t in traces:
            s = t["stage"]
            stage_count[s] = stage_count.get(s, 0) + 1

        lines.append("| 阶段 | 次数 |")
        lines.append("|------|------|")
        for stage, count in stage_count.items():
            lines.append(f"| {stage} | {count} |")
        lines.append("")

    return "\n".join(lines)


def get_trace_json(mint: str) -> dict:
    """获取 JSON 格式的追踪数据"""
    return {
        "session": _sessions.get(mint, {}),
        "traces": _traces.get(mint, []),
        "count": len(_traces.get(mint, [])),
    }


def clear_trace(mint: str):
    """清除某个 mint 的追踪数据"""
    _traces.pop(mint, None)
    _sessions.pop(mint, None)
