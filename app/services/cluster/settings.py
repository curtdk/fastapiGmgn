"""簇组配置项读取

精简版：仅保留总开关 + C008 自动判定阈值。
匹配条件已固定（按 signature 索引 + programs 保序），不再可配置。
"""
import logging
import time
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting, get_int_setting, get_float_setting

logger = logging.getLogger(__name__)


class ClusterSettings:
    """簇组配置类（精简版）

    使用类级别共享缓存（30s TTL）：
    - 单次查询所有 setting
    - 后续 30s 内所有属性访问从内存读取
    - 避免每笔交易都查 DB
    """

    _cache: Dict[str, Any] = {}
    _cache_ts: float = 0
    _CACHE_TTL: float = 30.0

    def __init__(self, db: Session):
        self.db = db
        self._load_if_needed()

    def _load_if_needed(self):
        """检查缓存失效并按需重新加载。"""
        now = time.time()
        if not ClusterSettings._cache or (now - ClusterSettings._cache_ts) > ClusterSettings._CACHE_TTL:
            ClusterSettings._cache = self._load_all_settings()
            ClusterSettings._cache_ts = now

    def _load_all_settings(self) -> Dict[str, str]:
        """一次性查所有 setting 到 dict。"""
        return {
            "cluster_enabled": get_setting(self.db, "cluster_enabled"),
            "cluster_c008_user_threshold": get_setting(self.db, "cluster_c008_user_threshold") or "20",
            "cluster_c008_holding_ratio": get_setting(self.db, "cluster_c008_holding_ratio") or "0.30",
        }

    # ── 总开关 ──

    @property
    def enabled(self) -> bool:
        return ClusterSettings._cache.get("cluster_enabled") == "true"

    # ── C008 庄家持仓占比判定 ──

    @property
    def c008_user_threshold(self) -> int:
        return int(ClusterSettings._cache.get("cluster_c008_user_threshold") or 20)

    @property
    def c008_holding_ratio(self) -> float:
        return float(ClusterSettings._cache.get("cluster_c008_holding_ratio") or 0.30)

    def to_dict(self) -> dict:
        """转换为字典（用于前端展示）"""
        return {
            "enabled": self.enabled,
            "c008_user_threshold": self.c008_user_threshold,
            "c008_holding_ratio": self.c008_holding_ratio,
        }


def get_cluster_settings(db: Session) -> ClusterSettings:
    """获取簇组配置实例"""
    return ClusterSettings(db)