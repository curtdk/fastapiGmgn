"""
策略模块 - 支持多策略的实时交易决策系统

架构说明：
1. WS 接收数据 → 策略总开关控制是否进入队列
2. 消息队列 → 策略消费者 Task
3. 消费者 → 调用当前选中的策略 on_trade()

使用方式：
1. 在 trade_live.html 中点击"开始策略"按钮
2. 选择策略（策略1/策略2/策略3）
3. WS 数据会发送到策略消费者
4. 策略根据规则自动执行买卖
"""
from app.taskcl.base import BaseStrategy
from app.taskcl.manager import StrategyManager, get_strategy_manager
from app.taskcl.consumer import start_strategy_consumer

__all__ = [
    "BaseStrategy",
    "StrategyManager", 
    "get_strategy_manager",
    "start_strategy_consumer",
]