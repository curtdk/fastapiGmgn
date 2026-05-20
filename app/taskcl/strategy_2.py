"""
策略2 - 高级交易策略

严格按照以下参数实现：
- maxSellSol: 有人卖X sol 触发购买
- waitTime: 等待x秒在开始操作
- waitbuyer: 等满足1个买家
- waitSeller: 等满足1个卖家
- buy: 购买x sol
- buyNum: 买入失败则重复x次
- lirun: 利润 x% 卖出
- sell: 卖出 x%
- sellNum: 卖出失败则重复执行x次
- xunHuan: 1=完成后继续循环 0=只执行一次

使用方式：
    from app.taskcl.manager import get_strategy_manager
    
    manager = get_strategy_manager()
    manager.select("策略2", mint="xxx", params={
        "maxSellSol": 1,
        "waitTime": 1,
        "waitbuyer": 2,
        "waitSeller": 1,
        "buy": 0.02,
        "buyNum": 3,
        "lirun": 10,
        "sell": 100,
        "sellNum": 5,
        "xunHuan": 1
    })
    manager.enable()
"""
import logging
import time
from typing import Dict, Any, List, Optional

from app.taskcl.base import BaseStrategy
from app.services.jupiter_service import get_jupiter_service

logger = logging.getLogger(__name__)


def _add_strategy_log(message: str):
    """添加策略日志到全局存储"""
    try:
        from app.routes.strategy_api import add_strategy_log
        add_strategy_log(message)
    except Exception:
        pass  # 忽略导入错误


