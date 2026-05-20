"""
策略管理器 - 管理策略的注册、选择、启用/禁用

使用方式：
    from app.taskcl.manager import get_strategy_manager
    
    manager = get_strategy_manager()
    
    # 选择策略
    manager.select("策略1")
    
    # 启用策略
    manager.enable()
    
    # 获取当前策略
    strategy = manager.get_current_strategy()
"""
import logging
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

# 策略映射（硬编码）
STRATEGY_MAP = {
    "策略1": "app.taskcl.strategy_1:Strategy1",
    "策略2": "app.taskcl.strategy_2:Strategy2",
    "策略3": "app.taskcl.strategy_3:Strategy3",
}


class StrategyManager:
    """策略管理器（单例）"""
    
    def __init__(self):
        self._strategies: Dict[str, Any] = {}  # 已注册的策略实例
        self._current_name: str = ""           # 当前选中的策略名称
        self._current_strategy: Optional[Any] = None  # 当前策略实例
        self._enabled: bool = False             # 策略总开关
        self._mint: str = ""                    # 当前监听的代币
        
        logger.info(f"[策略管理器] 初始化完成")
    
    def get_available_strategies(self) -> list:
        """获取可用的策略列表"""
        return list(STRATEGY_MAP.keys())
    
    def select(self, name: str, mint: str = "", params: dict = None) -> bool:
        """
        选择策略
        
        Args:
            name: 策略名称（如 "策略1"）
            mint: 代币 Mint 地址
            params: 策略参数（dict）
        
        Returns:
            是否选择成功
        """
        if name not in STRATEGY_MAP:
            logger.warning(f"[策略管理器] 未知策略: {name}")
            return False
        
        try:
            # 延迟导入策略
            module_path, class_name = STRATEGY_MAP[name].split(":")
            module = __import__(module_path, fromlist=[class_name])
            StrategyClass = getattr(module, class_name)
            
            # 创建策略实例
            strategy = StrategyClass(mint=mint)
            
            # 设置策略参数
            if params:
                strategy.set_params(params)
            
            # 如果已存在同名的策略，先清除
            if name in self._strategies:
                old_strategy = self._strategies[name]
                if hasattr(old_strategy, 'disable'):
                    old_strategy.disable()
            
            # 保存策略实例
            self._strategies[name] = strategy
            self._current_name = name
            self._current_strategy = strategy
            self._mint = mint
            
            logger.info(f"[策略管理器] 已选择策略: {name}, 参数: {params}")
            return True
            
        except Exception as e:
            logger.error(f"[策略管理器] 选择策略失败: {name}, error={e}", exc_info=True)
            return False
    
    def enable(self):
        """启用策略总开关"""
        self._enabled = True
        if self._current_strategy:
            self._current_strategy.enable()
        logger.info(f"[策略管理器] 策略已启用，当前策略: {self._current_name}")
    
    def disable(self):
        """禁用策略总开关"""
        self._enabled = False
        if self._current_strategy:
            self._current_strategy.disable()
        self.clear_strategy()
        logger.info(f"[策略管理器] 策略已禁用")
    
    def clear_strategy(self):
        """清空当前策略"""
        self._current_name = ""
        self._current_strategy = None
        logger.info(f"[策略管理器] 策略已清空")
    
    def is_enabled(self) -> bool:
        """是否启用"""
        return self._enabled
    
    def get_current_strategy(self):
        """获取当前策略实例"""
        return self._current_strategy
    
    def get_current_name(self) -> str:
        """获取当前策略名称"""
        return self._current_name
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        获取策略指标
        
        Returns:
            {
                "enabled": True/False,
                "current_strategy": "策略1",
                "strategies": ["策略1", "策略2", "策略3"],
                "position": {...},
            }
        """
        result = {
            "enabled": self._enabled,
            "current_strategy": self._current_name,
            "available_strategies": self.get_available_strategies(),
        }
        
        if self._current_strategy:
            result["metrics"] = self._current_strategy.get_metrics()
        
        return result
    
    def set_mint(self, mint: str):
        """设置当前监听代币"""
        self._mint = mint
        if self._current_strategy:
            self._current_strategy.mint = mint
    
    def get_mint(self) -> str:
        """获取当前监听代币"""
        return self._mint


# 单例实例
_manager: Optional[StrategyManager] = None


def get_strategy_manager() -> StrategyManager:
    """获取策略管理器单例"""
    global _manager
    if _manager is None:
        _manager = StrategyManager()
    return _manager