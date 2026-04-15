# GMGN 交易系统 - 运转流程文档

## 一、系统概述

本系统是一个基于 Solana 链上数据的代币交易监控与分析平台。通过 Helius RPC 服务，实现对指定代币 mint 地址的**实时交易订阅**和**历史交易回填**，并对交易数据进行类型判定（BUY/SELL/TRANSFER）和指标计算。

## 二、技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| ORM | SQLAlchemy |
| 数据库 | SQLite (gmgn.db) |
| WebSocket | websockets 库 |
| HTTP 客户端 | httpx |
| 管理后台 | SQLAdmin |
| 前端模板 | Jinja2 HTML |

## 三、项目结构

```
fastapiGmgn/
├── main.py                          # 应用入口，注册路由/SQLAdmin/启动逻辑
├── requirements.txt                 # Python 依赖
├── gmgn.db                          # SQLite 数据库
├── logs/app.log                     # 日志（10MB 轮转）
├── app/
│   ├── models/
│   │   ├── models.py               # User, Transaction, Token 数据库模型
│   │   └── trade.py                # Setting, TradeAnalysis 数据库模型
│   ├── routes/
│   │   ├── auth.py                 # 认证（注册/登录/me）
│   │   ├── users.py                # 用户管理
│   │   └── trades.py               # 交易监控 REST + WebSocket
│   ├── schemas/
│   │   ├── schemas.py              # 用户/Token/Transaction Pydantic schema
│   │   └── trade.py                # 交易/监控/设置/指标 Pydantic schema
│   ├── services/
│   │   ├── settings_service.py     # 系统设置 CRUD（键值对存储）
│   │   ├── trade_stream.py         # 实时交易流（Helius WebSocket 订阅）
│   │   ├── trade_backfill.py       # 历史交易回填（RPC 分页拉取）
│   │   └── trade_processor.py      # 交易处理 + 全量计算 + 指标计算
│   ├── templates/
│   │   ├── trade_monitor.html      # 交易监控前端页面
│   │   └── settings.html           # 系统设置页面
│   ├── utils/
│   │   └── database.py             # SQLAlchemy 引擎/会话/迁移
│   └── websocket/
│       └── manager.py              # WebSocket 连接管理器（单例）
```

## 四、数据库模型

### 4.1 核心表

**transactions（交易记录表）**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 自增主键 |
| sig | String (unique) | 交易签名 |
| slot | Integer | Solana slot 号 |
| block_time | DateTime | 区块时间戳 |
| from_address | String | 发送方（signer） |
| to_address | String | 接收方（BUY 时填充） |
| amount | Float | Token 变化绝对值 |
| token_mint | String | 代币 mint 地址 |
| token_symbol | String | 代币符号（暂未填充） |
| transaction_type | String | BUY / SELL / TRANSFER |
| dex | String | pump.fun / raydium / orca |
| pool_address | String | 池地址（暂未填充） |
| sol_spent | Float | SOL 净值支出（pre - post - fee） |
| fee | Float | 交易费（SOL） |
| raw_data | Text | 原始数据前 5000 字符 |
| source | String | **rpc_fill**（回填）/ **helius_ws**（实时） |
| created_at / updated_at | DateTime | 时间戳 |

**trade_analysis（交易分析表）**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 自增主键 |
| sig | String (unique) | 关联 transactions.sig |
| net_sol_flow | Float | SOL 净流入 |
| net_token_flow | Float | Token 净流入 |
| price_per_token | Float | 单价 |
| wallet_tag | String | 钱包标记 |
| processed_at | DateTime | 处理时间 |

**settings（系统设置表）**
| 字段 | 类型 | 说明 |
|------|------|------|
| key | String (PK) | 设置键名 |
| value | Text | 设置值 |
| description | Text | 说明 |

关键设置项：`batch_size`(默认100)、`concurrent_requests`(默认3)、`request_interval`(默认0.5)、`helius_api_key`

## 五、核心运转流程

### 5.1 启动监听流程

