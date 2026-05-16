# WebSocket 消息说明

## 消息类型概览

| type | 功能 | 触发时机 | 前端处理 |
|------|------|----------|----------|
| `status` | 状态通知 | 回填/实时流状态变化 | 更新状态徽章和消息 |
| `trade` | 交易记录 | 每笔交易处理完成 | 显示交易行 + 更新指标 |
| `cluster_matched` | 簇组匹配 | 匹配到已存在簇组 | 更新交易行的簇组标签 |
| `cluster_created` | 新簇组创建 | 创建新簇组 | 显示新簇组通知 |
| `user_status` | 用户状态变化 | 庄家检测完成 | 更新用户状态 |
| `error` | 错误通知 | 操作异常 | 弹出错误提示 |

---

## 详细说明

### 1. status
- **功能**：状态通知（回填进度、连接状态等）
- **触发时机**：
  - 回填开始
  - 回填进度更新
  - 回填完成
  - 实时流开始
- **后端位置**：`app/services/trade_backfill.py`、`app/services/trade_stream.py`
- **前端处理**：`updateStatus()` - 更新状态徽章和消息文本

### 2. trade
- **功能**：交易记录（核心消息）
- **触发时机**：每笔交易处理完成后广播
- **后端位置**：`app/services/trade_processor.py` 的 `_calculate_index()`
- **前端处理**：`addTradeRow()` - 添加交易行到表格

### 3. cluster_matched
- **功能**：簇组匹配通知
- **触发时机**：交易匹配到已存在的簇组（retail/dealer/unknown）
- **后端位置**：`app/services/trade_processor.py`
- **前端处理**：`handleClusterMatched()` - 更新交易行的簇组标签
- **说明**：在 trade 之后发送，确保交易行已显示

### 4. cluster_created
- **功能**：新簇组创建通知
- **触发时机**：首次交易创建新簇组
- **后端位置**：`app/services/cluster/detector.py` 的 `run_cluster_detection()`
- **前端处理**：待实现（暂无 handler）

### 5. user_status
- **功能**：用户状态变化通知（庄家判定结果）
- **触发时机**：庄家检测完成后（保存到 Redis 后）
- **后端位置**：`app/services/dealer_detector.py`
- **前端处理**：`handleUserStatus()` - 更新用户状态显示

### 6. error
- **功能**：错误通知
- **触发时机**：回填异常、实时流异常
- **后端位置**：`app/services/trade_backfill.py`、`app/services/trade_stream.py`
- **前端处理**：弹出错误提示

---

## 前端 WebSocket 处理代码

```javascript
ws.onmessage = (evt) => {
  try {
    const msg = JSON.parse(evt.data);
    if (msg.type === 'status') updateStatus(msg.data);
    else if (msg.type === 'trade') { addTradeRow(msg.data, true); fetchMetrics(); }
    else if (msg.type === 'error') alert('错误: ' + (msg.data.message || '未知'));
    else if (msg.type === 'user_status') handleUserStatus(msg.data);
    else if (msg.type === 'cluster_matched') handleClusterMatched(msg.data);
    // cluster_created 暂无 handler
  } catch (e) { console.error('WS 消息解析失败:', e); }
};
```

---

## 广播顺序（trade_processor.py）

1. **trade** - 先发送，让前端先显示交易行
2. **cluster_matched** - 再发送，更新同一行的簇组标签
3. **cluster_created** - 最后发送，通知新簇组创建