"""
测试 _adjust_metrics_for_user 的不变式：
total_bet = Σ holding_cost (status≠dealer)
realized_profit = Σ (sell_amt - sell_prc) (status≠dealer)
"""
import asyncio
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

# Mock redis
class MockRedis:
    def __init__(self):
        self.data = {}  # key -> dict(field -> value)

    async def hgetall(self, key):
        return dict(self.data.get(key, {}))

    async def hset(self, key, mapping=None, **kwargs):
        if key not in self.data:
            self.data[key] = {}
        if mapping:
            for k, v in mapping.items():
                self.data[key][k] = str(v)
        for k, v in kwargs.items():
            self.data[key][k] = str(v)

    async def hincrbyfloat(self, key, field, amount):
        cur = float(self.data.get(key, {}).get(field, "0") or 0)
        new_val = cur + amount
        if key not in self.data:
            self.data[key] = {}
        self.data[key][field] = str(new_val)
        return new_val

    async def hget(self, key, field):
        return self.data.get(key, {}).get(field, None)


async def test_user_lifecycle():
    from app.services.trade_processor import (
        _adjust_metrics_for_user, update_metrics_delta,
        user_key, _get_metrics_key, _get_redis,
    )
    from app.services.cluster.redis_keys import user_mint_key

    # Patch _get_redis
    import app.services.trade_processor as tp
    redis = MockRedis()
    tp._redis = redis
    tp._get_redis = lambda: asyncio.sleep(0, result=redis)

    mint = "mint_test"
    addr = "AddrTest123"

    user_global_key = user_key(addr)
    user_mint = user_mint_key(mint, addr)
    metrics_key = f"metrics:{mint}"

    # 初始化一个用户：retail, holding_cost=10, sell_amt=20, sell_prc=12 → realized=8
    await redis.hset(user_mint, mapping={
        "holdingQty": "100",
        "holdingCost": "10",
        "totalSellAmount": "20",
        "totalSellPrincipal": "12",
    })
    await redis.hset(user_global_key, mapping={
        "status": "retail",
        "status_source": "system",
    })

    # 初始 total_bet / realized_profit = 该用户贡献（散户）
    total_bet_init = 10
    realized_init = 8
    await redis.hset(metrics_key, mapping={
        "total_bet": str(total_bet_init),
        "realized_profit": str(realized_init),
        "dealer_count": "0",
    })

    # 验证初始不变式
    assert float((await redis.hgetall(metrics_key))["total_bet"]) == 10
    assert float((await redis.hgetall(metrics_key))["realized_profit"]) == 8
    print("✅ 初始不变式：total_bet=10, realized=8")

    # ========== 测试 1: retail → dealer (排除) ==========
    await _adjust_metrics_for_user(mint, addr, sign=-1)
    m = await redis.hgetall(metrics_key)
    g = await redis.hgetall(user_global_key)
    assert float(m["total_bet"]) == 0, f"期望 0，实际 {m['total_bet']}"
    assert float(m["realized_profit"]) == 0, f"期望 0，实际 {m['realized_profit']}"
    assert float(m["dealer_count"]) == 1, f"期望 1，实际 {m['dealer_count']}"
    assert g["status"] == "dealer"
    assert g[f"{mint}_dealerExcluded"] == "true"
    print("✅ 测试 1: retail→dealer 后 total_bet=0, realized=0, dealer_count=1")

    # ========== 测试 2: 模拟 dealer 期间 SELL (持仓变) ==========
    await redis.hset(user_mint, mapping={
        "holdingCost": "5",   # 减少
        "totalSellAmount": "30",  # 增加卖出收入
        "totalSellPrincipal": "15",  # 增加卖出本金
    })
    # dealer 期间 SELL 不应该影响 metrics
    # 验证 metrics 不变
    m = await redis.hgetall(metrics_key)
    assert float(m["total_bet"]) == 0
    assert float(m["realized_profit"]) == 0
    print("✅ 测试 2: dealer 期间持仓变化不影响 metrics")

    # ========== 测试 3: dealer → retail (恢复) ==========
    await _adjust_metrics_for_user(mint, addr, sign=+1)
    m = await redis.hgetall(metrics_key)
    g = await redis.hgetall(user_global_key)
    # 现在 holdingCost=5, realized=30-15=15
    assert float(m["total_bet"]) == 5, f"期望 5，实际 {m['total_bet']}"
    assert float(m["realized_profit"]) == 15, f"期望 15，实际 {m['realized_profit']}"
    assert float(m["dealer_count"]) == 0
    assert g["status"] == "retail"
    assert g[f"{mint}_dealerExcluded"] == "", f"期望清空，实际 '{g[f'{mint}_dealerExcluded']}'"
    print("✅ 测试 3: dealer→retail 后 total_bet=5(最新), realized=15(最新), dealer_count=0")

    # ========== 测试 4: 再切回 dealer，再切回 ==========
    await _adjust_metrics_for_user(mint, addr, sign=-1)
    m = await redis.hgetall(metrics_key)
    assert float(m["total_bet"]) == 0
    assert float(m["realized_profit"]) == 0
    assert float(m["dealer_count"]) == 1

    await _adjust_metrics_for_user(mint, addr, sign=+1)
    m = await redis.hgetall(metrics_key)
    assert float(m["total_bet"]) == 5
    assert float(m["realized_profit"]) == 15
    assert float(m["dealer_count"]) == 0
    print("✅ 测试 4: 反复切换后不变式保持")

    # ========== 测试 5: 多用户场景 ==========
    # 重置 metrics：注意 user3 是 dealer，所以 dealer_count=1
    await redis.hset(metrics_key, mapping={
        "total_bet": "0", "realized_profit": "0", "dealer_count": "1"
    })
    # 3 个用户：user1 retail(holding=10, realized=5), user2 retail(holding=20, realized=-3), user3 dealer(holding=100, realized=50)
    for i, (stat, hc, sa, sp) in enumerate([
        ("retail", 10, 15, 10),    # user1: realized=5
        ("retail", 20, 7, 10),     # user2: realized=-3
        ("dealer", 100, 60, 10),   # user3: realized=50 (庄家不算)
    ]):
        a = f"Addr{i}"
        await redis.hset(user_mint_key(mint, a), mapping={
            "holdingCost": str(hc), "totalSellAmount": str(sa), "totalSellPrincipal": str(sp),
        })
        await redis.hset(user_key(a), mapping={
            "status": stat, "status_source": "system",
            f"{mint}_dealerExcluded": "true" if stat == "dealer" else "",
        })

    # 通过全量计算（模拟"修正路径"）：直接调 _adjust 加 user1 和 user2 两次（已 retail，无需）
    # 直接手动初始化 metrics 等于 sum(retail 用户)
    await update_metrics_delta(redis, mint, 10 + 20, 5 + (-3))
    m = await redis.hgetall(metrics_key)
    assert float(m["total_bet"]) == 30
    assert float(m["realized_profit"]) == 2  # 5 + (-3)
    print("✅ 测试 5: 多用户 SUM total_bet=30, realized=2 (dealer 不计)")

    # user3 切回 retail，应该 total_bet += 100, realized += 50
    await _adjust_metrics_for_user(mint, "Addr2", sign=+1)  # user3 是 Addr2 (dealer)
    m = await redis.hgetall(metrics_key)
    assert float(m["total_bet"]) == 130, f"期望 130，实际 {m['total_bet']}"
    assert float(m["realized_profit"]) == 52, f"期望 52，实际 {m['realized_profit']}"
    # dealer_count: 之前是 1（user3 是 dealer），切回后 -1 = 0
    assert float(m["dealer_count"]) == 0, f"期望 0，实际 {m['dealer_count']}"
    print("✅ 测试 5b: dealer→retail 后 total_bet=130, realized=52, dealer_count=0")

    print("\n🎉 全部 5 组测试通过，不变式保持正确")


if __name__ == "__main__":
    asyncio.run(test_user_lifecycle())