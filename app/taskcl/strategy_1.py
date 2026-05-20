"""
策略1 - 追卖买入策略

核心逻辑：
1. 检测：大户卖出 > 2 SOL（排除自己）
2. 买入：立即买入固定金额（如 0.1 SOL）
3. 持有：监控利润
4. 卖出：利润 > 10% 时自动卖出

利润计算：
- 通过实时市场交易计算当前持仓市值
- 利润 = 当前市值 - 买入成本
- 利润率 = 利润 / 买入成本 × 100%

使用方式：
    from app.taskcl.manager import get_strategy_manager
    
    manager = get_strategy_manager()
    manager.select("策略1", mint="xxx")
    manager.enable()
"""
import logging
from typing import Dict, Any

from app.taskcl.base import BaseStrategy
from app.services.jupiter_service import get_jupiter_service

logger = logging.getLogger(__name__)


class Strategy1(BaseStrategy):
    """追卖买入策略"""
    
    name = "策略1"
    
    def __init__(self, mint: str = ""):
        super().__init__(mint)
        
        # 覆盖默认配置
        self.config["profit_threshold"] = 10.0   # 10% 利润阈值
        self.config["sell_threshold"] = 2.0       # 2 SOL 卖出阈值
        self.config["buy_amount"] = 0.1          # 每次买入 0.1 SOL
        
        # 冷却时间（秒）
        self.cooldown_seconds = 30
        self._last_action_time = 0
        
        logger.info(f"[策略1] 初始化完成: sell_threshold={self.config['sell_threshold']} SOL, profit_threshold={self.config['profit_threshold']}%, buy_amount={self.config['buy_amount']} SOL")
    
    async def on_trade(self, tx_detail: Dict[str, Any]):
        """
        处理每条交易
        
        逻辑：
        1. 如果有持仓 → 计算利润，达标则卖出
        2. 如果没持仓 → 检测大户卖出，满足则买入
        """
        import time
        
        sig = tx_detail.get("sig", "")
        mint = tx_detail.get("token_mint", "")
        from_address = tx_detail.get("from_address", "")
        tx_type = tx_detail.get("transaction_type", "")
        sol_spent = abs(tx_detail.get("sol_spent", 0))
        amount = abs(tx_detail.get("amount", 0))
        
        # 获取钱包地址
        jupiter = get_jupiter_service()
        my_wallet = jupiter.wallet_address
        
        logger.debug(f"[策略1] 处理交易 sig={sig[:8]}... type={tx_type} sol_spent={sol_spent:.4f}")
        
        # ========== 情况1：有持仓，监听利润 ==========
        if self.status == self.STATUS_HOLDING and mint == self.position["mint"]:
            await self._check_profit_and_sell(tx_detail)
        
        # ========== 情况2：没持仓，检测大户卖出 ==========
        elif self.status == self.STATUS_IDLE:
            # 检查冷却时间
            current_time = time.time()
            if current_time - self._last_action_time < self.cooldown_seconds:
                logger.debug(f"[策略1] 冷却中，剩余 {int(self.cooldown_seconds - (current_time - self._last_action_time))} 秒")
                return
            
            # 排除自己
            if from_address == my_wallet:
                logger.debug(f"[策略1] 跳过自己的交易 sig={sig[:8]}...")
                return
            
            # 检测：大户卖出 > 阈值
            sell_threshold = self.config["sell_threshold"]
            if tx_type == "SELL" and sol_spent > sell_threshold:
                logger.info(f"[策略1] 🚨 检测到大户卖出: {sol_spent:.4f} SOL (阈值: {sell_threshold} SOL)")
                
                # 立即买入
                await self._execute_buy(mint, self.config["buy_amount"])
                
                self._last_action_time = current_time
    
    async def _check_profit_and_sell(self, tx_detail: Dict[str, Any]):
        """
        检查利润，达标则卖出
        
        计算方式：
        - 用当前交易的市场价格计算持仓当前市值
        - 利润 = 当前市值 - 买入成本
        """
        if self.position["buy_cost"] <= 0 or self.position["buy_amount"] <= 0:
            return
        
        sol_spent = abs(tx_detail.get("sol_spent", 0))
        amount = abs(tx_detail.get("amount", 0))
        
        if amount <= 0:
            return
        
        # 计算当前市场均价
        market_price = sol_spent / amount
        
        # 计算利润
        buy_cost = self.position["buy_cost"]
        buy_amount = self.position["buy_amount"]
        current_value = buy_amount * market_price
        profit = current_value - buy_cost
        profit_rate = (profit / buy_cost) * 100 if buy_cost > 0 else 0
        
        logger.info(f"[策略1] 当前利润: {profit:.4f} SOL ({profit_rate:.2f}%), 市值: {current_value:.4f} SOL, 成本: {buy_cost:.4f} SOL")
        
        # 利润达标，卖出
        profit_threshold = self.config["profit_threshold"]
        if profit_rate >= profit_threshold:
            logger.info(f"[策略1] ✅ 利润达标 ({profit_rate:.2f}% >= {profit_threshold}%)，执行卖出")
            await self._execute_sell(self.position["mint"])
    
    async def _execute_buy(self, mint: str, sol_amount: float):
        """
        执行买入
        
        Args:
            mint: 代币 Mint
            sol_amount: 买入 SOL 金额
        """
        try:
            jupiter = get_jupiter_service()
            
            logger.info(f"[策略1] 🟢 执行买入: mint={mint[:8]}..., sol_amount={sol_amount}")
            
            result = jupiter.buy(mint=mint, sol_amount=sol_amount)
            
            if result.get("success"):
                buy_sig = result.get("signature", "")
                out_amount = result.get("out_amount", 0)
                
                # 计算实际买入代币数量
                buy_amount = out_amount / 1e9  # 假设代币 9 位小数
                buy_avg_price = sol_amount / buy_amount if buy_amount > 0 else 0
                
                # 更新持仓
                self.update_position(
                    mint=mint,
                    buy_sig=buy_sig,
                    buy_cost=sol_amount,
                    buy_amount=buy_amount,
                    buy_avg_price=buy_avg_price,
                )
                
                logger.info(f"[策略1] ✅ 买入成功: sig={buy_sig[:16]}..., cost={sol_amount} SOL, amount={buy_amount}, avg={buy_avg_price:.8f}")
            else:
                logger.error(f"[策略1] ❌ 买入失败: {result.get('error', '未知错误')}")
                
        except Exception as e:
            logger.error(f"[策略1] ❌ 买入异常: {e}", exc_info=True)
    
    async def _execute_sell(self, mint: str):
        """
        执行卖出
        
        Args:
            mint: 代币 Mint
        """
        try:
            jupiter = get_jupiter_service()
            
            logger.info(f"[策略1] 🔴 执行卖出: mint={mint[:8]}...")
            
            result = jupiter.sell(mint=mint, percent=100)
            
            if result.get("success"):
                sell_sig = result.get("signature", "")
                out_amount_sol = result.get("out_amount_sol", 0)
                
                # 计算利润
                buy_cost = self.position["buy_cost"]
                profit = out_amount_sol - buy_cost
                profit_rate = (profit / buy_cost) * 100 if buy_cost > 0 else 0
                
                logger.info(f"[策略1] ✅ 卖出成功: sig={sell_sig[:16]}..., 获得 {out_amount_sol:.4f} SOL, 利润 {profit:.4f} SOL ({profit_rate:.2f}%)")
                
                # 清空持仓
                self.clear_position()
            else:
                logger.error(f"[策略1] ❌ 卖出失败: {result.get('error', '未知错误')}")
                
        except Exception as e:
            logger.error(f"[策略1] ❌ 卖出异常: {e}", exc_info=True)
    
    def get_metrics(self) -> Dict[str, Any]:
        """获取策略指标（增强版）"""
        metrics = super().get_metrics()
        
        # 添加配置信息
        metrics["config"] = self.config
        metrics["cooldown_seconds"] = self.cooldown_seconds
        
        return metrics