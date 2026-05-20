获取 当前价格根据  获取当前 tx。得到的数据 是下面 用下面的公式算均价 ，
{'sig': '425npkc9Gr8o1GW4sydAhQcTqUkLQHgT2C8dMud9JbeiD8V54Yinu95JVQMy4iBpwPXXC9CLGXfWost6BES5pjao', 'slot': 420991137, 'block_time': None, 'from_address': 'E28qSjMwWR9JVUoVneGiNsiuAeDGbD3ZuQPUMBvr6XKt', 'to_address': 'E28qSjMwWR9JVUoVneGiNsiuAeDGbD3ZuQPUMBvr6XKt', 'amount': 207904.406951, 'token_mint': '8wMAG5aRJJTF1io2ac8o7y3gnNfDD8U5XRqpZ983pump', 'token_symbol': '', 'transaction_type': 'BUY', 'dex': '', 'pool_address': '', 'sol_spent': 1.7249577, 'fee': 1.5e-05, 'jito_tip': 0.0, 'priority_fee': 1e-05, 'cu_consumed': 127253, 'cu_limit': 200000, 'cu_price': 50000, 'instructions_count': 8, 'inner_instructions_count': 16, 'total_instruction_count': 24, 'account_keys_count': 27, 'uses_lookup_table': False, 'signers_count': 1, 'main_instructions': '[{"index": 0, "program_id": "ComputeBudget111111111111111111111111111111", "type": "S...uBvf9Ss623VQ5DA", "type": "closeAccount"}]', 'inner_instructions': '[{"group_index": 2, "index": 0, "program_id": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623...4bD4sWpmSwMn52FMfXEA", "type": "unknown"}]', 'program_ids': '["ComputeBudget111111111111111111111111111111", "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTN...eUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"]', 'risk_score': 35, 'risk_verdict': '中等风险', 'risk_indicators': '["\\u9ad8\\u8d26\\u6237\\u6570: 27", "\\u590d\\u6742\\u4ea4\\u6613 (\\u975e\\u7b80\\u5355\\u8f6c\\u8d26)"]', 'raw_data': "{'_signature': ...TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5D', 'source': 'helius_ws'}



 async def _check_profit_and_sell(self, tx_detail: Dict[str, Any]):
        """
        检查利润，达标则卖出
        """
        if self.position["buy_cost"] <= 0 or self.position["buy_amount"] <= 0:
            return
        
        sol_spent = abs(tx_detail.get("sol_spent", 0))
        amount_raw = tx_detail.get("amount", 0)
        
        # 处理 amount 可能是字符串的情况，并转换为最小单位
        try:
            # amount = abs(float(amount_raw)) / 1e9  # 统一除以 1e9，与买入保持一致
            amount = abs(float(amount_raw))   # 统一除以 1e9，与买入保持一致

        except (ValueError, TypeError):
            logger.error(f"[策略2] amount 转换失败: {amount_raw}")
            return
        
        if amount <= 0:
            return
        
        # 计算当前市场均价（每个代币的 SOL 价格）
        market_price = sol_spent / amount
        