```
用户 POST /api/trades/{mint}/start
        │
        ├── 1. 创建 TradeStream(mint, api_key)
        │   └── 创建 TradeBackfill(db, mint, stream=TradeStream实例)
        │
        ├── 2. asyncio.create_task(stream.start())
        │   └── TradeStream 连接 Helius WebSocket
        │       wss://mainnet.helius-rpc.com?api-key={key}
        │       发送 transactionSubscribe 订阅 {accountInclude: [mint]}
        │
        └── 3. asyncio.create_task(start_backfill_after_stream())
            └── 等待 stream.running == True 后启动 backfill.run()
```

### 5.2 TradeStream 实时流

```
[TradeStream]
    │
    ├── WebSocket 连接 Helius
    ├── 收到第一条实时交易
    │   ├── 设置 self.sync_point = signature（回填用）
    │   ├── 解析交易（资金流向优先协议）
    │   ├── 去重入库（source="helius_ws"）
    │   ├── 调用 process_trade() 创建 TradeAnalysis
    │   └── ws_manager.broadcast 推送 type="trade" 到前端
    │
    └── 持续接收新交易，断线自动重连（间隔3秒）
```

### 5.3 TradeBackfill 回填流程（接力模式）

```
[TradeBackfill]
    │
    ├── 阶段1: WAITING_SYNC
    │   └── 轮询等待 TradeStream.sync_point 就绪（每0.5秒检查）
    │       如果收到 stop() 信号则中断退出
    │
    ├── 阶段2: FETCHING_HISTORY
    │   └── 使用 before=sync_point 分页获取历史签名
    │       RPC: getSignaturesForAddress
    │       每批 1000 条，上限 50000 条
    │       从新到旧排列
    │
    ├── 阶段3: 占位创建
    │   └── 反转签名列表（从旧到新）
    │       INSERT OR IGNORE 创建占位记录
    │       只存 sig + slot + block_time, source="rpc_fill"
    │
    ├── 阶段4: FILLING
    │   └── 按 batch_size 分组，并发调用 getTransaction 解析
    │       成功：UPDATE 填充占位记录
    │       失败（meta.err / 超时等）：DELETE 删除占位记录
    │       每批完成后推送进度到前端
    │
    ├── 阶段5: DATA_READY
    │   └── 回填完成，推送状态
    │
    └── 阶段6: CALCULATION_DONE
        └── 调用 run_full_calculation()
            按全量排序（rpc_fill 在前，helius_ws 在后，各自 id ASC）
            逐条处理未处理的交易，创建 TradeAnalysis
```

### 5.4 状态机

```
IDLE → WAITING_SYNC → FETCHING_HISTORY → FILLING → DATA_READY → CALCULATION_DONE → STREAMING
```

各状态通过 `ws_manager.broadcast(mint, {"type": "status", "data": {...}})` 推送到前端。

### 5.5 停止监听

```
用户 POST /api/trades/{mint}/stop
        │
        ├── backfill.stop() → self.running = False
        │   └── _wait_for_sync_point 检测到 running=False 退出循环
        │   └── _fetch_all_signatures_before 检测到 running=False 退出循环
        │
        └── stream.stop() → self.running = False
            └── WebSocket 连接关闭，asyncio.Task 取消
```

## 六、交易类型判定（资金流向优先协议）

两个解析器（TradeStream._extract_trade_info 和 TradeBackfill._extract_trade_info）使用相同的判定逻辑：

### 6.1 SOL 净值计算

```
sol_spent = (preBalances[0] - postBalances[0] - fee) / 1e9
```

扣除 fee 后的净值，避免手续费干扰判断。

### 6.2 Token 余额计算

遍历 `postTokenBalances` 数组，找 `mint == 监控代币 AND owner == signer` 的项：

```
pre_amt = signer 在该 mint 下的 preTokenBalance（无则为 0）
post_amt = signer 在该 mint 下的 postTokenBalance（无则为 0）
delta = post_amt - pre_amt
amount = abs(delta)
```

此方式解决了新 ATA 创建时 preBalance 为空导致数量为 0 的问题。

