# Redis 替换 Transaction、TradeAnalysis 方案

## 一、现状分析

### 1.1 当前数据表

| 表名 | 用途 | 主要字段 |
|------|------|---------|
| `Transaction` | 存储原始交易数据 | sig, slot, block_time, from_address, to_address, amount, token_mint, transaction_type, sol_spent, source 等 |
| `TradeAnalysis` | 存储分析结果 | sig, net_sol_flow, net_token_flow, price_per_token, wallet_tag, processed_at |

### 1.2 数据流分析

```
┌─────────────────────────────────────────────────────────────────┐
│                        trade_stream.py                          │
│                     (实时交易 WebSocket)                          │
│                                                                  │
│  Helius WS → 解析交易 → 写入 Transaction → enqueue_trade        │
│                                         ↓                        │
│                                   _consumer_loop                 │
│                                         ↓                        │
│                          去重检查(TradeAnalysis)                  │
│                                         ↓                        │
│                          计算均价(_calculate_index)              │
│                                         ↓                        │
│                          写入 TradeAnalysis                      │
│                                         ↓                        │
│                          WebSocket 广播                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                        trade_backfill.py                         │
│                      (历史交易回填)                               │
│                                                                  │
│  getTransactionsForAddress → 批量解析 → 写入 Transaction         │
│                                              ↓                   │
│                               _trigger_full_calculation           │
│                                              ↓                   │
│                              run_full_calculation                │
│                                              ↓                   │
│                     读取 Transaction → 计算 → 写入 TradeAnalysis │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 当前 DB 排序逻辑

```python
# trade_processor.py run_full_calculation
trades = (
    db.query(Transaction)
    .filter(Transaction.token_mint == mint)
    .order_by(
        case((Transaction.source == "rpc_fill", 0), else_=1),  # 优先级排序
        Transaction.id.asc()                                    # 主键排序
    )
    .all()
)
```

**排序规则**：
1. `source` 优先级：rpc_fill（历史）= 0，helius_ws（实时）= 1
2. `id` 升序：自增主键保证同组内顺序

**重要**：Helius 返回的原始顺序才是真正的标准顺序，DB 的 id 自增只是碰巧保留了这个顺序。

---

## 二、Redis 数据结构设计

### 2.1 核心数据结构

每个 sig 用**一个 Hash** 存储所有数据（合并 Transaction + TradeAnalysis）：

```
Key: sig:{sig_value}
Type: Hash
TTL: 7天

Fields:
  # === Transaction 原始字段 ===
  sig: str              # 主键
  slot: int
  block_time: datetime  
  from_address: str
  to_address: str
  amount: float
  token_mint: str
  token_symbol: str
  transaction_type: str  # BUY / SELL / TRANSFER
  dex: str               # raydium / pump.fun / orca
  pool_address: str
  sol_spent: float       # 消耗 SOL
  fee: float
  raw_data: str
  source: str            # helius_ws / rpc_fill
  
  # === TradeAnalysis 分析字段 ===
  net_sol_flow: float    # SOL 净流入
  net_token_flow: float  # Token 净流入
  price_per_token: float # 单价（均价法计算结果）
  wallet_tag: str        # dealer / unknown / retail
  processed_at: datetime
  
  # === 辅助字段 ===
  status: str            # pending / processed
  mint: str               # token_mint 冗余存储
  seq: int               # 自增序号（用于排序）
```

### 2.2 索引结构

| Key | Type | 用途 | TTL |
|-----|------|------|-----|
| `sig:{sig}` | Hash | 存储 sig 的完整数据 | 7天 |
| `mint_txs:{mint}` | Sorted Set | 按顺序存储该 mint 的所有 sig | 7天 |
| `mint_seq:{mint}` | String | 自增计数器，用于生成序号 | 永久 |
| `processed:{mint}` | Set | 已处理 sig 集合（去重用） | 7天 |
| `user:{address}` | Hash | 用户状态（已有，保留） | 永久 |
| `metrics:{mint}` | Hash | 全局指标（已有，保留） | 永久 |

---

## 三、排序方案

### 3.1 核心问题

Helius 返回的原始顺序才是真正的标准顺序，DB 的 id 自增只是碰巧保留了这个顺序。Redis 需要模拟这个机制。

### 3.2 解决方案：自增序号 + 优先级

```python
# 1. 每个 mint 维护自增序号
Key: mint_seq:{mint}
操作: INCR mint_seq:{mint}  → 返回 1, 2, 3, 4, 5...

