# 数据抓取与处理核心链路

本文档梳理「从 Helius 抓数据 → 解析 → 存储 → 计算指标 → 判定庄家」这条主链路上各模块的职责、调用关系和关键设计点。

> 创建于 2026-07-29：随测试期稳定后，下一步可选的重构方向是把 `ingestion/` 和 `processing/` 物理拆分到子目录（见文末"未来重构"章节）。

---

## 1. 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│ 入口：main.py → api_start_monitor                                     │
│   └─> stream.start()       (WS 实时流 task)                          │
│   └─> backfill.run()       (RPC 历史回填，等 sync_point)             │
└─────────────────────────────────────────────────────────────────────┘
            │                                │
            ▼                                ▼
┌──────────────────────────┐    ┌──────────────────────────────┐
│ 抓数据 (ingestion)        │    │ 抓数据 (ingestion)            │
│ trade_stream.py          │    │ trade_backfill.py            │
│ - WS 订阅 Helius         │    │ - RPC getTransactionsForAddr │
│ - 实时收 tx #1, #2, #3   │    │ - 拉 sync_point 之前的旧 tx  │
│ - 写入 txlist:ws:*       │    │ - 写入 txlist:rpc_fill:*     │
│ - 入内存队列             │    │                              │
└──────────┬───────────────┘    └──────────────┬───────────────┘
           │                                   │
           │ enqueue_trade                     │ run_full_calculation
           ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 处理数据 (processing)                                                  │
│                                                                       │
│   trade_processor.py  (指数计算 + 队列消费)                            │
│      ├─ _calculate_index()  核心：BUY/SELL 累加持仓 + 成本             │
│      ├─ get_trader_state_with_sig()  庄家判定统一入口                  │
│      ├─ enqueue_trade()  内存队列                                    │
│      ├─ _consumer_loop()  串行消费 WS 队列                            │
│      └─ run_full_calculation()  倒序消费 rpc_fill 集合                │
│                                                                       │
│   dealer_detector.py  (庄家判定 C002-C007)                            │
│      └─ get_trader_state()  C002-C005 本地判定 + C006 簇组判定         │
│                                                                       │
│   trade_tracer.py  (调试追踪)                                         │
│      └─ trace()  三步日志（WS收到 → Redis保存 → 入队指数计算）           │
└─────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 共享层                                                                │
│ tx_redis.py  Redis 操作（txlist 有序集合 + tx:{sig} 详情）            │
│ cluster/     簇组管理（与 dealer_detector 共享判定逻辑）                  │
│ settings_service.py  抓数据时读取的配置（helius_api_key 等）            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 模块清单

### 抓数据（ingestion）

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| [trade_stream.py](trade_stream.py) | `TradeStream.start()` / `_stream_loop()` / `_handle_message()` | WS 实时订阅、收 tx、解析、入队 |
| [trade_backfill.py](trade_backfill.py) | `TradeBackfill.run()` / `_fetch_all_transactions_before()` | RPC 拉历史 tx、解析、存 Redis |

### 处理数据（processing）

| 文件 | 核心函数 | 职责 |
|---|---|---|
| [trade_processor.py](trade_processor.py) | `_calculate_index()` | 单条 tx 指数计算（核心） |
| | `enqueue_trade()` | WS 实时 tx 入内存队列 |
| | `_consumer_loop()` | 串行消费 WS 队列 |
| | `run_full_calculation()` | 倒序消费 rpc_fill 全量计算 |
| | `get_trader_state_with_sig()` | 庄家判定 C002-C006 统一入口 |
| | `reset_processor()` | mint 切换时的清理 |
| [dealer_detector.py](dealer_detector.py) | `get_trader_state()` | 庄家判定 C002-C005 本地条件 + C006 簇组 |
| [trade_tracer.py](trade_tracer.py) | `trace()` | 调试日志追踪（生产环境可关） |

### 共享层

| 文件 | 职责 |
|---|---|
| [tx_redis.py](tx_redis.py) | Redis 读写：`txlist:rpc_fill:{mint}` / `txlist:ws:{mint}` / `tx:{sig}` |
| [cluster/](cluster/) | 簇组管理（detector、matcher、manager、route） |
| [settings_service.py](settings_service.py) | 抓数据时读取的全局配置 |

### 不在主链路

| 文件 | 原因 |
|---|---|
| `jupiter_service.py` | Jupiter 交易/兑换 API，与数据分析无关 |
| `app/admin/*` | SQLAdmin 后台 |
| `app/websocket/manager.py` | WS 连接管理（基础设施，不属于业务链路） |

---

## 3. 启动顺序与衔接

