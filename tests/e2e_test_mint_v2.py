"""
端到端测试 v2：直接调 run_full_calculation，不依赖 sync_point
"""
import asyncio
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

from app.services.cluster.redis_keys import init_cluster_redis, _get_redis
from app.services.dealer_detector import init_dealer_detector
from app.services.trade_processor import run_full_calculation, calculate_metrics
from app.utils.database import SessionLocal
from app.services import tx_redis


MINT = "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump"


async def show_metrics():
    redis = await _get_redis()
    metrics = await redis.hgetall(f"metrics:{MINT}")
    print(f"\n=== Redis metrics:{MINT[:8]}... ===")
    if not metrics:
        print("(空)")
        return None
    for k, v in metrics.items():
        try:
            print(f"  {k} = {float(v):.6f}")
        except (ValueError, TypeError):
            print(f"  {k} = {v}")
    return metrics


async def show_users():
    redis = await _get_redis()
    cursor = 0
    keys = []
    while True:
        cursor, k = await redis.scan(cursor=cursor, match=f"user:{MINT}:*", count=100)
        keys.extend(k)
        if cursor == 0:
            break

    print(f"\n=== 用户列表 ===")
    print(f"共 {len(keys)} 个")
    summary = {"dealer": [], "retail": [], "unknown": []}
    total_bet = 0.0
    total_realized = 0.0
    total_holding = 0.0

    for k in keys:
        addr = k.decode() if isinstance(k, bytes) else k
        addr = addr.replace(f"user:{MINT}:", "")
        mint_data = await redis.hgetall(k)
        global_data = await redis.hgetall(f"user:{addr}")
        status = global_data.get("status", "unknown") if global_data else "unknown"
        hc = float((mint_data or {}).get("holdingCost", "0") or 0)
        sa = float((mint_data or {}).get("totalSellAmount", "0") or 0)
        sp = float((mint_data or {}).get("totalSellPrincipal", "0") or 0)
        hq = float((mint_data or {}).get("holdingQty", "0") or 0)
        realized = sa - sp

        summary[status].append((addr[:8] + "...", hc, hq, realized))
        total_holding += hq
        if status != "dealer":
            total_bet += hc
            total_realized += realized

    for st, users in summary.items():
        print(f"  {st}: {len(users)} 个")
        for u in users:
            print(f"    {u[0]} hc={u[1]:.4f} hq={u[2]:.2f} realized={u[3]:.4f}")

    print(f"\n=== 用户层 SUM（不含 dealer）===")
    print(f"  total_bet   = {total_bet:.6f}")
    print(f"  realized    = {total_realized:.6f}")
    print(f"  holdingQty  = {total_holding:.6f}")
    return total_bet, total_realized, total_holding


async def verify(metrics):
    if not metrics:
        print("\n⚠️  metrics 不存在")
        return
    user_bet, user_realized, user_holding = await show_users()

    redis_bet = float(metrics.get("total_bet", "0") or 0)
    redis_realized = float(metrics.get("realized_profit", "0") or 0)
    redis_holding = float(metrics.get("total_holdingQty", "0") or 0)

    print(f"\n=== 不变式验证 ===")
    print(f"  total_bet       期望={user_bet:.6f}  实际={redis_bet:.6f}  差={user_bet - redis_bet:.6f}")
    print(f"  realized_profit 期望={user_realized:.6f}  实际={redis_realized:.6f}  差={user_realized - redis_realized:.6f}")
    print(f"  holdingQty      期望={user_holding:.6f}  实际={redis_holding:.6f}  差={user_holding - redis_holding:.6f}")

    if abs(user_bet - redis_bet) < 0.001 and abs(user_realized - redis_realized) < 0.001:
        print("\n✅ 不变式通过")
    else:
        print("\n❌ 不变式不通过")


async def main():
    print(f"=== E2E 测试: {MINT} ===\n")

    await init_dealer_detector()
    await init_cluster_redis()

    redis = await _get_redis()
    rpc_sigs = list(reversed(await tx_redis.get_tx_list(MINT, "rpc_fill")))
    print(f"rpc_fill: {len(rpc_sigs)} 条")

    metrics = await redis.hgetall(f"metrics:{MINT}")
    if metrics:
        print(f"\nmetrics 已存在，跳过 backfill，直接验证")
        await show_metrics()
        await verify(metrics)
        return

    # 直接调 run_full_calculation（它内部会处理 backfill 流程）
    print(f"\n=== 开始跑 run_full_calculation ===")
    db = SessionLocal()
    try:
        await run_full_calculation(db, MINT)
        print("✅ run_full_calculation 完成")
    except Exception as e:
        print(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

    metrics = await show_metrics()
    await verify(metrics)


if __name__ == "__main__":
    asyncio.run(main())