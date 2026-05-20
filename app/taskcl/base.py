"""
策略基类 - 所有策略的抽象基类

策略需要实现的方法：
1. on_trade(tx_detail) - 每条交易触发时调用
2. get_metrics() - 可选，返回当前策略的指标数据

策略状态：
- idle: 空闲，等待机会
- holding: 持仓中，监控利润
- cooldown: 冷却中，等待下次机会

使用示例：
    class MyStrategy(BaseStrategy):
        async def on_trade(self, tx_detail):
            # 检测交易，满足条件则下单
            pass
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """策略基类"""
    
    # 策略名称（子类覆盖）
    name: str = "BaseStrategy"
    
    # 策略状态
    STATUS_IDLE = "idle"       # 空闲
    STATUS_HOLDING = "holding"  # 持仓
    STATUS_COOLDOWN = "cooldown"  # 冷却
    
    def __init__(self, mint: str = ""):
        """
        初始化策略
        
        Args:
            mint: 代币 Mint 地址（策略绑定的代币）
        """
        self.mint = mint
        self.enabled = False
        self.status = self.STATUS_IDLE
        
        # 持仓信息
        self.position = {
            "mint": "",
            "buy_sig": "",
            "buy_cost": 0.0,
            "buy_amount": 0.0,
            "buy_avg_price": 0.0,
            "buy_time": "",
        }
        
        # 配置参数
        self.config = {
            "profit_threshold": 10.0,    # 利润阈值（%）
            "sell_threshold": 2.0,        # 卖出金额阈值（SOL）
            "buy_amount": 0.1,            # 买入金额（SOL）
        }
        
        logger.info(f"[策略] {self.name} 初始化完成")
    
    @abstractmethod
    async def on_trade(self, tx_detail: Dict[str, Any]):
        """
        处理每条交易数据（子类必须实现）
        
        Args:
            tx_detail: 交易详情，结构如下：
                {
                    "sig": "xxx",                    # 交易签名
                    "token_mint": "xxx",            # 代币 Mint
                    "from_address": "xxx",          # 发起者地址
                    "transaction_type": "BUY/SELL", # 交易类型
                    "sol_spent": 1.5,               # SOL 金额（正=买入，负=卖出）
                    "amount": 1000,                 # 代币数量
                    "fee": 0.0005,                  # 手续费
                    ...
                }
        """
        pass
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        获取策略当前指标（子类可覆盖）
        
        Returns:
            {
                "status": "idle/holding/cooldown",
                "has_position": True/False,
                "buy_cost": 0.5,       # 买入成本
                "buy_amount": 1000,    # 持仓数量
                "current_profit": 0.1, # 当前利润
                "profit_rate": 20.0,  # 利润率%
            }
        """
        return {
            "name": self.name,
            "status": self.status,
            "has_position": self.position["buy_cost"] > 0,
            "buy_cost": self.position["buy_cost"],
            "buy_amount": self.position["buy_amount"],
            "buy_avg_price": self.position["buy_avg_price"],
            "buy_sig": self.position["buy_sig"],
            "enabled": self.enabled,
        }
    
    def set_config(self, key: str, value: Any):
        """设置配置参数"""
        self.config[key] = value
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置参数"""
        return self.config.get(key, default)
    
    def set_params(self, params: Dict[str, Any]):
        """
        设置策略参数（子类可覆盖）
        
        Args:
            params: 参数字典，如 {"maxSellSol": 1, "waitTime": 1, ...}
        """
        # 将参数合并到 config
        self.config.update(params)
        logger.info(f"[策略] {self.name} 参数已更新: {params}")
    
    def enable(self):
        """启用策略"""
        self.enabled = True
        logger.info(f"[策略] {self.name} 已启用")
    
    def disable(self):
        """禁用策略"""
        self.enabled = False
        logger.info(f"[策略] {self.name} 已禁用")
    
    def update_position(self, mint: str, buy_sig: str, buy_cost: float, buy_amount: float, buy_avg_price: float):
        """
        更新持仓信息
        
        Args:
            mint: 代币 Mint
            buy_sig: 买入交易签名
            buy_cost: 买入花费的 SOL
            buy_amount: 买入的代币数量
            buy_avg_price: 买入均价
        """
        from datetime import datetime
        self.position = {
            "mint": mint,
            "buy_sig": buy_sig,
            "buy_cost": buy_cost,
            "buy_amount": buy_amount,
            "buy_avg_price": buy_avg_price,
            "buy_time": datetime.utcnow().isoformat(),
        }
        self.status = self.STATUS_HOLDING
        logger.info(f"[策略] {self.name} 持仓已更新: cost={buy_cost} SOL, amount={buy_amount}, avg={buy_avg_price}")
    
    def clear_position(self):
        """清空持仓"""
        self.position = {
            "mint": "",
            "buy_sig": "",
            "buy_cost": 0.0,
            "buy_amount": 0.0,
            "buy_avg_price": 0.0,
            "buy_time": "",
        }
        self.status = self.STATUS_IDLE
        logger.info(f"[策略] {self.name} 持仓已清空")
    
    async def calculate_profit(self, market_price: float) -> Dict[str, float]:
        """
        计算当前利润（基于市场实时价格）
        
        Args:
            market_price: 当前市场价（SOL/Token）
        
        Returns:
            {
                "profit": 0.1,           # 利润（SOL）
                "profit_rate": 20.0,     # 利润率（%）
                "current_value": 0.6,    # 当前市值
            }
        """
        if self.position["buy_cost"] <= 0 or self.position["buy_amount"] <= 0:
            return {"profit": 0, "profit_rate": 0, "current_value": 0}
        
        buy_cost = self.position["buy_cost"]
        buy_amount = self.position["buy_amount"]
        
        current_value = buy_amount * market_price
        profit = current_value - buy_cost
        profit_rate = (profit / buy_cost) * 100 if buy_cost > 0 else 0
        
        return {
            "profit": profit,
            "profit_rate": profit_rate,
            "current_value": current_value,
        }