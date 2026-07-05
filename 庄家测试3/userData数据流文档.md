# /admin/trade 页面 userData 数据流文档

## 核心结论

**`/admin/trade` 页面的用户列表和簇组信息（顶部 3 张卡）完全依靠前端 `userData` 字典进行显示和聚合计算。**

只有"簇组详情弹窗"是另外的数据源（后端 API）。

---

## 1. userData 字典定义

`userData` 是一个**纯前端的全局对象**，位于 [trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) 中，结构如下：

```javascript
userData = {
  "<address_1>": {
    address: str,                   // 钱包地址
    status: str,                    // dealer / retail / unknown
    status_source: str,             // system / manual
    conditions: list,               // C002-C008 标记
    cluster_name: str,              // 簇名（如 C00XXXX）
    cluster_type: str,              // dealer / retail / unknown
    cluster_tx_count: int,          // 写死 0
    cluster_user_count: int,        // 写死 0
    holding_qty: float,             // 持仓量
    holding_cost: float,            // 持仓成本
    total_buy_amount: float,        // 累计买入金额
    total_sell_amount: float,       // 累计卖出金额
    total_sell_principal: float,    // 累计卖出本金
  },
  "<address_2>": { ... },
  ...
}
```

**关键字段**：
- `cluster_name`：用于顶部 3 张卡的**分组 key**
- `cluster_type`：用于顶部 3 张卡的**分类（dealer/retail/unknown）**
- `status`：用于按 dealer/retail/unknown 计数
- `holding_qty`：用于累加总持仓量

---

## 2. userData 的 4 个数据来源

### 来源 1：启动时批量拉取（HTTP GET）

**触发**：调用 `fetchUsers()`

