111

3. 前端读取时排序（trades.py:55）：
  .order_by(Transaction.block_time.desc())

  整体流程是这样的：

  启动顺序（trades.py:126-153）

  1. 先启动回填引擎 TradeBackfill.run() — 异步任务
  2. 轮询等待回填完成 — start_stream_after_backfill() 每秒检查 backfill.running
  3. 回填结束后启动实时流 TradeStream.start()

  关键衔接点：

  # trades.py:146-151
  async def start_stream_after_backfill():
      while backfill.running:
          await asyncio.sleep(1)
      if backfill.running is False and stream.running is False:
          await stream.start()

  排序机制

  两者没有显式合并排序，而是通过 slot 作为统一时间轴 + 数据库去重 来保证完整性：

  1. 回填阶段（trade_backfill.py:79）：signatures.reverse() — 把从新到旧的签名翻转为从旧到新，然后按批次解析、入库。每条记录的
  slot 字段天然有序。
  2. 实时流阶段（trade_stream.py）：WS 推送的新交易也带 slot，同样写入同一张 Transaction 表。入库时用 sig
  做去重（trade_stream.py:151 查已有记录，trade_backfill.py:305 也查已有记录）。
  3. 前端读取时排序（trades.py:55）：
  .order_by(Transaction.block_time.desc())
  3. 按 block_time 倒序返回，所以回填的历史数据和实时流入的新数据在查询时自然拼在一起并排好序。

  补漏阶段（trade_backfill.py:419-444）

  回填完成后还有一个 _catch_up(start_slot) 阶段，重新拉取从回填起始 slot
  到现在可能遗漏的交易，确保回填和实时流之间的时间窗口没有空洞。

  总结

  ┌──────────┬──────────────────────────────────────────┬──────────────────────────┬──────────┐
  │   阶段   │                 数据来源                 │         排序依据         │ 去重方式 │
  ├──────────┼──────────────────────────────────────────┼──────────────────────────┼──────────┤
  │ 回填     │ getSignaturesForAddress → getTransaction │ reverse() 后从旧到新处理 │ sig 查库 │
  ├──────────┼──────────────────────────────────────────┼──────────────────────────┼──────────┤
  │ 补漏     │ 再次拉取增量签名                         │ 同上                     │ 同上     │
  ├──────────┼──────────────────────────────────────────┼──────────────────────────┼──────────┤
  │ 实时流   │ Helius WS 推送                           │ 按到达顺序               │ sig 查库 │
  ├──────────┼──────────────────────────────────────────┼──────────────────────────┼──────────┤
  │ 前端展示 │ SELECT ... ORDER BY block_time DESC      │ block_time               │ —        │
  └──────────┴──────────────────────────────────────────┴──────────────────────────┴──────────┘

  核心设计是：不维护内存中的合并队列，而是以数据库为唯一存储，用 slot/block_time 作为全局时间轴，查询时统一排序。