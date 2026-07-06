"""
模拟 backfill_skip_ws_wait=2 的真实流程：调用 backfill.run()
"""
import asyncio
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

from app.services.cluster.redis_keys import init_cluster_redis, _get_redis
from app.services.dealer_detector import init_dealer_detector
from app.services.trade_backfill import TradeBackfill
from app.services.trade_stream import TradeStream
from app.utils.database import SessionLocal
from app.services import tx_redis


MINT = "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump"


async def show_status(label):
    redis = await _get_redis()
    metrics = await redis.hgetall(f"metrics:{MINT}")
    cursor = 0
    user_count = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=f"user:{MINT}:*", count=100)
        user_count += len(keys)
        if cursor == 0:
            break
    cluster_cursor = 0
    cluster_count = 0
    while True:
        cluster_cursor, ckeys = await redis.scan(cursor=cluster_cursor, match=f"cluster:data:*_BUY", count=100)
        cluster_count += len(ckeys)
        if cluster_cursor == 0:
            break
    print(f"\n[{label}] metrics: {bool(metrics)}  user:{MINT}:* = {user_count} 个  cluster:data:*_BUY = {cluster_count} 个")
    if metrics:
        for k in ["total_bet", "realized_profit", "dealer_count", "total_holdingQty"]:
            v = metrics.get(k, "0") or "0"
            try: print(f"  {k} = {float(v):.4f}")
            except: print(f"  {k} = {v}")


async def main():
    print(f"=== 模拟 backfill_skip_ws_wait=2: {MINT} ===\n")
    await init_dealer_detector()
    await init_cluster_redis()

    # 0. 确保 txlist 在 Redis
    rpc_count = await tx_redis.get_tx_count(MINT, "rpc_fill")
    ws_count = await tx_redis.get_tx_count(MINT, "ws")
    print(f"Redis 交易: rpc_fill={rpc_count}  ws={ws_count}")

    if rpc_count == 0:
        print("⚠️  没有 rpc_fill 数据，无法跑模式2")
        return

    await show_status("开始前")

    # 1. 跑 backfill
    print("\n=== 跑 backfill.run() ===")
    db = SessionLocal()
    try:
        stream = TradeStream(mint=MINT, api_key="")
        backfill = TradeBackfill(db=db, mint=MINT, stream=stream)
        await backfill.run()
        print("✅ backfill.run() 完成")
    except Exception as e:
        print(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

    await show_status("backfill 完成后")


if __name__ == "__main__":
    asyncio.run(main())