class Strategy2(BaseStrategy):
    """高级交易策略"""
    
    name = "策略2"
    
    def __init__(self, mint: str = ""):
        super().__init__(mint)
        
        # 默认参数配置
        self.config = {
            "maxSellSol": 1,       # 有人卖X sol 触发购买
            "waitTime": 1,         # 等待x秒在开始操作
            "waitbuyer": 2,        # 等满足1个买家
            "waitSeller": 1,      # 等满足1个卖家
            "buy": 0.02,          # 购买x sol
            "buyNum": 3,           # 买入失败则重复x次
            "lirun": 10,           # 利润 x% 卖出
            "sell": 100,           # 卖出 x%
            "sellNum": 5,          # 卖出失败则重复执行x次
            "xunHuan": 1,          # 1=完成后继续循环 0=只执行一次
        }
        
        # 状态跟踪
        self._buyer_count = 0      # 当前买家数量
        self._seller_count = 0     # 当前卖家数量
        self._trade_addresses: List[str] = []  # 记录交易过的地址
        self._start_time = 0       # 策略开始时间
        self._waiting_start_time = 0  # 等待开始时间
        self._is_waiting = False   # 是否在等待中
        self._cycle_count = 0       # 循环次数
        self._is_finished = False  # 是否已完成（xunHuan=0时使用）
        
        logger.info(f"[策略2] 初始化完成: {self.config}")
    
    def set_params(self, params: Dict[str, Any]):
        """设置策略参数"""
        # 合并参数，保留默认值
        for key, value in self.config.items():
            if key in params:
                self.config[key] = params[key]
        
        logger.info(f"[策略2] 参数已更新: {self.config}")
        _add_strategy_log(f"📋 策略2参数: maxSellSol={self.config['maxSellSol']}, buy={self.config['buy']}, lirun={self.config['lirun']}%")
    
    async def on_trade(self, tx_detail: Dict[str, Any]):
        """
        处理每条交易
        
        逻辑：
        1. 统计买家/卖家数量
        2. 等待满足条件（买家数量、卖家数量、等待时间）
        3. 检测到有人卖出超过 maxSellSol 时触发购买
        4. 执行买入，重试 buyNum 次
        5. 监控利润，达到 lirun% 时卖出
        6. 卖出后根据 xunHuan 决定是否继续循环
        """
        sig = tx_detail.get("sig", "")
        mint = tx_detail.get("token_mint", "")
        from_address = tx_detail.get("from_address", "")
        tx_type = tx_detail.get("transaction_type", "")
        sol_spent = abs(tx_detail.get("sol_spent", 0))
        amount = abs(tx_detail.get("amount", 0))
        
        # 获取钱包地址
        jupiter = get_jupiter_service()
        my_wallet = jupiter.wallet_address
        
        # 排除自己
        if from_address == my_wallet:
            logger.debug(f"[策略2] 跳过自己的交易 sig={sig[:8]}...")
            return
        
        # 记录交易地址
        if from_address not in self._trade_addresses:
            self._trade_addresses.append(from_address)
        
        # 统计买家/卖家
        if tx_type == "BUY":
            self._buyer_count += 1
            logger.debug(f"[策略2] 买家+1, 当前: {self._buyer_count}")
        elif tx_type == "SELL":
            self._seller_count += 1
            logger.debug(f"[策略2] 卖家+1, 当前: {self._seller_count}")
        
        # ========== 情况1：已完成，不再执行 ==========
        if self._is_finished:
            return
        
        # ========== 情况2：正在等待条件 ==========
        if self._is_waiting:
            elapsed = time.time() - self._waiting_start_time
            wait_time = self.config.get("waitTime", 0)
            
            # 检查是否等待时间已到
            if elapsed < wait_time:
                return
            
            # 等待时间到，检查条件
            self._is_waiting = False
            logger.info(f"[策略2] ⏱️ 等待结束，开始检查交易信号")
            _add_strategy_log(f"⏱️ 等待时间结束，检查交易信号...")
        
        # ========== 情况3：持仓中，监控利润 ==========
        if self.status == self.STATUS_HOLDING and mint == self.position["mint"]:
            await self._check_profit_and_sell(tx_detail)
            return
        
        # ========== 情况4：空闲，检查是否满足购买条件 ==========
        if self.status == self.STATUS_IDLE:
            await self._check_buy_condition(tx_detail)
    
    async def _check_buy_condition(self, tx_detail: Dict[str, Any]):
        """
        检查是否满足购买条件
        
        条件：
        1. waitbuyer: 满足买家数量
        2. waitSeller: 满足卖家数量
        3. maxSellSol: 有人卖出超过阈值
        """
        sig = tx_detail.get("sig", "")
        from_address = tx_detail.get("from_address", "")
        tx_type = tx_detail.get("transaction_type", "")
        sol_spent = abs(tx_detail.get("sol_spent", 0))
        
        wait_buyer = self.config.get("waitbuyer", 0)
        wait_seller = self.config.get("waitSeller", 0)
        max_sell_sol = self.config.get("maxSellSol", 1)
        
        # 检查是否满足买家数量要求
        if self._buyer_count < wait_buyer:
            logger.debug(f"[策略2] 买家不足: {self._buyer_count} < {wait_buyer}")
            return
        
        # 检查是否满足卖家数量要求
        if self._seller_count < wait_seller:
            logger.debug(f"[策略2] 卖家不足: {self._seller_count} < {wait_seller}")
            return
        
        # 检查是否有人卖出超过阈值
        if tx_type != "SELL" or sol_spent < max_sell_sol:
            return
        
        # 条件满足，开始等待
        logger.info(f"[策略2] 🎯 检测到满足条件: 买家={self._buyer_count}, 卖家={self._seller_count}, 大户卖出={sol_spent:.4f} SOL")
        _add_strategy_log(f"🎯 条件满足: 买家={self._buyer_count}, 卖家={self._seller_count}, 卖出={sol_spent:.4f} SOL")
        
        # 开始等待
        self._is_waiting = True
        self._waiting_start_time = time.time()
        
        # 执行购买
        await self._execute_buy_with_retry(self.mint)
    
    async def _execute_buy_with_retry(self, mint: str):
        """
        执行买入（带重试）
        
        Args:
            mint: 代币 Mint
        """
        buy_sol = self.config.get("buy", 0.02)
        buy_num = self.config.get("buyNum", 3)
        
        for attempt in range(1, buy_num + 1):
            try:
                jupiter = get_jupiter_service()
                
                logger.info(f"[策略2] 🟢 执行买入 (尝试 {attempt}/{buy_num}): sol_amount={buy_sol}")
                _add_strategy_log(f"🟢 买入尝试 {attempt}/{buy_num}: {buy_sol} SOL")
                
                result = jupiter.buy(mint=mint, sol_amount=buy_sol)
                
                if result.get("success"):
                    buy_sig = result.get("signature", "")
                    out_amount = result.get("out_amount", 0)
                    
                    # 计算实际买入代币数量（假设代币 9 位小数）
                    buy_amount = out_amount / 1e9
                    buy_avg_price = buy_sol / buy_amount if buy_amount > 0 else 0
                    
                    # 更新持仓
                    self.update_position(
                        mint=mint,
                        buy_sig=buy_sig,
                        buy_cost=buy_sol,
                        buy_amount=buy_amount,
                        buy_avg_price=buy_avg_price,
                    )
                    
                    logger.info(f"[策略2] ✅ 买入成功: sig={buy_sig[:16]}..., cost={buy_sol} SOL, amount={buy_amount}")
                    _add_strategy_log(f"✅ 买入成功: {buy_sol} SOL, 获得 {buy_amount} 代币")
                    return
                else:
                    error = result.get("error", "未知错误")
                    logger.error(f"[策略2] ❌ 买入失败 (尝试 {attempt}/{buy_num}): {error}")
                    _add_strategy_log(f"❌ 买入失败 {attempt}/{buy_num}: {error}")
                    
            except Exception as e:
                logger.error(f"[策略2] ❌ 买入异常 (尝试 {attempt}/{buy_num}): {e}", exc_info=True)
                _add_strategy_log(f"❌ 买入异常 {attempt}/{buy_num}: {str(e)}")
            
            # 重试前等待
            if attempt < buy_num:
                await self._sleep(2)
        
        # 所有尝试都失败
        logger.error(f"[策略2] ❌ 买入失败: 已重试 {buy_num} 次")
        _add_strategy_log(f"❌ 买入失败: 已重试 {buy_num} 次")
    
    async def _check_profit_and_sell(self, tx_detail: Dict[str, Any]):
        """
        检查利润，达标则卖出
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
        
        logger.info(f"[策略2] 💰 当前利润: {profit:.4f} SOL ({profit_rate:.2f}%), 市值: {current_value:.4f} SOL, 成本: {buy_cost:.4f} SOL")
        
        # 利润达标，卖出
        lirun = self.config.get("lirun", 10)
        if profit_rate >= lirun:
            logger.info(f"[策略2] ✅ 利润达标 ({profit_rate:.2f}% >= {lirun}%)，执行卖出")
            _add_strategy_log(f"✅ 利润达标 ({profit_rate:.2f}% >= {lirun}%)，执行卖出")
            await self._execute_sell_with_retry(self.position["mint"])
    
    async def _execute_sell_with_retry(self, mint: str):
        """
        执行卖出（带重试）
        
        Args:
            mint: 代币 Mint
        """
        sell_percent = self.config.get("sell", 100)
        sell_num = self.config.get("sellNum", 5)
        
        for attempt in range(1, sell_num + 1):
            try:
                jupiter = get_jupiter_service()
                
                logger.info(f"[策略2] 🔴 执行卖出 (尝试 {attempt}/{sell_num}): {sell_percent}%")
                _add_strategy_log(f"🔴 卖出尝试 {attempt}/{sell_num}: {sell_percent}%")
                
                result = jupiter.sell(mint=mint, percent=sell_percent)
                
                if result.get("success"):
                    sell_sig = result.get("signature", "")
                    out_amount_sol = result.get("out_amount_sol", 0)
                    
                    # 计算利润
                    buy_cost = self.position["buy_cost"]
                    profit = out_amount_sol - buy_cost
                    profit_rate = (profit / buy_cost) * 100 if buy_cost > 0 else 0
                    
                    logger.info(f"[策略2] ✅ 卖出成功: sig={sell_sig[:16]}..., 获得 {out_amount_sol:.4f} SOL, 利润 {profit:.4f} SOL ({profit_rate:.2f}%)")
                    _add_strategy_log(f"✅ 卖出成功: 获得 {out_amount_sol:.4f} SOL, 利润 {profit:.4f} SOL ({profit_rate:.2f}%)")
                    
                    # 清空持仓
                    self.clear_position()
                    
                    # 重置计数器
                    self._buyer_count = 0
                    self._seller_count = 0
                    self._trade_addresses = []
                    self._cycle_count += 1
                    
                    # 检查是否继续循环
                    xun_huan = self.config.get("xunHuan", 1)
                    if xun_huan == 0:
                        self._is_finished = True
                        logger.info(f"[策略2] 🏁 策略执行完成 (xunHuan=0)")
                        _add_strategy_log(f"🏁 策略执行完成，共循环 {self._cycle_count} 次")
                    else:
                        logger.info(f"[策略2] 🔄 继续循环 (xunHuan=1)，循环次数: {self._cycle_count}")
                        _add_strategy_log(f"🔄 继续循环，循环次数: {self._cycle_count}")
                    
                    return
                else:
                    error = result.get("error", "未知错误")
                    logger.error(f"[策略2] ❌ 卖出失败 (尝试 {attempt}/{sell_num}): {error}")
                    _add_strategy_log(f"❌ 卖出失败 {attempt}/{sell_num}: {error}")
                    
            except Exception as e:
                logger.error(f"[策略2] ❌ 卖出异常 (尝试 {attempt}/{sell_num}): {e}", exc_info=True)
                _add_strategy_log(f"❌ 卖出异常 {attempt}/{sell_num}: {str(e)}")
            
            # 重试前等待
            if attempt < sell_num:
                await self._sleep(2)
        
        # 所有尝试都失败
        logger.error(f"[策略2] ❌ 卖出失败: 已重试 {sell_num} 次")
        _add_strategy_log(f"❌ 卖出失败: 已重试 {sell_num} 次")
    
    async def _sleep(self, seconds: float):
        """异步等待"""
        import asyncio
        await asyncio.sleep(seconds)
    
    def get_metrics(self) -> Dict[str, Any]:
        """获取策略指标"""
        metrics = super().get_metrics()
        
        # 添加配置信息
        metrics["config"] = self.config
        metrics["buyer_count"] = self._buyer_count
        metrics["seller_count"] = self._seller_count
        metrics["cycle_count"] = self._cycle_count
        metrics["is_waiting"] = self._is_waiting
        metrics["is_finished"] = self._is_finished
        
        return metrics
    
    def reset(self):
        """重置策略状态"""
        self._buyer_count = 0
        self._seller_count = 0
        self._trade_addresses = []
        self._is_waiting = False
        self._cycle_count = 0
        self._is_finished = False
        self.clear_position()
        logger.info(f"[策略2] 已重置")
        _add_strategy_log("🔄 策略已重置")
