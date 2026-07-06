"""
复现 mint 3E1Z5tEx4Z7TbzmRrhKZTVpNRfWd7a7jNHwwypQHpump 的指数计算
对比页面显示：total_bet=132.7288, realized=231.0751, current_cost=-98.3463
"""
import asyncio
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

from app.services.cluster.redis_keys import (
    user_mint_key, _get_sync_redis, init_cluster_redis_sync,
)


async def replay(mint: str):
    from app.services.cluster.redis_keys import _get_redis
    redis = await _get_redis()

    # 1. 查 redis 里这个 mint 有多少用户（持仓数据）
    r_sync = _get_sync_redis()
    user_keys = []
    cursor = 0
    while True:
        cursor, keys = r_sync.scan(cursor=cursor, match=f"user:{mint}:*", count=200)
        user_keys.extend(keys)
        if cursor == 0:
            break
    print(f"[{mint[:8]}...] user:{mint}:* 共 {len(user_keys)} 个用户")

    # 2. 模拟 SUM 计算
    total_bet = 0.0
    total_realized = 0.0
    total_holding_qty = 0.0
    dealer_excluded = 0
    unknown_count = 0
    retail_count = 0
    dealer_count = 0

    for uk in user_keys:
        addr = uk.replace(f"user:{mint}:", "")
        mint_data = r_sync.hgetall(uk)
        user_global = r_sync.hgetall(f"user:{addr}")

        if not mint_data:
            continue

        status = user_global.get("status", "unknown")
        holding_cost = float(mint_data.get("holdingCost", "0") or 0)
        holding_qty = float(mint_data.get("holdingQty", "0") or 0)
        sell_amt = float(mint_data.get("totalSellAmount", "0") or 0)
        sell_prc = float(mint_data.get("totalSellPrincipal", "0") or 0)
        user_realized = sell_amt - sell_prc

        # 计数
        if status == "dealer":
            dealer_count += 1
            dealer_excluded += 1
            # dealer 不计入 total_bet / realized
        elif status == "retail":
            retail_count += 1
            total_bet += holding_cost
            total_realized += user_realized
        else:  # unknown
            unknown_count += 1
            total_bet += holding_cost
            total_realized += user_realized

        total_holding_qty += holding_qty

    print(f"\n=== 复算结果 ===")
    print(f"散户用户数: {retail_count}")
    print(f"未知用户数: {unknown_count}")
    print(f"庄家用户数: {dealer_count}")
    print(f"total_bet (本轮下注) = {total_bet:.4f}")
    print(f"realized_profit (已落袋) = {total_realized:.4f}")
    print(f"current_cost (本轮成本) = {total_bet - total_realized:.4f}")
    print(f"total_holdingQty (含庄家) = {total_holding_qty:.4f}")

    # 3. 对比页面显示
    print(f"\n=== 页面显示 ===")
    print(f"total_bet = 132.7288")
    print(f"realized_profit = 231.0751")
    print(f"current_cost = -98.3463")

    # 4. 对比 Redis metrics 当前值
    metrics = await redis.hgetall(f"metrics:{mint}")
    print(f"\n=== Redis metrics:{mint[:8]}... ===")
    print(metrics if metrics else "(空 / 不存在)")


if __name__ == "__main__":
    init_cluster_redis_sync()
    mint = "3E1Z5tEx4Z7TbzmRrhKZTVpNRfWd7a7jNHwwypQHpump"
    asyncio.run(replay(mint))