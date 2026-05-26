"""设置服务 - 读写系统设置"""
import json

from sqlalchemy.orm import Session
from app.models.trade import Setting
from app.schemas.trade import SettingResponse
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 默认设置
# 从 dealer_detector 常量生成 JSON 默认值
def _default_skip_programs_json():
    from app.services.dealer_detector import SKIP_PROGRAMS
    return json.dumps(sorted(SKIP_PROGRAMS), ensure_ascii=False)

def _default_normal_user_programs_json():
    from app.services.dealer_detector import NORMAL_USER_PROGRAMS
    return json.dumps(NORMAL_USER_PROGRAMS, ensure_ascii=False)

def _default_dealer_programs_json():
    from app.services.dealer_detector import DEALER_PROGRAMS
    return json.dumps(DEALER_PROGRAMS, ensure_ascii=False)

DEFAULT_SETTINGS = {
    "batch_size": "100",           # parseTransactions 每批数量
    "concurrent_requests": "3",    # 并发请求数
    "request_interval": "0.5",     # 批次间隔(秒)
    "ws_max_connections": "10",    # WS最大连接数
    "trade_page_size": "50",       # 前端显示交易行数
    "helius_api_key": "",          # Helius API 密钥
    "backfill_skip_ws_wait": "false", # 测试模式：跳过 sync_point 等待
    # 庄家检测条件设置
    "dealer_c001_enabled": "false", # C001 首笔交易 closeAccount 条件启用
    "dealer_alt_enabled": "false", # ALT 条件启用
    "dealer_gas_enabled": "false", # Gas 费条件启用
    "dealer_gas_max": "0.00001",   # Gas 费最大值 (SOL)
    "dealer_cu_enabled": "false",  # CU 条件启用
    "dealer_cu_min": "0",         # CU 最小值
    "dealer_cu_max": "200000",     # CU 最大值
    "dealer_risk_enabled": "false", # 风险分条件启用 → C005 程序类型判定
    "dealer_risk_min": "0",        # 风险分最小值（C005 改造后保留兼容）
    "dealer_skip_programs": "",    # C005 SKIP 程序列表（JSON 数组，空则用硬编码默认值）
    "dealer_normal_user_programs": "", # C005 普通用户程序（JSON 对象，空则用硬编码默认值）
    "dealer_dealer_programs": "",  # C005 庄家程序（JSON 对象，空则用硬编码默认值）
    # 簇组（C006）设置
    "cluster_enabled": "false",              # 簇组功能总开关
    "cluster_match_cu_enabled": "false",     # CU 匹配条件
    "cluster_match_program_enabled": "false", # 程序ID数量匹配
    "cluster_match_main_instruction_enabled": "false", # 主指令数量匹配
    "cluster_match_inner_instruction_enabled": "false", # 内部指令数量匹配
    "cluster_cu_offset": "0",                # CU 偏移量
    "cluster_program_offset": "0",          # 程序ID数量偏移量
    "cluster_main_instruction_offset": "0", # 主指令数量偏移量
    "cluster_inner_instruction_offset": "0", # 内部指令数量偏移量
    "cluster_tx_threshold": "50",           # 自动判定庄家 Tx数阈值
    "cluster_user_threshold": "50",         # 自动判定庄家用户数阈值
    # Jupiter 交易设置
    "jupiter_buy_amounts": "0.05,0.1,0.3,0.5",  # 买入 SOL 数量快捷选项
    "jupiter_sell_percents": "10,50,100",        # 卖出比例快捷选项
    "jupiter_buy_slippage": "500",               # 买入滑点 (bps)
    "jupiter_sell_slippage": "500",              # 卖出滑点 (bps)
    "jupiter_priority": "Medium",                # 优先级: Min/Low/Medium/High/VeryHigh
    "jupiter_confirm": "true",                   # 交易前二次确认
}


def init_default_settings(db: Session):
    """初始化默认设置（不存在时创建）"""
    for key, value in DEFAULT_SETTINGS.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        if not existing:
            actual_value = value
            if key == "dealer_skip_programs":
                actual_value = _default_skip_programs_json()
            elif key == "dealer_normal_user_programs":
                actual_value = _default_normal_user_programs_json()
            elif key == "dealer_dealer_programs":
                actual_value = _default_dealer_programs_json()
            db.add(Setting(key=key, value=actual_value, description=f"默认设置: {key}"))
    db.commit()
    logger.info("默认设置初始化完成")


def get_setting(db: Session, key: str) -> Optional[str]:
    """获取单个设置值"""
    setting = db.query(Setting).filter(Setting.key == key).first()
    value = setting.value if setting else DEFAULT_SETTINGS.get(key)

    _json_defaults = {
        "dealer_skip_programs": _default_skip_programs_json,
        "dealer_normal_user_programs": _default_normal_user_programs_json,
        "dealer_dealer_programs": _default_dealer_programs_json,
    }
    if key in _json_defaults and not value:
        value = _json_defaults[key]()

    return value


def get_all_settings(db: Session) -> dict:
    """获取所有设置（含默认值）"""
    settings = db.query(Setting).all()
    result = dict(DEFAULT_SETTINGS)
    for s in settings:
        result[s.key] = s.value

    for key in ("dealer_skip_programs", "dealer_normal_user_programs", "dealer_dealer_programs"):
        if not result.get(key):
            if key == "dealer_skip_programs":
                result[key] = _default_skip_programs_json()
            elif key == "dealer_normal_user_programs":
                result[key] = _default_normal_user_programs_json()
            elif key == "dealer_dealer_programs":
                result[key] = _default_dealer_programs_json()

    return result


def update_setting(db: Session, key: str, value: str) -> SettingResponse:
    """更新设置"""
    setting = db.query(Setting).filter(Setting.key == key).first()
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        db.add(setting)
    db.commit()
    db.refresh(setting)
    logger.info(f"设置更新: {key} = {value}")
    return SettingResponse.model_validate(setting)


def get_int_setting(db: Session, key: str, default: int) -> int:
    """获取整数类型设置"""
    val = get_setting(db, key)
    try:
        return int(val) if val else default
    except (ValueError, TypeError):
        return default


def get_float_setting(db: Session, key: str, default: float) -> float:
    """获取浮点数类型设置"""
    val = get_setting(db, key)
    try:
        return float(val) if val else default
    except (ValueError, TypeError):
        return default
