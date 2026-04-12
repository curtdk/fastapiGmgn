# fastapiGmgn 代码说明文档

## 一、项目概述

fastapiGmgn 是一个基于 FastAPI 的 Solana 链上交易监控系统，主要功能：
- 实时监控指定 Token 的交易活动
- 历史交易数据回填
- WebSocket 实时推送交易到前端
- SQLAdmin 管理后台

## 二、项目结构

```
fastapiGmgn/
├── main.py                      # 主应用入口
├── requirements.txt            # 依赖
├── app/
│   ├── models/
│   │   ├── models.py           # 数据模型 (User, Transaction, Token)
│   │   └── trade.py            # 交易相关模型 (Setting, TradeAnalysis)
│   ├── routes/
│   │   ├── auth.py             # 认证 (注册/登录/JWT)
│   │   ├── users.py            # 用户管理
│   │   └── trades.py           # 交易路由
│   ├── services/
│   │   ├── trade_stream.py     # 实时流服务 (Helius WebSocket)
│   │   ├── trade_backfill.py   # 历史回填服务 (Helius RPC)
│   │   ├── trade_processor.py  # 交易处理引擎
│   │   ├── settings_service.py # 设置管理
│   │   └── trade_backfill.py   # 回填服务
│   ├── websocket/
│   │   └── manager.py          # WebSocket 连接管理
│   ├── utils/
│   │   └── database.py         # 数据库连接
│   └── templates/
│       ├── trade_monitor.html  # 交易监控页面
│       └── settings.html       # 设置页面
└── logs/                        # 日志目录
```

## 三、点击"开始"后的完整调用链

### 3.1 前端触发

前端点击"开始"按钮 → 发送 POST 请求到 `/admin/api/start`

### 3.2 后端入口：`main.py` → `api_start_monitor`

**文件**: `main.py` 第 169-194 行

```python
@expose("/api/start", methods=["POST"])
async def api_start_monitor(self, request: Request):
    # 1. 获取 mint 参数
    mint = form.get("mint", "")
    
    # 2. 检查是否已有运行中的监控
    if mint in active_monitors:
        # 停止旧的监控
    
    # 3. 创建回填和流服务实例
    backfill = TradeBackfill(db=db, mint=mint)
    stream = TradeStream(mint=mint, api_key=api_key)
    
    # 4. 保存到 active_monitors
    active_monitors[mint] = {"backfill": backfill, "stream": stream}
    
    # 5. 启动回填任务（异步）
    asyncio.create_task(backfill.run())
    
    # 6. 启动流任务（等待回填完成后）
    asyncio.create_task(start_stream_after())
```

**关键断点位置**:
- `main.py:169` - `api_start_monitor` 入口
- `main.py:179` - 创建 TradeBackfill 实例
- `main.py:180` - 创建 TradeStream 实例
- `main.py:185` - `asyncio.create_task(backfill.run())`

### 3.3 第一阶段：历史回填 `TradeBackfill.run()`

**文件**: `app/services/trade_backfill.py` 第 24-100 行

```python
async def run(self):
    # 1. 获取当前 slot 作为分界点
    current_slot = await self._get_current_slot()
    
    # 2. 分页获取所有历史签名
    signatures = await self._fetch_all_signatures()
    
    # 3. 批量解析交易详情
    await self._batch_parse_transactions(signatures)
    
    # 4. 补漏阶段
    await self._catch_up(current_slot)
    
    # 5. 回填完成，running = False，触发流启动
```

**关键方法**:
- `_get_current_slot()` - 获取当前链上 slot
- `_fetch_all_signatures()` - 获取该 mint 的所有交易签名
- `_batch_parse_transactions()` - 并发批量获取交易详情
- `_bulk_insert()` - 批量入库

**断点位置**:
- `trade_backfill.py:24` - `run()` 入口
- `trade_backfill.py:36` - 获取当前 slot
- `trade_backfill.py:46` - 获取签名列表
- `trade_backfill.py:60` - 批量解析
- `trade_backfill.py:70` - 补漏阶段

