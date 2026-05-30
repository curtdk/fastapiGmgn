"""
策略2 - 高级交易策略

严格按照以下参数实现：
- maxSellSol: 有人卖X sol 触发购买
- waitTime: 等待x秒在开始操作
- waitbuyer: 等满足1个买家（在maxSellSol触发后统计）
- waitSeller: 等满足1个卖家（在maxSellSol触发后统计）
- buy: 购买x sol
- buyNum: 买入失败则重复x次
- lirun: 利润 x% 卖出
- sell: 卖出 x%
- sellNum: 卖出失败则重复执行x次
- xunHuan: 1=完成后继续循环 0=只执行一次

执行流程：
1. 等待触发：有人卖出 ≥ maxSellSol SOL → 进入统计阶段
2. 统计交易：统计后续 tx 的买家数量(≥waitbuyer) + 卖家数量(≥waitSeller)
3. 等待时间：满足条件后等待 waitTime 秒
4. 执行买入：买入 buy SOL，失败重试 buyNum 次
5. 监控利润：实时计算利润，达到 lirun% 则卖出
6. 执行卖出：卖出 sell%，失败重试 sellNum 次
7. 循环控制：xunHuan=1继续循环，xunHuan=0则结束

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
from typing import Dict, Any, List

from app.taskcl.base import BaseStrategy
from app.services.jupiter_service import get_jupiter_service

logger = logging.getLogger(__name__)

# 策略执行状态
STATE_IDLE = "idle"           # 等待触发
STATE_WAITING_TX = "waiting_tx"  # 统计交易
STATE_WAITING_TIME = "waiting_time"  # 等待时间
STATE_BUYING = "buying"       # 执行买入
STATE_HOLDING = "holding"     # 监控利润
STATE_SELLING = "selling"     # 执行卖出


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
            "waitSeller": 1,        # 等满足1个卖家
            "buy": 0.02,           # 购买x sol
            "buyNum": 3,           # 买入失败则重复x次
            "lirun": 10,           # 利润 x% 卖出
            "sell": 100,           # 卖出 x%
            "sellNum": 5,           # 卖出失败则重复执行x次
            "xunHuan": 1,          # 1=完成后继续循环 0=只执行一次
            "ifNeedDealer": 0,     # 1=需要庄家数据 0=不需要
        }
        
        # 状态跟踪
        self._state = STATE_IDLE  # 当前执行状态
        self._trigger_tx_count = 0  # 触发后的tx计数
        self._post_trigger_buyers = 0  # 触发后的买家数量
        self._post_trigger_sellers = 0  # 触发后的卖家数量
        self._waiting_start_time = 0  # 等待开始时间
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
    
    def get_current_state(self) -> str:
        """获取当前执行状态"""
        return self._state
    
    async def on_trade(self, tx_detail: Dict[str, Any]):
        """
        处理每条交易
        
        执行流程：
        1. 检测到有人卖出超过 maxSellSol → 切换到 WAITING_TX
        2. 统计后续 tx 的买家/卖家数量 → 满足条件后切换到 WAITING_TIME
        3. 等待 waitTime 秒 → 然后执行买入
        4. 持仓中监控利润 → 达标则卖出
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
        
        # ========== 状态1：等待触发 (IDLE) ==========
        if self._state == STATE_IDLE:
            if self.status == self.STATUS_HOLDING:
                # 有持仓，监控利润
                self._state = STATE_HOLDING
                await self._check_profit_and_sell(tx_detail)
                return
            
            # 检查是否有人卖出超过阈值
            max_sell_sol = self.config.get("maxSellSol", 1)
            if tx_type == "SELL" and sol_spent >= max_sell_sol:
                logger.info(f"[策略2] 🚀 触发！检测到大户卖出: {sol_spent:.4f} SOL (阈值: {max_sell_sol} SOL)")
                _add_strategy_log(f"🚀 触发！检测到大户卖出 {sol_spent:.4f} SOL")
                
                # 切换到统计交易状态
                self._state = STATE_WAITING_TX
                self._post_trigger_buyers = 0
                self._post_trigger_sellers = 0
                self._trigger_tx_count = 0
                
                # 更新前端状态
                _update_frontend_state("waiting_tx")
        
        # ========== 状态2：统计交易 (WAITING_TX) ==========
        elif self._state == STATE_WAITING_TX:
            self._trigger_tx_count += 1
            
            # 统计买家/卖家
            if tx_type == "BUY":
                self._post_trigger_buyers += 1
                logger.debug(f"[策略2] 统计: 买家+1, 当前: {self._post_trigger_buyers}")
            elif tx_type == "SELL":
                self._post_trigger_sellers += 1
                logger.debug(f"[策略2] 统计: 卖家+1, 当前: {self._post_trigger_sellers}")
            
            # 检查是否满足条件
            wait_buyer = self.config.get("waitbuyer", 0)
            wait_seller = self.config.get("waitSeller", 0)
            
            logger.info(f"[策略2] 📊 统计中: 买家={self._post_trigger_buyers}/{wait_buyer}, 卖家={self._post_trigger_sellers}/{wait_seller}")
            
            if self._post_trigger_buyers >= wait_buyer and self._post_trigger_sellers >= wait_seller:
                logger.info(f"[策略2] ✅ 统计完成！买家={self._post_trigger_buyers}, 卖家={self._post_trigger_sellers}")
                _add_strategy_log(f"✅ 统计完成！买家={self._post_trigger_buyers}, 卖家={self._post_trigger_sellers}")
                
                # 切换到等待时间状态
                self._state = STATE_WAITING_TIME
                self._waiting_start_time = time.time()
                
                # 更新前端状态
                _update_frontend_state("waiting_time")
                
                # 执行买入
                await self._execute_buy_with_retry(self.mint)
            elif self._trigger_tx_count >= 100:
                # 统计超过100个tx仍未满足条件，重置
                logger.warning(f"[策略2] ⚠️ 统计超时，重置")
                _add_strategy_log(f"⚠️ 统计超时，重置")
                self._state = STATE_IDLE
                self._post_trigger_buyers = 0
                self._post_trigger_sellers = 0
                _update_frontend_state("idle")
        
        # ========== 状态3：等待时间 (WAITING_TIME) ==========
        elif self._state == STATE_WAITING_TIME:
            elapsed = time.time() - self._waiting_start_time
            wait_time = self.config.get("waitTime", 0)
            
            if elapsed >= wait_time:
                logger.info(f"[策略2] ⏱️ 等待时间结束")
                _add_strategy_log(f"⏱️ 等待时间结束")
                # 已经在买入阶段处理
            else:
                # 仍在等待，检查是否有持仓变化
                pass
        
        # ========== 状态4：监控利润 (HOLDING) ==========
        elif self._state == STATE_HOLDING:
            if self.status == self.STATUS_HOLDING and mint == self.position["mint"]:
                await self._check_profit_and_sell(tx_detail)
            else:
                # 持仓已卖出，重置状态
                self._state = STATE_IDLE
                _update_frontend_state("idle")
    
    async def _execute_buy_with_retry(self, mint: str):
        """
        执行买入（带重试）
        """
        buy_sol = self.config.get("buy", 0.02)
        buy_num = self.config.get("buyNum", 3)
        
        self._state = STATE_BUYING
        _update_frontend_state("buying")
        
        for attempt in range(1, buy_num + 1):
            try:
                jupiter = get_jupiter_service()
                
                logger.info(f"[策略2] 🟢 执行买入 (尝试 {attempt}/{buy_num}): sol_amount={buy_sol}")
                _add_strategy_log(f"🟢 买入尝试 {attempt}/{buy_num}: {buy_sol} SOL")
                
                result = jupiter.buy(mint=mint, sol_amount=buy_sol)
                
                if result.get("success"):
                    buy_sig = result.get("signature", "")
                    out_amount = result.get("out_amount", 0)
                    
                    # 从缓存获取 decimals，如果没有则调用一次获取
                    cached = jupiter._balance_cache.get(mint)
                    if cached:
                        decimals = cached.get("decimals", 6)
                        logger.debug(f"[策略2] decimals 从缓存获取: {decimals}")
                    else:
                        token_info = jupiter.get_token_balance(mint)
                        decimals = token_info.get("decimals", 6)
                        logger.debug(f"[策略2] decimals 从 API 获取: {decimals}")
                    
                    # 计算实际买入代币数量
                    try:
                        buy_amount = float(out_amount) / (10 ** decimals)
                    except (ValueError, TypeError):
                        logger.error(f"[策略2] out_amount 转换失败: {out_amount}")
                        buy_amount = 0
                        decimals = 9
                    buy_avg_price = buy_sol / buy_amount if buy_amount > 0 else 0
                    
                    # 输出详细计算过程到日志
                    _add_strategy_log(f"📊 买入价格: sol_spent={buy_sol} SOL, out_amount={out_amount}, decimals={decimals}")
                    _add_strategy_log(f"📊 买入均价: {buy_avg_price:.10f} SOL/代币 = {buy_sol}/({out_amount}/10^{decimals})")
                    _add_strategy_log(f"📊 实际获得: {buy_amount} 代币 = {out_amount}/10^{decimals}")
                    
                    # 更新持仓
                    self.update_position(
                        mint=mint,
                        buy_sig=buy_sig,
                        buy_cost=buy_sol,
                        buy_amount=buy_amount,
                        buy_avg_price=buy_avg_price,
                    )
                    
                    logger.info(f"[策略2] ✅ 买入成功: sig={buy_sig[:16]}..., cost={buy_sol} SOL, amount={buy_amount}, decimals={decimals}")
                    _add_strategy_log(f"✅ 买入成功: {buy_sol} SOL, 获得 {buy_amount} 代币")
                    
                    # 切换到监控利润状态
                    self._state = STATE_HOLDING
                    _update_frontend_state("holding")
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
        
        # 重置状态
        self._state = STATE_IDLE
        _update_frontend_state("idle")
    
    async def _check_profit_and_sell(self, tx_detail: Dict[str, Any]):
        """
        检查利润，达标则卖出
        """
        if self.position["buy_cost"] <= 0 or self.position["buy_amount"] <= 0:
            return
        
        sol_spent = abs(tx_detail.get("sol_spent", 0))
        amount_raw = tx_detail.get("amount", 0)
        
        # amount 已经是处理过的代币数量，不需要再除以 decimals
        try:
            amount = abs(float(amount_raw))
        except (ValueError, TypeError):
            logger.error(f"[策略2] amount 转换失败: {amount_raw}")
            return
        
        if amount <= 0:
            return
        
        # 计算当前市场均价（每个代币的 SOL 价格）
        market_price = sol_spent / amount
        
        # 计算利润
        buy_cost = self.position["buy_cost"]
        buy_amount = self.position["buy_amount"]
        current_value = buy_amount * market_price
        profit = current_value - buy_cost
        profit_rate = (profit / buy_cost) * 100 if buy_cost > 0 else 0
        
        # 输出详细计算过程到日志
        _add_strategy_log(f"📊 市价监控: sol_spent={sol_spent:.6f} SOL, amount={amount} 代币")
        _add_strategy_log(f"📊 市场单价: {market_price:.10f} SOL/代币 = {sol_spent}/{amount}")
        _add_strategy_log(f"📊 持仓市值: {current_value:.6f} SOL = {buy_amount} × {market_price}")
        _add_strategy_log(f"📊 利润计算: {profit:.6f} SOL ({profit_rate:.2f}%) = {current_value} - {buy_cost}")
        
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
        """
        sell_percent = self.config.get("sell", 100)
        sell_num = self.config.get("sellNum", 5)
        
        self._state = STATE_SELLING
        _update_frontend_state("selling")
        
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
                    
                    # 输出详细计算过程到日志
                    _add_strategy_log(f"📊 卖出结果: out_amount_sol={out_amount_sol:.6f} SOL, 成本={buy_cost:.6f} SOL")
                    _add_strategy_log(f"📊 卖出利润: {profit:.6f} SOL ({profit_rate:.2f}%) = {out_amount_sol} - {buy_cost}")
                    
                    logger.info(f"[策略2] ✅ 卖出成功: sig={sell_sig[:16]}..., 获得 {out_amount_sol:.4f} SOL, 利润 {profit:.4f} SOL ({profit_rate:.2f}%)")
                    _add_strategy_log(f"✅ 卖出成功: 获得 {out_amount_sol:.4f} SOL, 利润 {profit:.4f} SOL ({profit_rate:.2f}%)")
                    
                    # 清空持仓
                    self.clear_position()
                    
                    # 重置计数器
                    self._post_trigger_buyers = 0
                    self._post_trigger_sellers = 0
                    self._cycle_count += 1
                    
                    # 检查是否继续循环
                    xun_huan = self.config.get("xunHuan", 1)
                    if xun_huan == 0:
                        self._is_finished = True
                        self._state = STATE_IDLE
                        logger.info(f"[策略2] 🏁 策略执行完成 (xunHuan=0)")
                        _add_strategy_log(f"🏁 策略执行完成，共循环 {self._cycle_count} 次")
                    else:
                        logger.info(f"[策略2] 🔄 继续循环 (xunHuan=1)，循环次数: {self._cycle_count}")
                        _add_strategy_log(f"🔄 继续循环，循环次数: {self._cycle_count}")
                        self._state = STATE_IDLE
                    
                    _update_frontend_state("idle")
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
        
        # 重置状态
        self._state = STATE_IDLE
        _update_frontend_state("idle")
    
    async def _sleep(self, seconds: float):
        """异步等待"""
        import asyncio
        await asyncio.sleep(seconds)
    
    def get_metrics(self) -> Dict[str, Any]:
        """获取策略指标"""
        metrics = super().get_metrics()
        
        # 添加配置信息
        metrics["config"] = self.config
        metrics["state"] = self._state
        metrics["post_trigger_buyers"] = self._post_trigger_buyers
        metrics["post_trigger_sellers"] = self._post_trigger_sellers
        metrics["cycle_count"] = self._cycle_count
        metrics["is_finished"] = self._is_finished
        
        return metrics
    
    def reset(self):
        """重置策略状态"""
        self._state = STATE_IDLE
        self._post_trigger_buyers = 0
        self._post_trigger_sellers = 0
        self._trigger_tx_count = 0
        self._cycle_count = 0
        self._is_finished = False
        self.clear_position()
        logger.info(f"[策略2] 已重置")
        _add_strategy_log("🔄 策略已重置")


def _update_frontend_state(state: str):
    """更新前端状态指示器"""
    try:
        from app.routes.strategy_api import update_strategy_state
        update_strategy_state(state)
    except Exception:
        pass