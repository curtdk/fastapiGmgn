"""设置服务 - 读写系统设置"""
from sqlalchemy.orm import Session
from app.models.trade import Setting
from app.schemas.trade import SettingResponse
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# 默认设置
DEFAULT_SETTINGS = {
    "batch_size": "100",           # parseTransactions 每批数量
    "concurrent_requests": "3",    # 并发请求数
    "request_interval": "0.5",     # 批次间隔(秒)
    "ws_max_connections": "10",    # WS最大连接数
    "trade_page_size": "50",       # 前端显示交易行数
    "helius_api_key": "",          # Helius API 密钥
}


def init_default_settings(db: Session):
    """初始化默认设置（不存在时创建）"""
    for key, value in DEFAULT_SETTINGS.items():
        existing = db.query(Setting).filter(Setting.key == key).first()
        if not existing:
            db.add(Setting(key=key, value=value, description=f"默认设置: {key}"))
    db.commit()
    logger.info("默认设置初始化完成")


def get_setting(db: Session, key: str) -> Optional[str]:
    """获取单个设置值"""
    setting = db.query(Setting).filter(Setting.key == key).first()
    return setting.value if setting else DEFAULT_SETTINGS.get(key)


def get_all_settings(db: Session) -> dict:
    """获取所有设置（含默认值）"""
    settings = db.query(Setting).all()
    result = dict(DEFAULT_SETTINGS)  # 先填充默认值
    for s in settings:
        result[s.key] = s.value
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