### 3.4 第二阶段：实时流 `TradeStream.start()`

**文件**: `app/services/trade_stream.py` 第 24-40 行

```python
async def start(self):
    self.running = True
    self._task = asyncio.create_task(self._stream_loop())

async def _stream_loop(self):
    # 1. 连接 Helius WebSocket
    async with websockets.connect(url) as ws:
        # 2. 发送订阅请求
        await ws.send(subscribe_msg)
        
        # 3. 持续接收消息
        while self.running:
            msg = await ws.recv()
            await self._handle_message(msg)
```

**关键方法**:
- `start()` - 启动流
- `_stream_loop()` - WebSocket 循环
- `_handle_message()` - 处理接收到的消息
- `_extract_trade_info()` - 解析交易数据

**断点位置**:
- `trade_stream.py:24` - `start()` 入口
- `trade_stream.py:47` - 发送订阅请求
- `trade_stream.py:60` - 接收消息循环
- `trade_stream.py:72` - `handle_message()` 入口

### 3.5 第三阶段：交易处理 `process_trade()`

**文件**: `app/services/trade_processor.py`

```python
async def process_trade(db, tx_detail):
    # 1. 计算关键指标
    # 2. 保存 TradeAnalysis 记录
    # 3. 推送到 WebSocket 前端
```

**断点位置**:
- `trade_processor.py` - `process_trade()` 入口

### 3.6 WebSocket 推送

**文件**: `app/websocket/manager.py`

```python
async def broadcast(mint: str, message: dict):
    # 遍历该 mint 的所有 WebSocket 连接
    # 发送 JSON 消息
```

**断点位置**:
- `websocket/manager.py` - `broadcast()` 方法

## 四、调试断点设置指南

### 4.1 调试回填流程

在 VSCode/PyCharm 中设置断点：

1. **入口断点**: `main.py:169` - `api_start_monitor`
2. **回填启动**: `trade_backfill.py:24` - `run()`
3. **签名获取**: `trade_backfill.py:87` - `_fetch_all_signatures()`
4. **批量解析**: `trade_backfill.py:124` - `_batch_parse_transactions()`
5. **交易入库**: `trade_backfill.py:222` - `_bulk_insert()`

### 4.2 调试实时流

1. **流启动**: `trade_stream.py:24` - `start()`
2. **WebSocket 连接**: `trade_stream.py:47` - 连接建立
3. **消息处理**: `trade_stream.py:72` - `_handle_message()`
4. **数据解析**: `trade_stream.py:134` - `_extract_trade_info()`

### 4.3 调试交易处理

1. **处理入口**: `trade_processor.py` - `process_trade()`
2. **指标计算**: `trade_processor.py` - `calculate_metrics()`

### 4.4 调试 WebSocket 推送

1. **广播入口**: `websocket/manager.py` - `broadcast()`
2. **连接管理**: `websocket/manager.py` - `connect()` / `disconnect()`

## 五、关键配置

### 5.1 可调整参数

在数据库 `settings` 表中：

| key | 默认值 | 说明 |
|-----|--------|------|
| batch_size | 100 | 每批处理签名数 |
| concurrent_requests | 3 | 并发请求数 |
| request_interval | 0.5 | 请求间隔(秒) |
| helius_api_key | - | Helius API 密钥 |

### 5.2 日志位置

- 主日志: `logs/app.log`
- 也输出到 stdout（容器日志）

## 六、API 端点汇总

| 端点 | 方法 | 说明 |
|------|------|------|
| /admin/api/start | POST | 开始监控指定 mint |
| /admin/api/stop | POST | 停止监控 |
| /admin/api/trades | GET | 获取交易列表 |
| /admin/api/metrics | GET | 获取指标数据 |
| /admin/api/settings | GET/PUT | 获取/更新设置 |
| /admin/ws/trades/{mint} | WebSocket | 实时交易推送 |

## 七、数据库表

- **users** - 用户表
- **transactions** - 交易记录
- **tokens** - Token 信息
- **settings** - 系统设置
- **trade_analysis** - 交易分析结果