```
[T0]  main.py: api_start_monitor
      │  创建 TradeStream / TradeBackfill 实例
      │  active_monitors[mint] = {...}
      │
      ├───task A: stream.start()
      │        │  asyncio.create_task(_stream_loop())
      │        │  WS connect → 订阅 → 等待确认
      │        ▼
      │   _stream_loop:
      │     while running:
      │       msg = await ws.recv()
      │       _handle_message(msg)
      │         ├── 闸门（logMessages 子串匹配 BUY/SELL）
      │         ├── _extract_trade_info()    ← 解析
      │         ├── tx_redis.save_tx()        ← 存 tx:{sig}
      │         ├── tx_redis.add_tx_to_list("ws")  ← 入 txlist:ws:{mint}
      │         └── enqueue_trade(tx_detail)  ← ★ 入内存队列
      │
      └───task B: start_backfill_after()
               │  while not stream.running: sleep(0.2)  ← 等 WS 启动
               │  sync_point = stream.sync_point        ← WS 第一条 sig
               ▼
            backfill.run():
              _fetch_all_transactions_before(sync_point)
                分页拉 sig < sync_point 的旧 tx
                for tx in txs:
                  ├── 闸门（同样 BUY/SELL 子串匹配）
                  ├── _extract_trade_info()    ← 解析
                  ├── tx_redis.save_tx()        ← 存 tx:{sig}
                  └── tx_redis.add_tx_to_list("rpc_fill", score=total_saved)
              │
              ▼ _trigger_full_calculation()
              │
              ├── run_full_calculation(db, mint)
              │     │  rpc_sigs = reversed(txlist:rpc_fill:{mint})
              │     │  for sig in rpc_sigs:
              │     │    tx_detail = await tx_redis.get_tx(sig)
              │     │    await _calculate_index(tx_detail, mint, is_backfill=True)
              │     │      → get_trader_state_with_sig()  ← 庄家判定
              │     │      → 指数计算 + 持仓更新
              │     │      → 写 metrics:{mint} / user:{mint}:{addr}
              │     │    （不广播到 WS）
              │     └──► 一条 rpc_fill 计算完
              │
              └── start_consumer(mint)
                    │  启动 _consumer_loop
                    │  while True:
                    │    tx_detail = await _trade_queue.get()
                    │    await _calculate_index(tx_detail, mint)
                    │      → 庄家判定 + 指数计算 + 广播到 WS
                    │    ← 队列里的 WS tx 全部消化
```

**关键衔接点**：

1. **`sync_point` 衔接**：WS 第一条 sig = 回填拉取的截止点
   - WS 收 #1, #2, #3（最新）
   - 回填拉 #0, #-1, #-2, ...（sync_point 之前的旧 tx）
   - 两者集合不重叠，无重复计算

2. **内存队列衔接**：WS 收的 tx 立即入队（不立即计算），等 backfill 完成后消费者启动
   - `_trade_queue` 是模块全局单例，asyncio.Queue **无界**
   - `reset_processor()` 切换 mint 时清空

3. **全量计算 vs 消费者分工**：
   - `run_full_calculation` 读 `txlist:rpc_fill:{mint}`（旧 tx）
   - `_consumer_loop` 处理内存队列（WS 实时 tx）
   - 两者**不重复处理**

---

## 4. 关键设计点

### 4.1 三层 SOL 金额计算（trade_stream.py / trade_backfill.py）

```python
# 第 1 层：_resolve_trader_owner()  - 找对的人
# 第 2 层：SOL 余额差   (post - pre + fee + jito_tip) / 1e9
# 第 3 层：wSOL 余额差  - DEX 中转场景兜底
```

详见 [trade_stream.py#L595-682](trade_stream.py#L595-L682) `_resolve_trader_owner`。

### 4.2 脏数据 4 道防线

| 防线 | 位置 | 作用 |
|---|---|---|
| 1. BUY/SELL 闸门 | `if not (is_buy or is_sell): continue` | 过滤非买卖 tx |
| 2. `_extract_trade_info` 内 `isinstance` 守卫 | 处理非 dict 元素 | 防止解析崩溃 |
| 3. `_log_dirty_sample()` | 记录脏样本 main/inner ix | 诊断数据形状 |
| 4. `_log_unparseable_tx()` | 写入 `logs/unparseable_txs.jsonl` | 标记闸门通过但解析失败 |

### 4.3 幂等保护

`TradeStream.start()` 加了 `_task.done()` 检查，重复调用只 warning 不启动新 task。

### 4.4 共享 dict 启动入口

只有 `main.py → api_start_monitor` 一个入口可以启动 stream（`app/routes/trades.py` 的 `start_monitor` 已删除）。
`active_monitors` 只有一份定义，从 `app/routes/trades.py` 导入。

---

## 5. 故障排查清单

| 现象 | 排查方向 |
|---|---|
| WS 没收到 tx | 看 `trade_stream.py` `_stream_loop` 日志 |
| 解析失败 | `cat logs/unparseable_txs.jsonl \| jq -r .reason \| sort \| uniq -c` |
| 庄家判定不准 | `dealer_detector.py` 各种 C00X 条件 + `cluster/` 簇组 |
| 内存涨 | `tx:{sig}` 是否清理、`txlist:rpc_fill:{mint}` 单个 key 大小 |
| 重复计算 | 看 `_calculate_index` 是否被多次调用（同一 sig） |
| 队列卡住 | `_consumer_loop` 日志，看是否抛异常 |

---

## 6. 未来重构（可选）

如果团队规模扩大或新人理解成本上升，可考虑：

```
app/
├── pipeline/
│   ├── ingestion/
│   │   ├── stream.py          ← 原 trade_stream.py
│   │   └── backfill.py        ← 原 trade_backfill.py
│   ├── processing/
│   │   ├── indexer.py         ← 原 trade_processor.py
│   │   ├── dealer.py          ← 原 dealer_detector.py
│   │   └── tracer.py          ← 原 trade_tracer.py
│   ├── storage/
│   │   └── tx_redis.py        ← 原 tx_redis.py
│   └── cluster/               ← 原 cluster/
└── services/
    └── jupiter_service.py     ← 保持
```

**不推荐现在做**：
- 测试期间改物理路径会增加调试成本
- 当前 6-7 个核心文件命名已经清晰（`trade_*`、`dealer_*`）
- IDE 跳转、git log 都不受影响

建议先保留现状 1-2 个月，等所有改动稳定后再评估。