### 6.3 类型判定规则

| 条件 | 类型 |
|------|------|
| `delta > 0` | **BUY**（signer 的 Token 增加 = 买入） |
| `delta < 0` | **SELL**（signer 的 Token 减少 = 卖出） |
| `delta == 0` 且 `abs(sol_spent) <= 0.001` | **TRANSFER**（Token 无变化，SOL 变化极小） |

### 6.4 DEX 检测

通过 inner instructions 的 programId 匹配：
- `pump` 或 `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` → pump.fun
- `raydium` 或 `675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8` → raydium
- `orca` → orca

## 七、错误处理

### 7.1 TradeStream（实时流）

- WebSocket 断线：等待 3 秒后自动重连
- meta.err 交易：提前检查，跳过不入库
- 解析异常：记录 warning，跳过该交易
- 入库冲突（重复 sig）：跳过

### 7.2 TradeBackfill（回填）

- sync_point 等待：持续轮询，不超时放弃，仅在 stop() 时中断
- getTransaction 失败：标记 `_error=True`，后续删除占位记录
- meta.err 交易：标记 `_error=True`，删除占位记录
- 数据库冲突：rollback 后继续下一条

## 八、前端数据展示

### 8.1 交易列表排序

```sql
ORDER BY
  CASE WHEN source = 'rpc_fill' THEN 0 ELSE 1 END,  -- 回填数据在前
  slot DESC,
  block_time DESC,
  id DESC
```

确保回填的历史数据先显示完，再显示实时流数据。

### 8.2 WebSocket 推送

前端通过 `/api/trades/ws/{mint}` 接收实时推送：

| type | 触发时机 |
|------|----------|
| `status` | 状态变更（WAITING_SYNC / FETCHING_HISTORY / FILLING / DATA_READY / CALCULATION_DONE / STREAMING） |
| `trade` | 新交易入库并处理完成 |
| `error` | 发生异常 |

### 8.3 API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/trades/{mint}?page=&page_size=` | 分页获取交易列表 |
| `GET /api/trades/{mint}/metrics` | 获取四指标（current_bet, current_cost, realized_profit, trade_count） |
| `GET /api/trades/{mint}/status` | 获取当前监控状态 |
| `POST /api/trades/{mint}/start` | 启动监控 |
| `POST /api/trades/{mint}/stop` | 停止监控 |

## 九、组件交互关系

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                              │
│  注册路由 → SQLAdmin → WebSocket端点 → 初始化设置/管理员      │
└──────────────────┬──────────────────────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    ▼              ▼              ▼
┌────────┐  ┌───────────┐  ┌────────────┐
│ trades │  │   auth    │  │  settings  │
│ routes │  │  routes   │  │   routes   │
└───┬────┘  └───────────┘  └────────────┘
    │
    ├── POST /start
    │   ├── TradeStream ────WS连接──→ Helius
    │   │       │                       │
    │   │       │ sync_point            │
    │   │       ▼                       │
    │   └── TradeBackfill ──RPC────→ Helius
    │           │                       │
    │           ▼                       │
    │   └── TradeProcessor ──→ TradeAnalysis 表
    │
    ├── GET /{mint} ──→ transactions 表（排序查询）
    ├── GET /{mint}/metrics ──→ calculate_metrics()
    └── WS /ws/{mint} ──→ ws_manager.broadcast
                                │
                                ▼
                          前端 WebSocket 连接
```

## 十、关键设计决策

1. **接力模式**：先起实时流，回填基于实时流的第一条交易（sync_point）作为分界点，避免数据遗漏或重复
2. **占位+填充**：回填先创建占位记录再批量填充，保证数据按从旧到新的顺序入库
3. **source 区分**：`rpc_fill`（回填）和 `helius_ws`（实时）通过 source 字段区分，排序时回填数据优先显示
4. **资金流向优先**：交易类型判定结合 SOL 净值和 Token 变化双重校验，避免单一指标误判
5. **错误 sig 清理**：回填阶段解析失败的交易，其占位记录会被删除，不留脏数据