# 2. 计算复合 score
def calc_score(source: str, seq: int) -> float:
    """
    复合分数 = 优先级 * 大基数 + 序号
    - 优先级：rpc_fill=10, helius_ws=20
    - 大基数：10_000_000（保证序号不会进位影响优先级）
    
    示例：
      - seq=1, source=rpc_fill → score=10_000_001
      - seq=2, source=rpc_fill → score=10_000_002
      - seq=3, source=helius_ws → score=20_000_003
    """
    priority = 10 if source == "rpc_fill" else 20
    return priority * 10_000_000 + seq

# 3. 存入 Sorted Set
await redis.zadd(f"mint_txs:{mint}", {sig: score})
```

### 3.3 效果验证

**Helius 返回顺序**：
```
sig_A (rpc_fill, 第1条)
sig_B (rpc_fill, 第2条)
sig_C (helius_ws, 第3条)  ← 实时流开始
sig_D (helius_ws, 第4条)
sig_E (helius_ws, 第5条)
```

**存入 Redis 过程**：
```python
# sig_A: seq=1, priority=10, score=10_000_001
# sig_B: seq=2, priority=10, score=10_000_002
# sig_C: seq=3, priority=20, score=20_000_003
# sig_D: seq=4, priority=20, score=20_000_004
# sig_E: seq=5, priority=20, score=20_000_005

await redis.zadd("mint_txs:{mint}", {
    sig_A: 10_000_001,
    sig_B: 10_000_002,
    sig_C: 20_000_003,
    sig_D: 20_000_004,
    sig_E: 20_000_005,
})
```

**取出结果**：
```python
sig_list = await redis.zrange("mint_txs:{mint}", 0, -1)
# [sig_A, sig_B, sig_C, sig_D, sig_E]  ← 与 Helius 顺序完全一致！
```

### 3.4 排序结果对照表

| sig | source | seq | priority | score | ZRANGE 顺序 |
|-----|--------|-----|----------|-------|-------------|
| sig_A | rpc_fill | 1 | 10 | 10_000_001 | 1 |
| sig_B | rpc_fill | 2 | 10 | 10_000_002 | 2 |
| sig_C | helius_ws | 3 | 20 | 20_000_003 | 3 |
| sig_D | helius_ws | 4 | 20 | 20_000_004 | 4 |
| sig_E | helius_ws | 5 | 20 | 20_000_005 | 5 |

**与 DB 的 `ORDER BY source, id ASC` 完全一致！**

---

## 四、字段对照表

### 4.1 Transaction → sig:{sig}

| Transaction 字段 | Redis Hash Field | 说明 |
|-----------------|------------------|------|
| sig | sig | 主键 |
| slot | slot | 区块链槽位 |
| block_time | block_time | 区块时间 |
| from_address | from_address | 发送方 |
| to_address | to_address | 接收方 |
| amount | amount | 数量 |
| token_mint | mint | 代币地址（冗余存储） |
| token_symbol | token_symbol | 代币符号 |
| transaction_type | transaction_type | 交易类型 |
| dex | dex | DEX 来源 |
| pool_address | pool_address | 池地址 |
| sol_spent | sol_spent | SOL 消耗 |
| fee | fee | 手续费 |
| raw_data | raw_data | 原始数据 |
| source | source | 数据来源 |

### 4.2 TradeAnalysis → sig:{sig}

| TradeAnalysis 字段 | Redis Hash Field | 说明 |
|-------------------|------------------|------|
| sig | sig | 主键 |
| - | net_sol_flow | SOL 净流入（复用 sol_spent） |
| - | net_token_flow | Token 净流入（复用 amount） |
| price_per_token | price_per_token | 单价 |
| wallet_tag | wallet_tag | 钱包标记 |
| processed_at | processed_at | 处理时间 |

### 4.3 新增字段

| 字段 | 说明 |
|------|------|
| status | pending / processed，处理状态 |
| mint | token_mint 冗余存储，方便按 mint 查询 |
| seq | 自增序号，用于排序 |

---

## 五、DB → Redis 操作映射

| 操作 | 当前 DB 操作 | 替换为 Redis |
|------|-------------|-------------|
| 写入交易 | `INSERT Transaction` | `HSET sig:{sig} ...` + `ZADD mint_txs:{mint}` |
| 写入分析 | `INSERT TradeAnalysis` | `HSET sig:{sig} ...`（更新已有 Hash） |
| 去重检查 | `SELECT FROM TradeAnalysis WHERE sig=?` | `EXISTS sig:{sig}` 或 `HGET sig:{sig} status` |
| 按 mint 查询 | `SELECT FROM Transaction WHERE token_mint=?` | `ZRANGE mint_txs:{mint} 0 -1` |
| 读取详情 | `SELECT FROM Transaction WHERE sig=?` | `HGETALL sig:{sig}` |
| 全量计算 | `SELECT FROM Transaction ORDER BY source, id` | `ZRANGE mint_txs:{mint} 0 -1` + `HGETALL sig:{sig}` |
| 清理 mint | `DELETE Transaction + TradeAnalysis` | `ZRANGE` 获取所有 sig + `DEL sig:{sig}` + `DEL mint_txs:{mint}` |

---

## 六、代码修改点

### 6.1 trade_stream.py（第 180-205 行）

**当前代码**：
```python
existing = db.query(Transaction).filter(Transaction.sig == sig).first()
if not existing:
    tx_record = Transaction(**tx_detail)
    db.add(tx_record)
    db.commit()
