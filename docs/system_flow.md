# GMGN 交易监控系统运行流程

## 一、项目概述

GMGN 是一个基于 FastAPI 的 Solana 代币交易监控系统，核心功能：
- 实时监控指定代币（mint）的链上交易
- 回填历史交易数据
- 计算交易指标（BUY/SELL/TRANSFER）
- 识别庄家（Dealer）地址
- 通过 WebSocket 实时推送交易数据

## 二、系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         FastAPI 应用                            │
├─────────────────────────────────────────────────────────────────┤
│  路由层 (routes)                                                │
│  ├── /api/auth - 用户认证                                       │
│  ├── /api/users - 用户管理                                      │
│  ├── /api/trades - 交易监控 API                                 │
│  │     ├── GET  /{mint}      - 获取交易列表                     │
│  │     ├── GET  /{mint}/metrics - 获取指标                      │
│  │     ├── GET  /{mint}/status - 获取监听状态                   │
│  │     ├── POST /{mint}/start  - 启动监听                      │
│  │     ├── POST /{mint}/stop   - 停止监听                      │
│  │     └── WS   /ws/{mint}    - WebSocket 实时推送             │
│  └── /api/settings - 系统设置                                   │
├─────────────────────────────────────────────────────────────────┤
│  服务层 (services)                                              │
│  ├── trade_stream.py    - 实时交易流（Helius WebSocket）        │
│  ├── trade_backfill.py  - 历史交易回填（Helius RPC）            │
│  ├── trade_processor.py - 交易处理引擎（指数计算、队列消费）     │
│  ├── dealer_detector.py - 庄家地址检测                          │
│  └── settings_service.py - 设置管理                             │
├─────────────────────────────────────────────────────────────────┤
│  WebSocket 管理器 (websocket/manager.py)                        │
│  └── 管理所有客户端连接，按 mint 广播消息                         │
├─────────────────────────────────────────────────────────────────┤
│  数据层 (models + database)                                     │
│  ├── Transaction  - 交易记录表                                  │
│  ├── TradeAnalysis - 交易分析表（指标计算结果）                 │
│  ├── Token        - 代币信息表                                  │
│  ├── User         - 用户表                                      │
│  └── Setting      - 系统设置表                                  │
└─────────────────────────────────────────────────────────────────┘
```

## 三、启动流程

### 1. 应用启动 (main.py)

```
python main.py
     │
     ▼
┌────────────────────────────────────────┐
│ 1. 配置日志 (RotatingFileHandler)      │
│ 2. 创建数据库表 (Base.metadata.create_all)│
│ 3. 迁移 transactions 表                │
│ 4. 创建 FastAPI 实例                    │
│ 5. 添加中间件 (Session, CORS)           │
│ 6. 注册路由                            │
│ 7. 配置 SQLAdmin 后台                  │
│ 8. 初始化超级管理员 (admin/admin123)    │
│ 9. 初始化默认设置                       │
│ 10. 启动事件 → 初始化庄家检测服务        │
└────────────────────────────────────────┘
     │
     ▼
┌────────────────────────────────────────┐
│ 启动完成，等待请求                       │
│ 访问 http://localhost:8000/admin        │
│ 自动重定向到 /admin/trade-monitor       │
└────────────────────────────────────────┘
```

## 四、交易监听启动流程

当用户在前端点击「开始监听」时：

```
POST /api/trades/{mint}/start
          │
          ▼
