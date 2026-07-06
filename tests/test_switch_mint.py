"""
切换测试：fresh 数据 + dealer/retail 切换，验证修复
"""
import asyncio
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

from app.services.cluster.redis_keys import init_cluster_redis, _get_redis
from app.services.dealer_detector import init_dealer_detector
from app.services.trade_processor import (
    _adjust_metrics_for_user, user_key, update_metrics_delta,
)
from app.services.cluster.redis_keys import user_mint_key


MINT = "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump"


async def show_metrics():
    redis = await _get_redis()
    metrics = await redis.hgetall(f"metrics:{MINT}")
    print(f"  metrics: ", end="")
    for k in ["total_bet", "realized_profit", "dealer_count", "total_holdingQty"]:
        v = metrics.get(k, "0") or "0"
        try:
            print(f"{k}={float(v):.4f}", end="  ")
        except:
            print(f"{k}={v}", end="  ")
    print()
    return metrics


async def show_user(addr):
    redis = await _get_redis()
    global_data = await redis.hgetall(user_key(addr))
    mint_data = await redis.hgetall(user_mint_key(MINT, addr))
    status = global_data.get("status", "unknown")
    hc = float((mint_data or {}).get("holdingCost", "0") or 0)
    hq = float((mint_data or {}).get("holdingQty", "0") or 0)
    sa = float((mint_data or {}).get("totalSellAmount", "0") or 0)
    sp = float((mint_data or {}).get("totalSellPrincipal", "0") or 0)
    realized = sa - sp
    dealer_excluded = global_data.get(f"{MINT}_dealerExcluded", "")
    print(f"  user {addr[:8]}... status={status} hc={hc:.4f} hq={hq:.2f} realized={realized:.6f} dealerExcluded='{dealer_excluded}'")
    return status, hc, realized, dealer_excluded


async def main():
    print(f"=== 切换测试: {MINT} ===\n")
    await init_dealer_detector()
    await init_cluster_redis()

    redis = await _get_redis()

    # 1. 看当前 metrics 和 4 个用户
    print("【初始状态】")
    await show_metrics()
    cursor = 0
    users = []
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=f"user:{MINT}:*", count=100)
        for k in keys:
            addr = k.decode() if isinstance(k, bytes) else k
            addr = addr.replace(f"user:{MINT}:", "")
            users.append(addr)
        if cursor == 0:
            break

    print(f"  共 {len(users)} 个用户: {[u[:8] for u in users]}")
    print()

    # 2. 测试 1：把一个 unknown 用户手动标记为 dealer
    if users:
        target = users[0]
        print(f"【测试 1：{target[:8]}... 从 unknown 切到 dealer】")
        print("切之前:")
        await show_metrics()
        await show_user(target)

        await _adjust_metrics_for_user(MINT, target, sign=-1)

        print("切之后:")
        await show_metrics()
        await show_user(target)
        print()

    # 3. 测试 2：把它切回 retail
    if users:
        target = users[0]
        print(f"【测试 2：{target[:8]}... 从 dealer 切回 retail】")
        print("切之前:")
        await show_metrics()
        await show_user(target)

        await _adjust_metrics_for_user(MINT, target, sign=+1)

        print("切之后:")
        await show_metrics()
        await show_user(target)
        print()

    # 4. 测试 3：把所有 4 个用户都切到 dealer
    print(f"【测试 3：所有用户切到 dealer】")
    for u in users:
        await _adjust_metrics_for_user(MINT, u, sign=-1)
    print("切之后:")
    await show_metrics()
    print()

    # 5. 测试 4：所有用户切回 retail
    print(f"【测试 4：所有用户切回 retail】")
    for u in users:
        await _adjust_metrics_for_user(MINT, u, sign=+1)
    print("切之后:")
    await show_metrics()
    print()

    # 6. 验证最终不变式
    print("【最终不变式验证】")
    cursor = 0
    keys = []
    while True:
        cursor, k = await redis.scan(cursor=cursor, match=f"user:{MINT}:*", count=100)
        keys.extend(k)
        if cursor == 0:
            break

    expected_bet = 0.0
    expected_realized = 0.0
    for k in keys:
        addr = k.decode() if isinstance(k, bytes) else k
        addr = addr.replace(f"user:{MINT}:", "")
        global_data = await redis.hgetall(user_key(addr))
        mint_data = await redis.hgetall(k)
        status = global_data.get("status", "unknown") if global_data else "unknown"
        if status != "dealer":
            hc = float((mint_data or {}).get("holdingCost", "0") or 0)
            sa = float((mint_data or {}).get("totalSellAmount", "0") or 0)
            sp = float((mint_data or {}).get("totalSellPrincipal", "0") or 0)
            expected_bet += hc
            expected_realized += (sa - sp)

    metrics = await redis.hgetall(f"metrics:{MINT}")
    actual_bet = float(metrics.get("total_bet", "0") or 0)
    actual_realized = float(metrics.get("realized_profit", "0") or 0)

    print(f"  期望 total_bet = {expected_bet:.6f}")
    print(f"  实际 total_bet = {actual_bet:.6f}")
    print(f"  期望 realized  = {expected_realized:.6f}")
    print(f"  实际 realized  = {actual_realized:.6f}")

    if abs(expected_bet - actual_bet) < 0.001 and abs(expected_realized - actual_realized) < 0.001:
        print("\n✅ 4 次切换后不变式仍正确")
    else:
        print("\n❌ 不变式破坏！")


if __name__ == "__main__":
    asyncio.run(main())