**位置**：[trade_live.html:1264-1273](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html#L1264)

```javascript
async function fetchUsers() {
  if (!currentMint) return;
  try {
    const resp = await fetch('/admin/api/users?mint=' + encodeURIComponent(currentMint));
    const data = await resp.json();
    userData = {};  // ★ 清空字典
    const users = data.users || [];
    users.forEach(function(u) { userData[u.address] = u; });
    renderUserTable();
    ...
  }
}
```

**后端处理**（[routes/trades.py:313](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/routes/trades.py#L313)）：
1. SCAN `user:{mint}:*`（per-mint 持仓数据）
2. 读 `user:{address}`（全局数据：cluster_name / status / judgment_type）
3. 读 `cluster:{cluster_name}`（cluster_type）
4. 返回完整用户列表

**写入**：`userData = {}` 后整体赋值 12 字段

---

### 来源 2：实时 WS 推送（user_status 消息）

**触发**：每笔交易经 `_calculate_index` 处理后

**后端位置**：[trade_processor.py:752-761](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/services/trade_processor.py#L752)

```python
await ws_manager.broadcast(mint, {
    "type": "user_status",
    "data": {
        "address": address,
        "status": state["status"],
        "status_source": ...,
        "conditions": ...,
        "cluster_name": cluster_name,
        "cluster_type": cluster_type,
        "cluster_tx_count": ...,   # 写死 0
        "cluster_user_count": ..., # 写死 0
        "holding_qty": ...,
        "holding_cost": ...,
        "total_buy_amount": ...,
        "total_sell_amount": ...,
        "total_sell_principal": ...,
    }
})
```

**前端处理**（[trade_live.html:672, 1276-1283](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html#L1276)）：

```javascript
function upsertUserRow(data) {
  if (!data || !data.address) return;
  var existing = userData[data.address] || {};
  for (var k in data) {
    if (data.hasOwnProperty(k)) existing[k] = data[k];  // 整体覆盖
  }
  userData[data.address] = existing;
  renderUserTable();  // 触发重渲 + 簇组聚合
}
```

**触发链**：
```
WS user_status 消息
  ↓
ws.onmessage → handleUserStatus(data)
  ↓
upsertUserRow(data) → 整体覆盖 userData[address]
  ↓
renderUserTable() 末尾 → computeClusterSummary()
  ↓
顶部 3 张卡实时更新
```

---

### 来源 3：手动修改用户状态

**触发**：点击用户行的"改状态"按钮

**位置**：[trade_live.html:1383-1386](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html#L1383)

```javascript
// 前端立即更新
if (userData[currentUserAddress]) {
  userData[currentUserAddress].status = status;
  userData[currentUserAddress].status_source = statusSource;
  renderUserTable();
}
```

**说明**：
- 仅修改 `status` 和 `status_source` 2 个字段
- 不重新拉取全量数据

---

### 来源 4：手动修改簇组类型

**触发**：点击"改类型"按钮并保存

**位置**：[trade_live.html:1457-1463](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html#L1457)

```javascript
// 更新 userData 中所有同簇组用户的 cluster_type
for (var addr in userData) {
  if (userData.hasOwnProperty(addr) && userData[addr].cluster_name === currentCdmClusterName) {
    userData[addr].cluster_type = type;
  }
}
renderUserTable();
```

**说明**：
- 遍历 userData，更新**所有同簇组用户**的 `cluster_type`
- 不会触发后端 user_status 推送（依赖前端本地更新）

---

## 3. 页面显示与 userData 的关系

### 用户列表（renderUserTable）

| 显示项 | 读取字段 |
|--------|---------|
| 地址 | `address` |
| 状态标签 | `status` |
| 判定来源 | `status_source` |
| 持仓量 | `holding_qty` |
| 簇名 | `cluster_name` |
| 簇类型 | `cluster_type` |
| 持仓成本 | `holding_cost` |
| 买入金额 | `total_buy_amount` |

**渲染**：[trade_live.html:1286+](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html#L1286)

---

### 顶部 3 张簇组卡（computeClusterSummary）

**位置**：[trade_live.html:1472-1530](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html#L1472)

**计算步骤**：

1. **按 cluster_name 分组**
   ```javascript
   for (var addr in userData) {
     var u = userData[addr];
     var cname = u.cluster_name || '';
     if (!cname) { emptyCluster++; continue; }
     if (!clusters[cname]) {
       clusters[cname] = { type: u.cluster_type || 'unknown', users: [], ... };
     }
     if (clusters[cname].users.indexOf(addr) === -1) clusters[cname].users.push(addr);
   }
   ```

2. **按 status 计数**
   ```javascript
   if (u.status === 'dealer') clusters[cname].dealer_count++;
   else if (u.status === 'retail') clusters[cname].retail_count++;
   else clusters[cname].unknown_count++;
   ```

3. **累加持仓量**
   ```javascript
   clusters[cname].holding_qty += (parseFloat(u.holding_qty) || 0);
   ```

4. **计算总持仓（占比分母）**
   ```javascript
   var totalHoldingQty = 0;
   for (var addr in userData) {
     totalHoldingQty += (parseFloat(userData[addr].holding_qty) || 0);
   }
   ```

5. **按 cluster_type 分桶**
   ```javascript
   var groups = { dealer: [], retail: [], unknown: [] };
   for (var cn in clusters) {
     var c = clusters[cn];
     var ct = c.type;
     if (!groups[ct]) ct = 'unknown';
     groups[ct].push({ name: cn, user_count: c.users.length, ... });
   }
   ```

6. **排序 + 渲染 3 张卡**
   ```javascript
   for (var k in groups) { groups[k].sort(function(a, b) { return b.user_count - a.user_count; }); }
   // 渲染 csDealerCount / csRetailCount / csUndefinedCount
   ```

**显示内容**：
- 簇数量（`groups[key].length`）
- 用户数（`sum(items[i].user_count)`）
- Top3 簇组（按 user_count 降序）

---

## 4. 两条独立的数据流路径

### 路径 A：顶部 3 张卡 + 用户列表（依赖 userData）

```
                    ┌─────────────────────┐
                    │ 后端 Redis           │
                    │  user:{mint}:{addr} │
                    │  user:{addr}        │
                    │  cluster:{name}     │
                    └──────────┬──────────┘
                               │
                               │ 1. fetchUsers() - HTTP GET
                               ▼
                    ┌─────────────────────┐
                    │ userData (前端字典)  │
                    └──────────┬──────────┘
                               │
                               │ 2. ws 推 user_status
                               ▼
                    ┌─────────────────────┐
                    │ upsertUserRow       │
                    │ 整体覆盖 userData   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ renderUserTable     │
                    │  └→ computeClusterSummary │
                    │     ├─ 按 cluster_name 分组 │
                    │     ├─ 按 status 计数     │
                    │     └─ 渲染 3 张卡      │
                    └─────────────────────┘
```

**特点**：
- 完全前端聚合（无后端 API 调用）
- 每次 user_status 推送触发 1 次
- 依赖 userData 字典完整性

---

### 路径 B：簇组详情弹窗（独立 API）

```
点击 3 张卡之一 → openClusterSummaryModal(tabType)
  ↓
fetch('/admin/api/clusters/summary?mint=...')
  ↓
api_get_clusters_summary(mint) [cluster_api.py:11]
  ├─ SCAN cluster:* (cluster:index zset)
  ├─ SCAN user:{mint}:* 统计 per-mint 活跃用户
  ├─ 分组 dealer/retail/unknown
  └─ 返回 JSON
  ↓
renderSummaryTab(tabType, data)
```

**特点**：
- 后端 API 计算
- **仅在打开弹窗时调用一次**
- 切换 tab 时**重新调用**一次
- 数据来源是 Redis 全部簇组

---

## 5. 关键时序

```
T0: 启动 → connectWS()                          → 仅连 ws，userData 为空
T1: startMonitor(mint)                           → 后端启动 backfill + consumer
T2: backfill 进行中
    - 每笔交易 → _calculate_index → 推 user_status
    - 前端 userData 持续累积
T3: backfill 完成 → ws 推 backfill_done          → 不影响 userData
T4: fetchUsers() 调用                            → 首次批量写入 userData
    （此时 userData 应该已经通过 T2 累积了一部分）
T5: 实时 ws 推 user_status                       → userData 增量更新
T6: 用户手动改状态/簇类型                        → userData 局部更新
```

---

## 6. userData 的局限性

### 6.1 userData 缺失的场景

| 场景 | 影响 |
|------|------|
| **后端创建了新簇，但 userData 里没这个地址** | 顶部卡不显示这个簇 |
| **某用户从未收到过 user_status 推送** | 不在 userData 里 |
| **SELL 交易** | **不推 user_status**（C006 检测跳过 SELL）→ 该用户不进 userData |
| **0 用户的簇组** | **不可能在 userData 里**（必须先有 user_status 推送） |
| **改类型时没广播 user_status 给所有用户** | 其他客户端 userData 不更新 |

### 6.2 与后端真实数据的差异

| 数据 | 后端 Redis | 前端 userData |
|------|-----------|---------------|
| 簇组数 | 全部（41 个） | **仅出现在 user_status 推送中的簇**（可能 29 个） |
| 用户数 | per-mint 全量 | 推送过的用户 |
| 0 用户簇 | 存在 | **不显示** |
| 删除的簇 | 不存在 | 如果 userData 里有残留会**继续显示** |

---

## 7. 关键代码位置

### 后端

| 文件 | 行 | 作用 |
|------|---|------|
| [services/trade_processor.py](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/services/trade_processor.py) | 752-761 | user_status 广播 |
| [routes/trades.py](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/routes/trades.py) | 313+ | GET /admin/api/users |
| [routes/cluster_api.py](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/routes/cluster_api.py) | 11-80 | GET /api/clusters/summary |

### 前端

| 文件 | 行 | 作用 |
|------|---|------|
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1264 | fetchUsers() |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1276 | upsertUserRow() |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1286 | renderUserTable() |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1383 | 手动改状态 |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1457 | 手动改簇类型 |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1472 | computeClusterSummary() |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1544 | openClusterSummaryModal() |
| [templates/trade_live.html](file:///Users/curtdk/.openclaw/workspace/fastapiGmgn/app/templates/trade_live.html) | 1573 | switchClusterSummaryTab() |

---

## 8. 总结

### 一句话总结

**`/admin/trade` 页面的用户列表和顶部 3 张簇组卡完全依赖前端的 `userData` 字典**，该字典通过 4 个来源累积：
1. 启动时批量拉取（HTTP GET `/admin/api/users`）
2. 实时 WS 推送（`user_status` 消息）
3. 手动改状态
4. 手动改簇类型

**簇组详情弹窗**是独立的 API（`/admin/api/clusters/summary`），不依赖 userData。

### 已知问题与缓解

| 问题 | 缓解 |
|------|------|
| 顶部卡显示簇组数少于实际 | 弹窗表格显示完整数据（后端 API） |
| 0 用户簇不显示 | 弹窗表格可显示 |
| 改类型后其他客户端不同步 | 依赖前端 userData 本地更新 |

### 设计取舍

- ✓ **实时感**：每笔交易推送，立即反映在顶部卡
- ✗ **完整性**：userData 可能不完整
- ✗ **多客户端一致性**：依赖前端本地更新，无后端推送