┌──────────────────────────────────────────────────────┐
│ 1. 停止该 mint 的现有监听（如果存在）                  │
│ 2. 创建 TradeStream 实例（Helius WebSocket）         │
│ 3. 创建 TradeBackfill 实例（历史回填）               │
│ 4. 保存到 active_monitors[mint]                      │
│ 5. 异步启动 TradeStream.start()                      │
│ 6. 异步启动 TradeBackfill.run()（等待 sync_point）   │
└──────────────────────────────────────────────────────┘
```

## 五、实时交易流 (TradeStream)

```
TradeStream.start()
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 连接 Helius WebSocket                                    │
│ wss://mainnet.helius-rpc.com?api-key=xxx                │
│                                                          │
│ 发送订阅请求:                                             │
│ {                                                       │
│   "method": "transactionSubscribe",                    │
│   "params": [{"accountInclude": [mint]}]               │
│ }                                                        │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 持续接收交易通知 (transactionNotification)             │
│                                                          │
│ 每条消息处理:                                             │
│ 1. 解析交易数据（signature, slot, meta）               │
│ 2. 跳过失败的交易 (meta.err)                           │
│ 3. 提取关键信息:                                        │
│    - from_address (signer)                             │
│    - SOL 流向 (sol_spent)                              │
│    - Token 余额变化 (BUY/SELL/TRANSFER)                │
│    - DEX 检测 (pump.fun/raydium/orca)                  │
│ 4. 写入 Transaction 表                                  │
│ 5. 记录第一条 WS 交易作为 sync_point                   │
│ 6. 调用 enqueue_trade() 入队                           │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ WebSocket 广播                                          │
│ ws_manager.broadcast(mint, {                            │
│   "type": "status",                                    │
│   "data": {"status": "STREAMING", ...}                │
│ })                                                      │
└────────────────────────────────────────────────────────┘
```

## 六、历史交易回填 (TradeBackfill)

```
TradeBackfill.run()
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 等待 TradeStream 的 sync_point 就绪                    │
│ （第一条 WS 交易的 signature）                          │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 使用 getTransactionsForAddress 分页获取历史交易         │
│                                                          │
│ 请求参数:                                                 │
│ {                                                       │
│   "method": "getTransactionsForAddress",               │
│   "params": [                                           │
│     mint,                                               │
│     {                                                   │
│       "filters": {"signature": {"lt": sync_point}},    │
│       "transactionDetails": "full"                     │
│     }                                                   │
│   ]                                                     │
│ }                                                        │
│                                                          │
│ 使用 paginationToken 分页，最多获取 50000 条            │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 批量入库（按 batch_size 分组）                          │
│                                                          │
│ 每批处理:                                                │
│ 1. 去重检查（检查 sig 是否存在）                        │
│ 2. INSERT Transaction                                  │
│ 3. 广播进度: {"status": "FILLING", "progress": xx%}   │
│ 4. 批次间隔 (request_interval)                          │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ 回填完成 → 触发全量计算                                 │
│                                                          │
│ 1. run_full_calculation() - 遍历历史交易                │
│    - 庄家判定 + 指数计算                                │
│    - 写入 TradeAnalysis 表                              │
│    - 非庄家交易广播到前端                               │
│                                                          │
│ 2. start_consumer() - 启动消费者                        │
│    - 消化队列中积压的 WS 消息                           │
│    - 实时处理新交易                                     │
└────────────────────────────────────────────────────────┘
```

## 七、交易处理引擎 (TradeProcessor)

### 7.1 队列消费模式

```
实时流收到 WS 消息
      │
      ▼
enqueue_trade(tx_detail)
      │
      ▼
放入 asyncio.Queue 队列
      │
      ▼
消费者循环 (_consumer_loop)
      │
      ▼
┌────────────────────────────────────────────────────┐
│ 1. 从队列取出交易                                   │
│ 2. 去重检查（TradeAnalysis）                       │
│ 3. 庄家判定 (check_and_classify_user)              │
│ 4. 指数计算 (更新 IndexState)                       │
│ 5. 写入 TradeAnalysis 表                           │
│ 6. 非庄家交易 → WebSocket 广播                     │
│ 7. 庄家交易 → 仅记录，不广播                       │
└────────────────────────────────────────────────────┘
```

### 7.2 指数状态 (IndexState)

```python
class IndexState:
    current_bet: float = 0.0       # 本轮下注（累计 BUY 的 SOL）
    dealer_profit: float = 0.0     # 庄家利润
    realized_profit: float = 0.0   # 已落袋
    unrealized_pnl: float = 0.0    # 浮盈浮亏
```

### 7.3 交易类型判定

| Token 变化 | SOL 变化 | 类型 |
|-----------|---------|------|
| delta > 0 | - | BUY |
| delta < 0 | - | SELL |
| delta = 0 | abs(sol_spent) <= 0.001 | TRANSFER |

## 八、庄家检测 (DealerDetector)

```
check_and_classify_user(address, mint, sig)
      │
      ▼
