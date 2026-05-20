# 策略系统说明

## 📁 文件结构

```
app/taskcl/
├── __init__.py          # 模块入口
├── base.py              # 策略基类（所有策略继承）
├── manager.py           # 策略管理器（单例）
├── consumer.py          # 策略消费者（后台 Task）
├── strategy_1.py        # 策略1（追卖买入策略）
├── strategy_2.py        # 策略2（待实现）
└── strategy_3.py        # 策略3（待实现）
```

---

## 🏗️ 架构说明

```
WS 接收数据
    ↓
消息队列 (asyncio.Queue)
    ↓
策略消费者 Task (consumer.py)
    ↓
当前选中的策略 (strategy_1.py)
    ↓
JupiterService (买卖接口)
```

---

## 📋 使用方法

### 1. 前端操作（/admin/trade 页面）

启动监听后，页面会显示"策略控制区域"：

```
策略: [下拉框: 策略1/策略2/策略3]  [策略开关 Toggle]  状态显示
```

- 选择策略名称
- 打开策略开关 → 策略启用，数据进入策略队列
- 关闭策略开关 → 策略禁用，队列清空

### 2. 后端自动执行

当策略开关打开后：
1. WS 收到的每条交易都会发送到策略队列
2. 消费者 Task 从队列取出交易
3. 调用当前策略的 `on_trade()` 方法
4. 策略根据规则自动执行买卖

---

## 📊 策略1：追卖买入策略

### 核心逻辑

```
状态机：
- idle（空闲）→ 检测大户卖出
- holding（持仓）→ 监控利润

流程：
1. 检测：大户卖出 > 2 SOL（排除自己）
2. 买入：立即买入 0.1 SOL
3. 持有：监控利润（每条交易到来时计算）
4. 卖出：利润 > 10% 时自动卖出
```

### 配置参数

```python
profit_threshold = 10.0   # 利润阈值（%）
sell_threshold = 2.0       # 卖出金额阈值（SOL）
buy_amount = 0.1          # 买入金额（SOL）
cooldown_seconds = 30      # 冷却时间（秒）
```

### 利润计算方法

```python
# 通过实时市场交易计算当前持仓市值
market_price = sol_spent / amount  # 当前市场价

# 当前市值 = 持仓数量 × 当前市场价
current_value = buy_amount * market_price

# 利润 = 当前市值 - 买入成本
profit = current_value - buy_cost

# 利润率 = 利润 / 买入成本 × 100%
profit_rate = (profit / buy_cost) * 100
```

---

## 🔧 开发新策略

### 1. 创建策略文件

```python
# app/taskcl/strategy_2.py
from app.taskcl.base import BaseStrategy
from app.services.jupiter_service import get_jupiter_service

class Strategy2(BaseStrategy):
    name = "策略2"
    
    def __init__(self, mint: str = ""):
        super().__init__(mint)
        # 自定义配置
        self.config["profit_threshold"] = 15.0
    
    async def on_trade(self, tx_detail):
        # 实现策略逻辑
        pass
```

### 2. 注册策略

编辑 `manager.py`，在 `STRATEGY_MAP` 中添加：

```python
STRATEGY_MAP = {
    "策略1": "app.taskcl.strategy_1:Strategy1",
    "策略2": "app.taskcl.strategy_2:Strategy2",
    "策略3": "app.taskcl.strategy_3:Strategy3",
}
```

### 3. 更新前端

编辑 `trade_live.html`，在策略下拉框中添加选项：

```html
<select id="strategySelect">
    <option value="策略1">策略1</option>
    <option value="策略2">策略2</option>
    <option value="策略3">策略3</option>
</select>
```

---

## 📡 API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/admin/api/strategy/select` | POST | 选择并启用策略 |
| `/admin/api/strategy/disable` | POST | 禁用策略 |
| `/admin/api/strategy/status` | GET | 获取策略状态 |

---

## 🧪 测试

1. 启动程序：`python main.py`
2. 打开浏览器：`http://localhost:8000/admin/trade`
3. 输入 Mint 地址，点击"开始"
4. 选择策略，打开策略开关
5. 观察控制台日志，看策略是否正常工作