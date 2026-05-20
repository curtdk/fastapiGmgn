




'status' =
'Success'
'signature' =
'2Wdtgoouaki3TuHA4VvedQuET2EJ9426rSxUnfNxHDULWV8PQGSkms5CNytxb8a1W9Eun9AEQjKthNrK7ejnmEEK'
'slot' =
'420993157'
'code' =
0
'totalInputAmount' =
'200000'
'totalOutputAmount' =
'27472259'
'inputAmountResult' =
'199800'
'outputAmountResult' =
'27472259'
'swapEvents' =
[{'inputMint': 'So11111111111111111111111111111111111111112', 'inputAmount': '199800', 'outputMint': '8wMAG5aRJJTF1io2ac8o7y3gnNfDD8U5XRqpZ983pump', 'outputAmount': '27472259'}]
len() =
9


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
                    
                    # 计算实际买入代币数量（假设代币 9 位小数）
                    # 处理 out_amount 可能是字符串的情况
                    try:
                        # buy_amount = float(out_amount) / 1e9
                        buy_amount = float(out_amount) 

                    except (ValueError, TypeError):
                        logger.error(f"[策略2] out_amount 转换失败: {out_amount}")
                        buy_amount = 0
                    buy_avg_price = buy_sol / buy_amount if buy_amount > 0 else 0
                    