┌────────────────────────────────────────────────────┐
│ 1. 查 Redis 缓存                                    │
│    - 命中 → 直接返回 (dealer/retail/unknown)        │
│    - 未命中 → 继续                                  │
│                                                          │
│ 2. 分析交易历史                                       │
│    - 获取该 address 在该 mint 上的所有交易           │
│    - 统计累计 BUY/SELL 量                           │
│    - 判断是否为早期买入者                            │
│                                                          │
│ 3. 判定规则                                          │
│    - 早期买入 + 高比例 → dealer                      │
│    - 普通参与者 → retail                            │
│    - 其他 → unknown                                 │
│                                                          │
│ 4. 写入 Redis 缓存                                  │
└────────────────────────────────────────────────────┘
```

## 九、WebSocket 通信

### 9.1 连接建立

```
客户端连接: ws://localhost:8000/api/trades/ws/{mint}
      │
      ▼
ws_manager.connect(websocket, mint)
      │
      ▼
发送初始状态: {"type": "status", "data": {...}}
```

### 9.2 消息类型

| type | 说明 | data |
|------|------|------|
| `status` | 状态通知 | mint, status, message, progress |
| `trade` | 交易推送 | 完整交易详情 + 指标 |
| `error` | 错误通知 | mint, message |
| `ping/pong` | 心跳 | - |

### 9.3 状态流转

```
IDLE → WAITING_SYNC → FETCHING_HISTORY → FILLING → 
DATA_READY → CALCULATION_DONE → STREAMING
```

## 十、停止监听流程

```
POST /api/trades/{mint}/stop
      │
      ▼
┌────────────────────────────────────────────────────┐
│ 1. TradeBackfill.stop() - 停止回填                  │
│ 2. TradeStream.stop()  - 停止实时流                 │
│ 3. reset_processor()   - 清理状态:                  │
│    - 停止消费者任务（发送毒丸）                      │
│    - 清空队列                                       │
│    - 重置全局变量                                   │
│    - 删除 DB 中该 mint 的记录                       │
│ 4. 从 active_monitors 删除                          │
└────────────────────────────────────────────────────┘
```

## 十一、数据流向图

```
┌──────────────────────────────────────────────────────────────────┐
│                        Helius RPC/WSS                            │
│                        (外部数据源)                                │
└──────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           │                                      │
           ▼                                      ▼
┌─────────────────────┐                ┌─────────────────────┐
│   TradeStream       │                │   TradeBackfill     │
│   (实时流)           │                │   (历史回填)         │
│   - WS 订阅          │                │   - RPC 分页        │
│   - 实时解析         │                │   - 批量入库        │
│   - 入队             │                │   - 全量计算        │
└─────────────────────┘                └─────────────────────┘
           │                                      │
           └──────────────────┬──────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   asyncio.Queue     │
                    │   (交易队列)         │
                    └─────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     TradeProcessor                               │
│  ┌────────────────┐    ┌────────────────┐    ┌──────────────┐  │
│  │ 消费者循环      │    │ 庄家检测       │    │ 指数计算     │  │
│  │ _consumer_loop │───▶│ DealerDetector │    │ IndexState   │  │
│  └────────────────┘    └────────────────┘    └──────────────┘  │
└──────────────────────────────────────────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           │                                      │
           ▼                                      ▼
┌─────────────────────┐                ┌─────────────────────┐
│   Transaction 表    │                │   TradeAnalysis 表   │
│   (原始交易数据)     │                │   (分析结果)         │
└─────────────────────┘                └─────────────────────┘
           │                                      │
           └──────────────────┬──────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                       WebSocket 广播                            │
│              ws_manager.broadcast(mint, message)                │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────────┐
                    │   前端 Dashboard    │
                    │   (实时展示)         │
                    └─────────────────────┘
```

## 十二、API 端点汇总

### 交易监控

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trades/{mint}` | 获取交易列表（分页） |
| GET | `/api/trades/{mint}/metrics` | 获取四个核心指标 |
| GET | `/api/trades/{mint}/status` | 获取监听状态 |
| POST | `/api/trades/{mint}/start` | 启动监听 |
| POST | `/api/trades/{mint}/stop` | 停止监听 |
| WS | `/api/trades/ws/{mint}` | WebSocket 实时推送 |

### 设置管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings/` | 获取所有设置 |
| PUT | `/api/settings/{key}` | 更新设置 |

### 管理后台

| 路径 | 说明 |
|------|------|
| `/admin` | SQLAdmin 管理界面 |
| `/admin/trade-monitor` | 交易监控页面 |
| `/admin/settings-page` | 设置页面 |

## 十三、关键配置

