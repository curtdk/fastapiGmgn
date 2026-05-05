"""簇组配置项读取

从 settings_service 读取簇组相关的配置项：
- 总开关
- 匹配条件开关
- 自动判定阈值
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.services.settings_service import get_setting, get_int_setting

logger = logging.getLogger(__name__)


class ClusterSettings:
    """簇组配置类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    # ── 总开关 ──
    
    @property
    def enabled(self) -> bool:
        """簇组功能是否启用"""
        return get_setting(self.db, "cluster_enabled") == "true"
    
    # ── 匹配条件开关 ──
    
    @property
    def match_cu_enabled(self) -> bool:
        """CU 匹配条件是否启用"""
        return get_setting(self.db, "cluster_match_cu_enabled") == "true"
    
    @property
    def match_program_enabled(self) -> bool:
        """程序ID数量匹配条件是否启用"""
        return get_setting(self.db, "cluster_match_program_enabled") == "true"
    
    @property
    def match_main_instruction_enabled(self) -> bool:
        """主指令数量匹配条件是否启用"""
        return get_setting(self.db, "cluster_match_main_instruction_enabled") == "true"
    
    @property
    def match_inner_instruction_enabled(self) -> bool:
        """内部指令数量匹配条件是否启用"""
        return get_setting(self.db, "cluster_match_inner_instruction_enabled") == "true"
    
    # ── 匹配偏移量设置 ──
    
    @property
    def cu_offset(self) -> int:
        """CU 偏移量（基准 ± offset）"""
        return get_int_setting(self.db, "cluster_cu_offset", 0)
    
    @property
    def program_offset(self) -> int:
        """程序ID数量偏移量"""
        return get_int_setting(self.db, "cluster_program_offset", 0)
    
    @property
    def main_instruction_offset(self) -> int:
        """主指令数量偏移量"""
        return get_int_setting(self.db, "cluster_main_instruction_offset", 0)
    
    @property
    def inner_instruction_offset(self) -> int:
        """内部指令数量偏移量"""
        return get_int_setting(self.db, "cluster_inner_instruction_offset", 0)
    
    # ── 自动判定阈值 ──
    
    @property
    def tx_threshold(self) -> int:
        """Tx数阈值（默认 > 50）"""
        return get_int_setting(self.db, "cluster_tx_threshold", 50)
    
    @property
    def user_threshold(self) -> int:
        """用户数阈值（默认 > 50）"""
        return get_int_setting(self.db, "cluster_user_threshold", 50)
    
    def to_dict(self) -> dict:
        """转换为字典（用于前端展示）"""
        return {
            "enabled": self.enabled,
            "match_cu_enabled": self.match_cu_enabled,
            "match_program_enabled": self.match_program_enabled,
            "match_main_instruction_enabled": self.match_main_instruction_enabled,
            "match_inner_instruction_enabled": self.match_inner_instruction_enabled,
            "cu_offset": self.cu_offset,
            "program_offset": self.program_offset,
            "main_instruction_offset": self.main_instruction_offset,
            "inner_instruction_offset": self.inner_instruction_offset,
            "tx_threshold": self.tx_threshold,
            "user_threshold": self.user_threshold,
        }


def get_cluster_settings(db: Session) -> ClusterSettings:
    """获取簇组配置实例"""
    return ClusterSettings(db)