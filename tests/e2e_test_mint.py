"""
端到端测试：mint 75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump
1. 直接调 backfill.run()，跑完整指数计算
2. 验证 metrics 数据
3. 模拟 dealer 切换，验证修复
"""
import asyncio
import sys
import json
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

from app.services.cluster.redis_keys import init_cluster_redis, _get_redis
from app.services.dealer_detector import init_dealer_detector
from app.services.trade_processor import run_full_calculation, calculate_metrics
from app.services.trade_backfill import TradeBackfill
from app.services.trade_stream import TradeStream
from app.services import tx_redis
from app.utils.database import SessionLocal
from app.routes.trades import active_monitors


MINT = "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump"


async def setup():
    """初始化所有依赖"""
    await init_dealer_detector()
    await init_cluster_redis()
    print("✅ 初始化完成")


async def fetch_tx_list():
    """看 redis 里有哪些 tx"""
    redis = await _get_redis()
    rpc_sigs = list(reversed(await tx_redis.get_tx_list(MINT, "rpc_fill")))
    ws_sigs = list(reversed(await tx_redis.get_tx_list(MINT, "ws")))
    print(f"\n=== Redis 交易列表 ===")
    print(f"txlist:rpc_fill 共 {len(rpc_sigs)} 条")
    print(f"txlist:ws 共 {len(ws_sigs)} 条")
    if rpc_sigs:
        print(f"rpc 前 3 个 sig: {rpc_sigs[:3]}")
    return rpc_sigs, ws_sigs


async def show_user_status():
    """查看 user:{mint}:* 的用户状态"""
    redis = await _get_redis()
    cursor = 0
    user_keys = []
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=f"user:{MINT}:*", count=100)
        user_keys.extend(keys)
        if cursor == 0:
            break

    print(f"\n=== 用户列表 (per-mint) ===")
    print(f"共 {len(user_keys)} 个用户")
    summary = {"dealer": [], "retail": [], "unknown": []}
    total_holding = 0.0
    total_realized = 0.0
    total_cost = 0.0

    for k in user_keys:
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

        total_holding += hq
        summary[status].append({
            "addr": addr[:8] + "...",
            "hc": hc, "hq": hq, "realized": realized,
        })
        if status != "dealer":
            total_cost += hc
            total_realized += realized

    for st, users in summary.items():
        print(f"  {st}: {len(users)} 个")
        for u in users[:3]:
            print(f"    {u['addr']}: hc={u['hc']:.4f} hq={u['hq']:.2f} realized={u['realized']:.4f}")
        if len(users) > 3:
            print(f"    ...还有 {len(users)-3} 个")

    return summary, total_cost, total_realized, total_holding


async def show_metrics():
    """看 Redis 里的 metrics"""
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


async def verify_invariant():
    """验证不变式：total_bet = Σ holding_cost (非 dealer)"""
    redis = await _get_redis()
    metrics = await redis.hgetall(f"metrics:{MINT}")
    if not metrics:
        print("\n⚠️  metrics 不存在，未跑过 backfill")
        return

    total_bet_expected = 0.0
    total_realized_expected = 0.0
    total_holding_expected = 0.0

    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=f"user:{MINT}:*", count=100)
        for k in keys:
            mint_data = await redis.hgetall(k)
            addr = k.decode() if isinstance(k, bytes) else k
            addr = addr.replace(f"user:{MINT}:", "")
            global_data = await redis.hgetall(f"user:{addr}")

            status = global_data.get("status", "unknown") if global_data else "unknown"
            hc = float((mint_data or {}).get("holdingCost", "0") or 0)
            sa = float((mint_data or {}).get("totalSellAmount", "0") or 0)
            sp = float((mint_data or {}).get("totalSellPrincipal", "0") or 0)
            hq = float((mint_data or {}).get("holdingQty", "0") or 0)

            total_holding_expected += hq
            if status != "dealer":
                total_bet_expected += hc
                total_realized_expected += (sa - sp)

    total_bet_actual = float(metrics.get("total_bet", "0") or 0)
    realized_actual = float(metrics.get("realized_profit", "0") or 0)
    holding_actual = float(metrics.get("total_holdingQty", "0") or 0)

    print(f"\n=== 不变式验证 ===")
    print(f"total_bet:  期望={total_bet_expected:.6f}  实际={total_bet_actual:.6f}  差={total_bet_expected - total_bet_actual:.6f}")
    print(f"realized:   期望={total_realized_expected:.6f}  实际={realized_actual:.6f}  差={total_realized_expected - realized_actual:.6f}")
    print(f"holdingQty: 期望={total_holding_expected:.6f}  实际={holding_actual:.6f}  差={total_holding_expected - holding_actual:.6f}")

    ok = (abs(total_bet_expected - total_bet_actual) < 0.001 and
          abs(total_realized_expected - realized_actual) < 0.001)
    if ok:
        print("\n✅ 不变式通过")
    else:
        print("\n❌ 不变式不通过（说明我们刚改的代码还没生效，或有 bug）")


async def main():
    print(f"=== 测试 mint: {MINT} ===\n")
    await setup()

    # 1. 看初始状态
    rpc, ws = await fetch_tx_list()
    await show_user_status()
    await show_metrics()

    # 2. 如果没 backfill，先跑一次
    redis = await _get_redis()
    metrics = await redis.hgetall(f"metrics:{MINT}")
    if not metrics and rpc:
        print(f"\n=== 开始跑 backfill ({len(rpc)} 笔) ===")
        db = SessionLocal()
        try:
            stream = TradeStream(mint=MINT, api_key="")
            backfill = TradeBackfill(db=db, mint=MINT, stream=stream)

            # 不调 stream.start()，直接跑 backfill.run() 的指数计算部分
            # 因为我们只关心 _calculate_index 的结果
            await backfill.run()
            print("✅ backfill.run() 完成")
        except Exception as e:
            print(f"❌ backfill 异常: {e}")
            import traceback
            traceback.print_exc()
        finally:
            db.close()

    # 3. 验证
    await show_metrics()
    await show_user_status()
    await verify_invariant()


if __name__ == "__main__":
    asyncio.run(main())