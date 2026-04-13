"""
SIG 顺序验证测试
对比 Solana RPC 真实顺序 vs 数据库存储顺序
验证 WSS 接收数据是否正确写入数据库
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

import httpx

# 添加项目根目录到 path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils.database import SessionLocal, engine, Base
from app.models.models import Transaction
from app.models.trade import TradeAnalysis, Setting

# 确保表存在
Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
TEST_MINT = "9hK5hP651A7GSwR7jdBD7SFoo5ut4BYLeCj1LhBipump"


def get_api_key() -> str:
    """从数据库设置或环境变量获取 API Key"""
    db = SessionLocal()
    try:
        setting = db.query(Setting).filter(Setting.key == "helius_api_key").first()
        if setting and setting.value:
            return setting.value
    finally:
        db.close()
    return os.getenv("HELIUS_API_KEY", "")


async def fetch_rpc_signatures(mint: str) -> list[dict]:
    """从 Solana RPC 获取真实签名列表（从新到旧）"""
    api_key = get_api_key()
    all_sigs = []
    before_sig = None
    batch_size = 1000

    while True:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": batch_size, "commitment": "confirmed"}],
        }
        if before_sig:
            body["params"][1]["before"] = before_sig

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={api_key}", json=body)
            resp.raise_for_status()
            data = resp.json()

        sigs = data.get("result", [])
        if not sigs:
            break

        all_sigs.extend(sigs)

        if len(sigs) < batch_size:
            break

        before_sig = sigs[-1]["signature"]

    return all_sigs


def get_db_signatures(mint: str) -> list[dict]:
    """从数据库获取签名列表（按 slot 降序，同 slot 按 id 降序）"""
    db = SessionLocal()
    try:
        txs = (
            db.query(Transaction)
            .filter(Transaction.token_mint == mint)
            .order_by(Transaction.slot.desc(), Transaction.id.desc())
            .all()
        )
        return [{"sig": tx.sig, "slot": tx.slot, "block_time": tx.block_time, "source": tx.source, "id": tx.id} for tx in txs]
    finally:
        db.close()


def get_db_signatures_asc(mint: str) -> list[dict]:
    """从数据库获取签名列表（按 slot 升序，即从旧到新）"""
    db = SessionLocal()
    try:
        txs = (
            db.query(Transaction)
            .filter(Transaction.token_mint == mint)
            .order_by(Transaction.slot.asc(), Transaction.id.asc())
            .all()
        )
        return [{"sig": tx.sig, "slot": tx.slot, "block_time": tx.block_time, "source": tx.source, "id": tx.id} for tx in txs]
    finally:
        db.close()


async def run_backfill(mint: str):
    """运行回填引擎"""
    from app.services.trade_backfill import TradeBackfill

    db = SessionLocal()
    try:
        backfill = TradeBackfill(db=db, mint=mint)
        await backfill.run()
        logger.info(f"回填完成: 共 {backfill.total_fetched} 条")
        return backfill.total_fetched
    finally:
        db.close()


def compare_order(rpc_sigs: list[dict], db_sigs: list[dict], db_sigs_asc: list[dict]):
    """对比 RPC 和数据库的顺序"""
    results = []
    results.append("=" * 80)
    results.append(f"SIG 顺序验证测试 - Mint: {TEST_MINT}")
    results.append(f"测试时间: {datetime.now().isoformat()}")
    results.append("=" * 80)
    results.append("")

    # 过滤掉 RPC 中失败的交易（err != None）
    rpc_success = [s for s in rpc_sigs if s.get("err") is None]
    rpc_failed = [s for s in rpc_sigs if s.get("err") is not None]

    # 1. 数量对比
    results.append("【1. 数量对比】")
    results.append(f"  RPC 总签名数: {len(rpc_sigs)}")
    results.append(f"  RPC 成功交易: {len(rpc_success)}")
    results.append(f"  RPC 失败交易: {len(rpc_failed)}（被系统正确跳过）")
    results.append(f"  数据库记录数: {len(db_sigs)}")
    results.append(f"  差异（成功交易 - DB）: {len(rpc_success) - len(db_sigs)}")
    results.append("")

    # 2. 完整性检查（只检查成功的交易）
    results.append("【2. 完整性检查 - 成功的 RPC 签名是否全部入库】")
    rpc_sig_set = {s["signature"] for s in rpc_success}
    db_sig_set = {s["sig"] for s in db_sigs}

    missing_in_db = rpc_sig_set - db_sig_set
    extra_in_db = db_sig_set - rpc_sig_set

    if missing_in_db:
        results.append(f"  缺失（RPC 成功但 DB 无）: {len(missing_in_db)} 条")
        for sig in list(missing_in_db)[:5]:
            results.append(f"    - {sig[:16]}...")
        if len(missing_in_db) > 5:
            results.append(f"    ... 还有 {len(missing_in_db) - 5} 条")
    else:
        results.append("  通过: 所有成功的 RPC 签名均已入库")

    if extra_in_db:
        results.append(f"  多余（DB 有但 RPC 无）: {len(extra_in_db)} 条")
        for sig in list(extra_in_db)[:5]:
            results.append(f"    - {sig[:16]}...")
    else:
        results.append("  通过: 数据库无多余签名")

    # 记录失败的交易
    if rpc_failed:
        results.append("")
        results.append("  被跳过的失败交易:")
        for s in rpc_failed:
            results.append(f"    - {s['signature'][:16]}...  slot={s.get('slot', 0)}  err={s['err']}")
    results.append("")

    # 3. 顺序对比（从新到旧，RPC 默认顺序）
    results.append("【3. 顺序对比（从新到旧）】")
    rpc_list = [s["signature"] for s in rpc_success]
    db_list = [s["sig"] for s in db_sigs]

    common_sigs = [s for s in rpc_list if s in db_sig_set]
    db_common = [s for s in db_list if s in rpc_sig_set]

    order_match = common_sigs == db_common
    results.append(f"  交集签名数: {len(common_sigs)}")
    results.append(f"  顺序是否一致: {'是' if order_match else '否'}")

    if not order_match:
        for i, (r, d) in enumerate(zip(common_sigs, db_common)):
            if r != d:
                results.append(f"  第一个差异位置: 索引 {i}")
                results.append(f"    RPC 顺序: {r[:16]}...")
                results.append(f"    DB  顺序: {d[:16]}...")
                break
    results.append("")

    # 4. Slot 单调性检查
    results.append("【4. 数据库 Slot 单调性检查】")
    slots_desc = [s["slot"] for s in db_sigs]
    is_monotonic_desc = all(slots_desc[i] >= slots_desc[i + 1] for i in range(len(slots_desc) - 1))
    results.append(f"  降序单调性: {'通过' if is_monotonic_desc else '失败'}")

    slots_asc = [s["slot"] for s in db_sigs_asc]
    is_monotonic_asc = all(slots_asc[i] <= slots_asc[i + 1] for i in range(len(slots_asc) - 1))
    results.append(f"  升序单调性: {'通过' if is_monotonic_asc else '失败'}")
    results.append("")

    # 5. Slot 对应关系
    results.append("【5. Slot 对应关系（前 10 条）】")
    results.append(f"  {'#':>3}  {'RPC Slot':>15}  {'DB Slot':>15}  {'一致':>6}  {'RPC Sig':>16}  {'DB Sig':>16}")
    results.append(f"  {'-'*3}  {'-'*15}  {'-'*15}  {'-'*6}  {'-'*16}  {'-'*16}")

    for i in range(min(10, len(rpc_success))):
        rpc_sig = rpc_success[i]["signature"]
        rpc_slot = rpc_success[i].get("slot", 0)
        if i < len(db_sigs):
            db_sig = db_sigs[i]["sig"]
            db_slot = db_sigs[i]["slot"] or 0
            match = "OK" if rpc_slot == db_slot else "MISMATCH"
            results.append(f"  {i+1:>3}  {rpc_slot:>15}  {db_slot:>15}  {match:>6}  {rpc_sig[:16]:>16}  {db_sig[:16]:>16}")
        else:
            results.append(f"  {i+1:>3}  {rpc_slot:>15}  {'N/A':>15}  {'MISS':>6}  {rpc_sig[:16]:>16}  {'N/A':>16}")
    results.append("")

    # 6. WSS 实时写入验证
    results.append("【6. WSS 实时写入验证】")
    results.append("  按 source 分类:")
    ws_sigs = [s for s in db_sigs if s.get("source") == "helius_ws"]
    rpc_sigs_source = [s for s in db_sigs if s.get("source") == "solana_rpc"]
    results.append(f"  回填入库记录数 (solana_rpc): {len(rpc_sigs_source)}")
    results.append(f"  WSS 入库记录数 (helius_ws): {len(ws_sigs)}")

    if ws_sigs:
        results.append(f"  最新 WSS 记录: sig={ws_sigs[0]['sig'][:16]}..., slot={ws_sigs[0]['slot']}")
        results.append(f"  最旧 WSS 记录: sig={ws_sigs[-1]['sig'][:16]}..., slot={ws_sigs[-1]['slot']}")

        ws_sig_set = {s["sig"] for s in ws_sigs}
        rpc_sig_set_full = {s["signature"] for s in rpc_sigs}
        ws_in_rpc = ws_sig_set & rpc_sig_set_full
        ws_not_in_rpc = ws_sig_set - rpc_sig_set_full
        results.append(f"  WSS 记录在 RPC 列表中: {len(ws_in_rpc)} 条")
        if ws_not_in_rpc:
            results.append(f"  WSS 记录不在 RPC 中: {len(ws_not_in_rpc)} 条（回填后新增的实时交易）")
    else:
        results.append("  无 WSS 记录（未启动实时流）")
    results.append("")

    # 7. 总结
    results.append("=" * 80)
    results.append("【总结】")
    total_ok = len(missing_in_db) == 0 and order_match and is_monotonic_desc
    results.append(f"  完整性: {'通过' if len(missing_in_db) == 0 else '失败'}")
    results.append(f"  顺序一致性: {'通过' if order_match else '失败'}")
    results.append(f"  Slot 单调性: {'通过' if is_monotonic_desc else '失败'}")
    results.append(f"  失败交易过滤: {'通过' if len(rpc_failed) > 0 else 'N/A（无失败交易）'}")
    results.append(f"  总体: {'全部通过' if total_ok else '存在问题'}")
    results.append("=" * 80)

    return "\n".join(results)


async def main():
    logger.info(f"开始测试 Mint: {TEST_MINT}")

    # 1. 先运行回填
    logger.info("===== 步骤 1: 运行回填引擎 =====")
    total = await run_backfill(TEST_MINT)
    logger.info(f"回填完成: {total} 条")

    # 等待回填完全写入
    await asyncio.sleep(2)

    # 2. 从 RPC 获取真实签名（回填后的快照）
    logger.info("===== 步骤 2: 从 Solana RPC 获取真实签名 =====")
    rpc_sigs = await fetch_rpc_signatures(TEST_MINT)
    logger.info(f"RPC 获取完成: {len(rpc_sigs)} 条")

    # 3. 从数据库获取签名
    logger.info("===== 步骤 3: 从数据库获取签名 =====")
    db_sigs = get_db_signatures(TEST_MINT)
    db_sigs_asc = get_db_signatures_asc(TEST_MINT)
    logger.info(f"DB 查询完成: {len(db_sigs)} 条（降序）, {len(db_sigs_asc)} 条（升序）")

    # 4. 对比
    logger.info("===== 步骤 4: 对比分析 =====")
    report = compare_order(rpc_sigs, db_sigs, db_sigs_asc)
    print(report)

    # 5. 写入结果文件
    output_dir = os.path.join(os.path.dirname(__file__), "..", "dk")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "sig_order_test_result.md")
    with open(output_path, "w") as f:
        f.write(report)
    logger.info(f"结果已写入: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
