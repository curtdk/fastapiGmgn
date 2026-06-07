# 用户 ↔ 簇组 关系文档

## 一、架构概览

```
user:{address}  ──(cluster_name)──►  cluster:data:{name}
    (Redis Hash)                        (Redis Hash)
  仅存簇名索引                         完整簇组信息
```

**设计原则**：用户只存簇名索引，所有簇组属性统一从簇组 Redis 读取，单一数据源。

---

## 二、存储结构

### 2.1 用户状态 — `user:{address}`

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | str | 用户类型：`dealer` / `retail` / `unknown` |
| `status_source` | str | 状态来源：`system`（系统判定）/ `manual`（手动锁定），默认为 `system` |
| `conditions` | JSON array | 触发的庄家检测条件列表，如 `["C005","C006"]` |
| `cluster_name` | str | 所属簇组名称（唯一索引），无簇组时为空 |
| `{mint}_holdingQty` | float | 当前持仓数量 |
| `{mint}_holdingCost` | float | 当前持仓成本 (SOL) |
| `{mint}_avgPrice` | float | 当前均价 |
| `{mint}_totalBuyAmount` | float | 累计买入金额 (SOL) |
| `{mint}_totalSellAmount` | float | 累计卖出金额 (SOL) |
| `{mint}_totalSellPrincipal` | float | 累计卖出本金 (SOL) |

> - `cluster_type` 不再存储，需要时从簇组 Redis 实时读取
> - `status_source = "manual"` 时，簇组类型变更不会覆盖用户状态

### 2.2 簇组数据 — `cluster:data:{name}`

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | str | 簇组名称（格式：`{user_address}_{transaction_type}`） |
| `enabled` | bool | 是否启用 |
| `cluster_type` | str | 簇组类型：`dealer` / `retail` / `undefined` |
| `judgment_type` | str | 判定来源：`system` / `manual` |
| `folder` | str | 自定义文件夹 |
| `base_cu` | int | 基准 CU 消耗 |
| `base_cu_offset` | int | CU 偏移量 |
| `base_program_count` | int | 基准程序 ID 数量 |
| `base_program_offset` | int | 程序数偏移量 |
| `base_main_instruction_count` | int | 基准主指令数量 |
| `base_main_offset` | int | 主指令偏移量 |
| `base_inner_instruction_count` | int | 基准内部指令数量 |
| `base_inner_offset` | int | 内部指令偏移量 |
| `base_transaction_type` | str | 基准交易类型（`BUY` / `SELL`） |
| `base_programs` | JSON array | 基准程序 ID 列表 |
| `base_main_instructions` | JSON array | 基准主指令列表 |
| `base_inner_instructions` | JSON array | 基准内部指令列表 |
| `txs` | JSON array | 所有交易签名列表 |
| `users` | JSON array | 所有用户地址列表 |
| `tx_count` | int | 总交易数 |
| `user_count` | int | 总用户数 |
| `created_at` | float | 创建时间戳 |

---

## 三、数据写入流程

### 3.1 新用户第 1 笔交易

```
tx_detail 到达
  └─ _calculate_index()
       └─ get_trader_state_with_sig()
            └─ if not state (新用户)
                 └─ _check_local_dealer_conditions()  ← C002-C006
                      └─ run_cluster_detection()
                           ├─ 匹配到已有簇组 → cluster_info = {name, type}
                           └─ 创建新簇组 → cluster_info + new_cluster_broadcast
                 └─ state["status"] = dealer/retail/unknown
            └─ return {state, cluster_info}
       └─ save_trader_state()
            └─ Redis HSET user:{address}
                 ├─ cluster_name = "GDzisw_BUY"
                 ├─ status = "retail"
                 └─ {mint}_holding* = ...
       └─ ws_manager.broadcast("cluster_matched", cluster_info)
```

### 3.2 已有用户后续交易

```
tx_detail 到达
  └─ _calculate_index()
       └─ get_trader_state_with_sig()
            └─ else (已有用户)
                 ├─ 从 Redis 读取 cluster_name
                 ├─ get_cluster_sync(name) → 获取最新 cluster_type
                 ├─ cluster_info = {name, type}  ← 实时读取，始终最新
                 └─ add_tx_to_cluster_sync(name, sig, address)  ← 追加 sig
            └─ return {state, cluster_info}
       └─ save_trader_state()
            └─ Redis HSET user:{address}
                 ├─ cluster_name = "GDzisw_BUY"  ← 仅存索引
                 └─ {mint}_holding* = ...
       └─ ws_manager.broadcast("cluster_matched", cluster_info)
```

### 3.3 tx_count 累积

- 新用户第 1 笔：`create_cluster()` → `tx_count=1` + `add_tx_to_cluster_sync()` → `txs.append(sig)`
- 已有用户后续：`else` 分支每次都调 `add_tx_to_cluster_sync()` → `txs` 数组增长 → `tx_count` 递增

---

## 四、数据读取方式

### 4.1 获取簇组类型

```python
from app.services.cluster.redis_keys import get_cluster_sync

cluster = get_cluster_sync("GDzisw_BUY")
cluster_type = cluster.cluster_type  # "dealer" / "retail" / "undefined"
```

### 4.2 获取完整簇组信息

```python
from app.services.cluster.redis_keys import get_cluster

cluster = await get_cluster("GDzisw_BUY")
# cluster.to_dict() → 包含所有字段
```

API 端点：`GET /admin/api/clusters/{name}`

---

## 五、同步机制

| 变更操作 | 同步方式 |
|----------|---------|
| 管理员修改簇组类型 | `cluster:data:{name}`.cluster_type 更新 → 用户下一笔交易由 `get_cluster_sync()` 读取最新值 |
| 簇组类型变更 → 用户 status | `status_source != "manual"` 时自动同步：`state["status"] = cluster_type` |
| 簇组类型变更 → 手动用户 | `status_source == "manual"` 时 **跳过**，不覆盖手动设置 |
| 手动修改用户状态 | `PUT /admin/api/users/{address}/status` → `status_source = "manual"`，永久锁定 |
| 新用户匹配到簇组 | `user:{address}`.cluster_name 写入，`status_source = "system"` |
| 用户新交易追加 sig | `add_tx_to_cluster_sync()` → `cluster:data:{name}`.txs 追加 |

### 5.1 手动修改优先级

```
管理员手动设置: status_source = "manual"  → 最高优先级，永不自动覆盖
系统 C006 判定:  status_source = "system"  → 可被簇组变更覆盖
```

API 示例：
```bash
curl -X PUT http://127.0.0.1:8000/admin/api/users/{address}/status \
  -H "Content-Type: application/json" \
  -d '{"status": "dealer"}'
```

---

## 六、关键文件

| 文件 | 职责 |
|------|------|
| [trade_processor.py](../app/services/trade_processor.py) | `get_trader_state_with_sig()` — 读写用户状态，同步簇组 |
| [redis_keys.py](../app/services/cluster/redis_keys.py) | `ClusterData` — 簇组数据结构定义 |
| [detector.py](../app/services/cluster/detector.py) | C006 簇组检测入口 |
| [manager.py](../app/services/cluster/manager.py) | 簇组创建、匹配、管理 |
| [cluster_api.py](../app/routes/cluster_api.py) | 簇组 REST API |