```

**修改为**：
```python
sig_key = f"sig:{sig}"
exists = await redis.exists(sig_key)
if not exists:
    # 获取自增序号
    seq = await redis.incr(f"mint_seq:{mint}")
    
    # 计算分数并存入 Sorted Set
    score = calc_score(tx_detail.get("source", ""), seq)
    await redis.zadd(f"mint_txs:{mint}", {sig: score})
    
    # 写入完整数据
    await redis.hset(sig_key, mapping={
        **tx_detail,
        "status": "pending",
        "processed_at": "",
        "net_sol_flow": "",
        "net_token_flow": "",
        "price_per_token": "",
        "wallet_tag": "",
        "mint": mint,
        "seq": seq,
    })
```

### 6.2 trade_backfill.py（第 245-265 行）

**当前代码**：
```python
existing = db.query(Transaction).filter(Transaction.sig == sig).first()
if existing:
    skipped += 1
    continue
try:
    stmt = insert(Transaction).values(**detail)
    db.execute(stmt)
```

**修改为**：
```python
sig_key = f"sig:{sig}"
exists = await redis.exists(sig_key)
if exists:
    skipped += 1
    continue

try:
    # 获取自增序号
    seq = await redis.incr(f"mint_seq:{mint}")
    
    # 计算分数并存入
    score = calc_score(detail.get("source", ""), seq)
    await redis.zadd(f"mint_txs:{mint}", {sig: score})
    
    # 写入完整数据
    await redis.hset(sig_key, mapping={
        **detail,
        "status": "pending",
        "mint": mint,
        "seq": seq,
    })
    inserted += 1
```

### 6.3 trade_processor.py (_consumer_loop，第 349-369 行)

**当前代码**：
```python
existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == sig).first()
if existing:
    continue
...
analysis = TradeAnalysis(...)
db.add(analysis)
```

**修改为**：
```python
sig_key = f"sig:{sig}"
existing = await redis.exists(sig_key)
if existing:
    status = await redis.hget(sig_key, "status")
    if status == "processed":
        continue

# 计算均价...
metrics = await _calculate_index(tx_detail, mint)

# 更新分析结果
await redis.hset(sig_key, mapping={
    "status": "processed",
    "net_sol_flow": tx_detail.get("sol_spent", 0) or 0.0,
    "net_token_flow": tx_detail.get("amount", 0) or 0.0,
    "price_per_token": metrics.get("avgPrice", 0),
    "wallet_tag": "dealer" if metrics.get("is_dealer") else "unknown",
    "processed_at": datetime.utcnow().isoformat(),
})
```

### 6.4 trade_processor.py (run_full_calculation，第 424-488 行)

**当前代码**：
```python
trades = (
    db.query(Transaction)
    .filter(Transaction.token_mint == mint)
    .order_by(...)
    .all()
)

for tx in trades:
    existing = db.query(TradeAnalysis).filter(TradeAnalysis.sig == tx.sig).first()
    ...
