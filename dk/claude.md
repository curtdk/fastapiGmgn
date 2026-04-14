当前问题：TradeStream (WS) 和 TradeBackfill (RPC) 并发写入导致 slot
 排序混乱，补漏方案也因此出错。同一时间多个 trade 造成 slot.desc(), block_time.desc(), id.desc()
 排序不可靠。

 新方案：WS 先启动顺序写入，Backfill 创建占位记录后填充，两组数据合起来保证完整有序。

 核心流程

 1. TradeStream 启动 → transactionSubscribe 接收 trade → 直接 INSERT (source='helius_ws')
 2. 第一条 WS 交易存入后，记录 sync_point (signature)
 3. TradeBackfill 立即启动 → getSignaturesForAddress(before=sync_point, limit=1000) 分页获取
    - 使用 before=sync_point 参数，RPC 返回的就是 sync_point 之前的历史签名
    - 循环分页直到返回不足 1000 条或达到 50,000 条上限
    - 物理层面实现无缝对接，不需要逐条比对
 4. Backfill 将签名反转（从旧到新），创建占位记录：
    - 对每个 sig INSERT 占位：{sig, slot, block_time, source='rpc_fill'}，其他字段为空
 5. Backfill 进入 _batch_parse_and_fill → 获取完整 tx 详情
 6. 使用 UPDATE 填充 source='rpc_fill' 的占位记录（不 INSERT 新记录）
 7. Backfill 永远不触碰 source='helius_ws' 的记录

 全量排序查询

 高并发下 WS 和 RPC 的 id 会交织，单靠 id ASC 无法保证"历史在前，实时在后"。

 正确排序：逻辑分区 + 各自内部 id ASC
 ORDER BY
   CASE WHEN source = 'rpc_fill' THEN 0 ELSE 1 END,
   id ASC
 - source='rpc_fill' 的记录（历史数据）在前，按 id ASC
 - source='helius_ws' 的记录（实时数据）在后，按 id ASC

 指数计算触发

 Backfill 完成 (_batch_parse_and_fill 执行完毕)
   → 推送状态 'DATA_READY' 通知前端
   → 触发 calculate_metrics() 开始计算
   → 按全量排序顺序逐个处理交易
   → 每个交易处理后创建 TradeAnalysis 记录（标记已处理）
   → WS 新数据插入时也触发 process_trade，保持顺序

 关键：计算引擎按全量排序顺序处理，确保历史数据先计算，实时数据后计算。

 修改文件

 1. app/services/trade_stream.py

 - _handle_message 中 INSERT 成功后，如果 sync_point 未设置，记录第一条 sig 为 self.sync_point
 - 新增 self.sync_point: Optional[str] = None 属性
 - 其余逻辑不变

 2. app/services/trade_backfill.py — 主要改动

 run() 方法重构：
 - 移除原来的 _fetch_all_signatures → reverse → _batch_parse_transactions 流程
 - 新增流程：
   a. 等待 sync_point 就绪（轮询 self.stream.sync_point）
   b. _fetch_all_signatures_before(sync_point) — 使用 before=sync_point 分页获取，上限 50,000 条
   c. _create_placeholders(signatures) — 遍历签名（从旧到新），创建占位记录
   d. _batch_parse_and_fill(signatures) — 获取 tx 详情，UPDATE 填充占位记录

 修改 _fetch_all_signatures → _fetch_all_signatures_before(sync_point)：
 - 使用 before=sync_point 参数调用 getSignaturesForAddress
 - 循环分页，每次 limit=1000，直到返回不足 1000 条或达到 50,000 条上限
 - 返回的签名就是从最早到 sync_point 之前的所有历史签名（RPC 返回从新到旧，需要 reverse）

 新增 _create_placeholders(signatures)：
 - 遍历签名（从旧到新），对每个 sig：
   - INSERT 占位记录：{sig, slot, block_time, source='rpc_fill'}，其他字段为空/null
 - 使用 INSERT OR IGNORE 防止重复
 - 返回实际创建的占位数量

 修改 _batch_parse_transactions → _batch_parse_and_fill：
 - 不再调用 _bulk_insert
 - 改为 _parse_transactions 获取完整 tx 详情后，对每条记录执行 UPDATE：
 UPDATE transactions SET from_address=?, to_address=?, amount=?, ..., source='solana_rpc'
 WHERE sig=? AND source='rpc_fill'
 - 只更新 source='rpc_fill' 的记录，不触碰 source='helius_ws'

 移除 _catch_up：
 - 新方案不需要补漏，WS 已经覆盖了所有实时数据

 3. app/routes/trades.py 和 main.py

 - 修改启动顺序：先 stream.start()，再 backfill.run()
 - 传递 stream 引用给 backfill 以便访问 sync_point

 4. app/services/trade_processor.py

 - calculate_metrics 改为按全量排序查询：
 .order_by(
     case((Transaction.source == 'rpc_fill', 0), else_=1),
     Transaction.id.asc()
 )
 - 新增 run_full_calculation(db, mint) 函数：
   - 按全量排序获取所有交易
   - 逐个调用 process_trade 计算指标
   - 完成后推送 DATA_READY 状态

 5. app/models/models.py

 - 无需修改，现有字段足够

 验证

 1. 启动服务，触发 POST /api/trades/{mint}/start
 2. 观察日志：TradeStream 先启动，记录 sync_point
 3. TradeBackfill 启动，创建占位记录，然后填充
 4. 查询 DB：验证全量排序 CASE WHEN source='rpc_fill' THEN 0 ELSE 1 END, id ASC 结果正确
 5. 验证 source='rpc_fill' 的记录 slot 严格递增，source='helius_ws' 的记录 slot 也严格递增
 6. 验证 Backfill 完成后收到 DATA_READY 状态推送
 7. 验证指数计算按全量排序顺序执行
 8. 运行 tests/test_sig_order.py
╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌

 Claude has written up a plan and is ready to execute. Would you like to proceed?

 ❯ 1. Yes, auto-accept edits
   2. Yes, manually approve edits
   3. Tell Claude what to change
      shift+tab to approve with this feedback
        其他都认可
      我认为应该 ，按照全量排序顺序 循环数据库的数据 ，ws  只需要填充数据库就可以了 ，不需要 WS 新数据插入时也触发 process_trade，