### 环境变量 / 数据库设置

| 设置项 | 默认值 | 说明 |
|--------|--------|------|
| `helius_api_key` | - | Helius API 密钥（必需） |
| `batch_size` | 100 | 批量入库大小 |
| `request_interval` | 0.5 | 请求间隔（秒） |

### DEX 识别

- **pump.fun**: `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`
- **Raydium**: `675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8`

### 已知 Jito 小费地址

内置 10 个常见 Jito 小费账户地址，用于从 sol_spent 中扣除小费，避免统计虚高。

## 十四、错误处理

1. **WebSocket 断开**: 自动重连，回填任务继续
2. **RPC 请求失败**: 重试 + 指数退避
3. **数据库错误**: 回滚 + 日志记录
4. **解析失败**: 跳过该交易 + 警告日志

## 十五、日志输出

```
[实时流] 启动: {mint}
[实时流] sync_point 已设置: {signature}
[回填] sync_point 已就绪: {signature}
[回填] 已获取 {count} 条有效交易...
[回填] 批次 {n}/{total}: 插入 {n} 条，跳过 {n} 条
[回填] 入库进度 {progress}%
[回填] 回填完成，共 {count} 条历史数据
[全量计算] 开始计算 mint={mint}
[全量计算] 共 {count} 条交易待处理
[全量计算] 完成 mint={mint}
[消费者] 已启动 mint={mint}
[消费者] {sig}... 完成 user={status}
[WS] 新连接: mint={mint}, 当前连接数={n}
[WS] 断开连接: mint={mint}
```

## 十六、启动命令

```bash
# 安装依赖
pip install -r requirements.txt

# 启动应用
python main.py

# 访问地址
# - API 文档: http://localhost:8000/docs
# - 管理后台: http://localhost:8000/admin
# - 交易监控: http://localhost:8000/admin/trade-monitor

# 默认管理员账号
# 用户名: admin
# 密码: admin123
```

## 十七、流程总结

```
用户操作                      系统响应
─────────────────────────────────────────────────────────
1. 访问 /admin/trade-monitor  → 显示监控页面
2. 输入 mint 地址             → 等待用户点击开始
3. 点击「开始监听」            → POST /api/trades/{mint}/start
4. 系统启动实时流              → Helius WebSocket 订阅
5. 系统获取 sync_point        → 第一条 WS 交易签名
6. 系统回填历史交易            → getTransactionsForAddress 分页
7. 系统计算历史指标            → run_full_calculation
8. 系统启动消费者              → 处理实时队列
9. 前端收到实时推送            → 展示交易列表和指标
10. 点击「停止监听」           → POST /api/trades/{mint}/stop
11. 系统清理所有状态            → reset_processor
```

## 十八、文件结构

```
fastapiGmgn/
├── main.py                    # 应用入口，路由注册，Admin 配置
├── requirements.txt          # 依赖列表
├── app/
│   ├── __init__.py
│   ├── routes/               # 路由层
│   │   ├── __init__.py
│   │   ├── auth.py          # 认证路由
│   │   ├── users.py         # 用户路由
│   │   └── trades.py        # 交易监控路由 + WebSocket
│   ├── services/            # 服务层
│   │   ├── __init__.py
│   │   ├── trade_stream.py   # 实时交易流
│   │   ├── trade_backfill.py # 历史回填
│   │   ├── trade_processor.py # 交易处理引擎
│   │   ├── dealer_detector.py # 庄家检测
│   │   └── settings_service.py # 设置管理
│   ├── models/              # 数据模型
│   │   ├── __init__.py
│   │   ├── models.py       # Transaction, Token, User
│   │   └── trade.py        # TradeAnalysis, Setting
│   ├── schemas/             # Pydantic 模型
│   │   ├── __init__.py
│   │   ├── schemas.py
│   │   └── trade.py
│   ├── websocket/          # WebSocket 管理
│   │   ├── __init__.py
│   │   └── manager.py      # 连接管理器
│   ├── utils/               # 工具函数
│   │   ├── __init__.py
│   │   └── database.py     # 数据库连接
│   ├── templates/           # HTML 模板
│   │   ├── trade_monitor.html
│   │   └── settings.html
│   └── admin/               # Admin 页面
│       └── pages.py
├── docs/
│   └── system_flow.md      # 本文档
└── tests/
    └── test_sig_order.py   # 测试用例
```