```

**修改为**：
```python
# 1. 获取该 mint 的所有 sig（已排序）
sig_list = await redis.zrange(f"mint_txs:{mint}", 0, -1)

# 2. 批量获取详情（使用 pipeline 提高性能）
pipe = redis.pipeline()
for sig in sig_list:
    pipe.hgetall(f"sig:{sig}")
results = await pipe.execute()

for i, tx_detail in enumerate(results):
    if not tx_detail:
        continue
    
    sig = tx_detail.get("sig", "")
    
    # 检查是否已处理
    if tx_detail.get("status") == "processed":
        continue
    
    # 计算均价...
    metrics = await _calculate_index(tx_detail, mint)
    
    # 更新分析结果
    await redis.hset(f"sig:{sig}", mapping={
        "status": "processed",
        "net_sol_flow": tx_detail.get("sol_spent", 0) or 0.0,
        "net_token_flow": tx_detail.get("amount", 0) or 0.0,
        "price_per_token": metrics.get("avgPrice", 0),
        "wallet_tag": "dealer" if metrics.get("is_dealer") else "unknown",
        "processed_at": datetime.utcnow().isoformat(),
    })
```

### 6.5 trade_processor.py (reset_processor，第 491-543 行)

**当前代码**：
```python
sigs = [row.sig for row in db.query(Transaction.sig).filter(Transaction.token_mint == mint).all()]
if sigs:
    db.query(TradeAnalysis).filter(TradeAnalysis.sig.in_(sigs)).delete()
    db.query(Transaction).filter(Transaction.token_mint == mint).delete()
```

**修改为**：
```python
# 1. 获取所有 sig
sigs = await redis.zrange(f"mint_txs:{mint}", 0, -1)

# 2. 删除所有 sig 数据
if sigs:
    pipe = redis.pipeline()
    for sig in sigs:
        pipe.delete(f"sig:{sig}")
    await pipe.execute()

# 3. 删除索引
await redis.delete(f"mint_txs:{mint}")
await redis.delete(f"processed:{mint}")

# 4. 可选：重置序号计数器
# await redis.set(f"mint_seq:{mint}", 0)
```

### 6.6 trade_processor.py (calculate_metrics，第 546-564 行)

**当前代码**：
```python
trade_count = db.query(Transaction).filter(Transaction.token_mint == mint).count()
```

**修改为**：
```python
trade_count = await redis.zcard(f"mint_txs:{mint}")
```

---

## 七、辅助函数

```python
async def _get_redis():
    """获取 Redis 连接"""
    from app.services.dealer_detector import _redis
    return _redis


def calc_score(source: str, seq: int) -> float:
    """
    计算 Sorted Set 分数：优先级 * 大基数 + 序号
    
    Args:
        source: 数据来源 (rpc_fill / helius_ws)
        seq: 自增序号
    
    Returns:
        float: 复合分数
    
    优先级：
        - rpc_fill: 10 (历史回填优先)
        - helius_ws: 20 (实时流次之)
    
    示例：
        - seq=1, source=rpc_fill → 10_000_001
        - seq=2, source=helius_ws → 20_000_002
    """
    priority = 10 if source == "rpc_fill" else 20
    return priority * 10_000_000 + seq
```

---

## 八、TTL 设置建议

```python
# sig 数据：7 天过期
await redis.expire(f"sig:{sig}", 7 * 24 * 3600)

# mint 索引：7 天过期
await redis.expire(f"mint_txs:{mint}", 7 * 24 * 3600)

# 自增计数器：永久
# mint_seq:{mint} 不设置过期
```

---

## 九、迁移路径建议

### Phase 1：双写模式（可选）
- 新增 Redis 写入路径
- 保留 DB 作为备份
- 验证 Redis 数据正确性

### Phase 2：读 Redis，写 DB
- 切换读操作到 Redis
- DB 异步写入（或作为备份）

### Phase 3：纯 Redis
- 完全移除 DB 依赖
- 生产环境建议开启 Redis RDB/AOF 持久化

---

## 十、注意事项

1. **内存预估**：假设每条 sig ~500 bytes，100万条 ~500MB
2. **持久化**：生产环境建议开启 Redis RDB/AOF 持久化
3. **兼容性**：保留原有接口，底层切换存储实现
4. **并发控制**：自增序号 `INCR` 是原子操作，无需额外锁
5. **异常处理**：Redis 操作需做好异常捕获，保证服务稳定性
