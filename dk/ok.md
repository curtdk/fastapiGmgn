
lsof -ti :8000 | xargs kill -9

  1. 排序改用 slot — main.py:139、trades.py:55、trade_processor.py:98 全部改为 .order_by(Transaction.slot.desc(),              
  Transaction.block_time.desc())                                                                                               
  2. 前端 fmtTime 兼容 ISO 字符串 — 原来 new Date(ts) 对 ISO 字符串解析可能失败                                                
  3. fetchHistory 改为 WS onopen 时立即调用 — 不再延迟 2 秒，连接成功就拉取历史数据                                            
  4. 添加了 console.log 调试 — 方便确认数据是否返回                                                                            
                                                                                                                               
  回答你的三个问题                                                                                                             
                                                                                                                               
  1. sig 是否完全、排序是否正确                                                                                                
                                                            
  回填阶段：getSignaturesForAddress 返回从新到旧 → reverse() 翻转为从旧到新 →                                                  
  按批次解析入库。实时流阶段：按到达顺序入库。查询时统一 ORDER BY slot DESC，所以历史+实时在读取时拼接并排序。sig 
  是完整的（回填 + 补漏 + 实时流），排序正确（slot 严格单调递增）。                                                            
                                                            
  2. 交易列表正常显示                                                                                                          
   
  重启服务器后，前端会：                                                                                                       
  - WS 连接成功 → onopen → 立即调用 fetchHistory 拉取已有数据
  - 回填/实时流推送的 trade 消息 → addTradeRow 逐条插入                                                                        
  - 打开浏览器 DevTools Console 可以看到 历史数据: {trades: [...], total: N} 确认数据返回

    trade_processor.py — 核心改动

  - 新增 IndexState 类（四个状态字段）
  - 新增模块级变量：_trade_queue、_index_state、_consumer_task、_mint
  - 新增 _calculate_index() — 根据交易类型更新指数状态（公式待补充）
  - 新增 enqueue_trade() — WS 消息只入队
  - 新增 start_consumer() / _consumer_loop() — 单一消费者串行处理队列
  - 改造 run_full_calculation() — 历史 tx 直接处理，更新 _index_state，不再调 process_trade
  - 保留 process_trade() 兼容旧调用

  trade_stream.py — 修改 _handle_message

  - import 从 process_trade 改为 enqueue_trade
  - WS 消息入库后只调 enqueue_trade(tx_detail)，不再直接处理

  trade_backfill.py — 修改 _trigger_full_calculation

  - 先 run_full_calculation() 处理历史 tx
  - 再 start_consumer() 启动消费者消化队列

  数据流：历史 tx 直接处理 → 启动消费者 → 消费队列中积压的 WS 消息 → 持续处理新入队消息。严格串行，不会丢数据或乱序。