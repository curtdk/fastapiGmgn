"""测试批量获取交易"""
import asyncio
import time
import json
from datetime import datetime
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"
MINT = "5AowH4Erw73DRmcwfmSv6gGizr6cedFVcvHBAJo3pump"


async def test_get_signatures():
    """测试 getSignaturesForAddress"""
    print("=" * 50)
    print("测试 getSignaturesForAddress")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{HELIUS_RPC_URL}/?api-key={API_KEY}",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [MINT, {"limit": 10, "commitment": "confirmed"}]
            }
        )
        data = resp.json()
        sigs = data.get("result", [])
        
        print(f"第一批获取: {len(sigs)} 条")
        if sigs:
            print(f"最新签名: {sigs[0]['signature'][:20]}...")
            print(f"最早签名: {sigs[-1]['signature'][:20]}...")
            print(f"最新 slot: {sigs[0]['slot']}")
            print(f"最早 slot: {sigs[-1]['slot']}")
            
            print("\n各签名详情:")
            for i, s in enumerate(sigs):
                print(f"  [{i}] slot={s['slot']}, time={s.get('blockTime', 'N/A')}, sig={s['signature'][:30]}...")
            
            return sigs
        return []


def extract_trade_info(tx_data: dict, mint: str) -> dict:
    """从交易数据中提取交易信息"""
    try:
        sig = tx_data.get("signature", "")
        if not sig:
            return None
        
        meta = tx_data.get("meta", {})
        if meta.get("err"):
            return None
        
        block_time = tx_data.get("blockTime")
        if block_time:
            block_time = datetime.utcfromtimestamp(block_time)
        
        slot = tx_data.get("slot", 0)
        message = tx_data.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])
        
        # 获取 signer
        signer = ""
        if account_keys:
            first_key = account_keys[0]
            signer = first_key.get("pubkey", "") if isinstance(first_key, dict) else first_key
        
        # 从 pre/postTokenBalances 计算 token 变化
        pre_balances = {b["mint"]: b for b in meta.get("preTokenBalances", [])}
        post_balances = {b["mint"]: b for b in meta.get("postTokenBalances", [])}
        
        pre = pre_balances.get(mint)
        post = post_balances.get(mint)
        
        amount = 0.0
        from_addr = signer
        to_addr = ""
        tx_type = "UNKNOWN"
        dex = ""
        
        if pre and post:
            pre_amt = pre["uiTokenAmount"].get("uiAmount", 0) or 0
            post_amt = post["uiTokenAmount"].get("uiAmount", 0) or 0
            delta = post_amt - pre_amt
            amount = abs(delta)
            
            if delta > 0:
                tx_type = "BUY"
                from_addr = signer
                to_addr = post.get("owner", "")
            elif delta < 0:
                tx_type = "SELL"
                from_addr = signer
                to_addr = ""
            else:
                tx_type = "TRANSFER"
        
        # 从 inner instructions 中提取 DEX 信息
        inner_instructions = meta.get("innerInstructions", [])
        for ix_group in inner_instructions:
            for ix in ix_group.get("instructions", []):
                program_id = ix.get("programId", "")
                if "pump" in program_id.lower() or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in program_id:
                    dex = "pump.fun"
                    if tx_type == "UNKNOWN":
                        tx_type = "SWAP"
                elif "raydium" in program_id.lower() or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in program_id:
                    dex = "raydium"
                    if tx_type == "UNKNOWN":
                        tx_type = "SWAP"
                elif "orca" in program_id.lower():
                    dex = "orca"
                    if tx_type == "UNKNOWN":
                        tx_type = "SWAP"
        
        # 计算 SOL 变化
        pre_sol = meta.get("preBalances", [])
        post_sol = meta.get("postBalances", [])
        fee = meta.get("fee", 0)
        if pre_sol and post_sol and len(pre_sol) > 0:
            signer_sol_pre = pre_sol[0] / 1e9
            signer_sol_post = post_sol[0] / 1e9
            net_sol = signer_sol_post - signer_sol_pre + fee / 1e9
        else:
            net_sol = 0.0
        
        return {
            "sig": sig,
            "slot": slot,
            "block_time": block_time,
            "signer": signer,
            "amount": amount,
            "tx_type": tx_type,
            "dex": dex,
            "net_sol": round(net_sol, 9),
            "fee": fee,
        }
    except Exception as e:
        print(f"    解析失败: {e}")
        return None


async def test_get_transactions_with_details():
    """测试批量获取交易详情并打印"""
    print("\n" + "=" * 60)
    print("测试批量获取交易详情")
    print("=" * 60)
    
    # 先获取签名
    sigs = await test_get_signatures()
    if not sigs:
        return
    
    sig_list = [s["signature"] for s in sigs[:5]]
    print(f"\n批量获取 {len(sig_list)} 个交易详情...")
    
    async def fetch_tx(sig: str):
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{HELIUS_RPC_URL}/?api-key={API_KEY}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                }
            )
            data = resp.json()
            result = data.get("result")
            if result:
                result["signature"] = sig
            return result
    
    # 串行获取
    for i, sig in enumerate(sig_list):
        print(f"\n{'─' * 60}")
        print(f"交易 {i+1}: {sig[:40]}...")
        
        tx = await fetch_tx(sig)
        if not tx:
            print("  获取失败")
            continue
        
        # 提取交易信息
        trade_info = extract_trade_info(tx, MINT)
        
        if trade_info:
            print(f"  Slot:       {trade_info['slot']}")
            print(f"  时间:        {trade_info['block_time']}")
            print(f"  签名:        {trade_info['sig'][:40]}...")
            print(f"  交易类型:   {trade_info['tx_type']}")
            print(f"  DEX:        {trade_info['dex'] or 'N/A'}")
            print(f"  数量:       {trade_info['amount']}")
            print(f"  发起人:     {trade_info['signer'][:20]}...")
            print(f"  SOL 变化:  {trade_info['net_sol']}")
            print(f"  手续费:    {trade_info['fee']}")
        else:
            print("  解析失败或不是交易")
        
        # 打印部分原始数据
        print(f"\n  [原始数据结构预览]")
        meta = tx.get("meta", {})
        print(f"  preTokenBalances: {json.dumps(meta.get('preTokenBalances', [])[:2], indent=6)[:300]}...")
        print(f"  postTokenBalances: {json.dumps(meta.get('postTokenBalances', [])[:2], indent=6)[:300]}...")


async def test_concurrent_get_transactions():
    """测试并发获取交易详情"""
    print("\n" + "=" * 60)
    print("测试并发获取交易详情")
    print("=" * 60)
    
    sigs = await test_get_signatures()
    if not sigs:
        return
    
    sig_list = [s["signature"] for s in sigs[:10]]
    concurrent = 3
    
    print(f"\n并发获取 {len(sig_list)} 个交易详情 (并发数={concurrent})...")
    
    start_time = time.time()
    
    semaphore = asyncio.Semaphore(concurrent)
    
    async def fetch_tx(sig: str):
        async with semaphore:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{HELIUS_RPC_URL}/?api-key={API_KEY}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                    }
                )
                data = resp.json()
                result = data.get("result")
                if result:
                    result["signature"] = sig
                return result
    
    tasks = [fetch_tx(sig) for sig in sig_list]
    results = await asyncio.gather(*tasks)
    
    elapsed = time.time() - start_time
    success_count = sum(1 for r in results if r is not None)
    
    print(f"\n总耗时: {elapsed:.2f}s, 成功 {success_count} 条")
    
    # 打印所有交易详情
    print(f"\n{'=' * 60}")
    print(f"所有交易详情汇总 (共 {success_count} 条)")
    print("=" * 60)
    
    trade_list = []
    for tx in results:
        if tx:
            trade_info = extract_trade_info(tx, MINT)
            if trade_info:
                trade_list.append(trade_info)
    
    # 按 slot 排序打印
    trade_list.sort(key=lambda x: x["slot"])
    
    print(f"\n{'Slot':<12} {'类型':<8} {'数量':<15} {'DEX':<10} {'签名':<40}")
    print("-" * 100)
    
    for t in trade_list:
        sig_short = t["sig"][:35] + "..."
        print(f"{t['slot']:<12} {t['tx_type']:<8} {t['amount']:<15.4f} {t['dex'] or '-':<10} {sig_short}")
    
    print(f"\n总计: {len(trade_list)} 条交易")
    
    # 统计
    tx_types = {}
    dexes = {}
    for t in trade_list:
        tx_types[t["tx_type"]] = tx_types.get(t["tx_type"], 0) + 1
        dex = t["dex"] or "unknown"
        dexes[dex] = dexes.get(dex, 0) + 1
    
    print(f"\n交易类型统计:")
    for k, v in tx_types.items():
        print(f"  {k}: {v}")
    
    print(f"\nDEX 统计:")
    for k, v in dexes.items():
        print(f"  {k}: {v}")


async def test_fetch_all_signatures():
    """测试完整获取所有签名"""
    print("\n" + "=" * 60)
    print("测试完整获取所有签名")
    print("=" * 60)
    
    all_sigs = []
    before_sig = None
    batch_size = 100
    batch_count = 0
    total_time = 0
    
    while True:
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                MINT,
                {"limit": batch_size, "commitment": "confirmed"}
            ]
        }
        if before_sig:
            body["params"][1]["before"] = before_sig
        
        start_time = time.time()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{HELIUS_RPC_URL}/?api-key={API_KEY}",
                json=body
            )
        elapsed = time.time() - start_time
        total_time += elapsed
        
        data = resp.json()
        sigs = data.get("result", [])
        
        if not sigs:
            break
        
        all_sigs.extend(sigs)
        batch_count += 1
        
        print(f"批次 {batch_count}: 获取 {len(sigs)} 条, 耗时 {elapsed:.2f}s, 累计: {len(all_sigs)} 条")
        
        if len(sigs) < batch_size:
            break
        
        before_sig = sigs[-1]["signature"]
        
        if batch_count >= 20:
            print("达到最大批次限制，停止")
            break
    
    print(f"\n总计: {len(all_sigs)} 条签名, {batch_count} 批次, 总耗时 {total_time:.2f}s")
    
    if all_sigs:
        print(f"最新 slot: {all_sigs[0]['slot']}")
        print(f"最早 slot: {all_sigs[-1]['slot']}")


async def main():
    print("\n" + "=" * 60)
    print("Helius API 批量获取交易测试")
    print("=" * 60)
    
    # 1. 测试基本获取
    await test_get_signatures()
    
    # 2. 测试批量获取交易详情
    await test_get_transactions_with_details()
    
    # 3. 测试并发获取并汇总
    await test_concurrent_get_transactions()
    
    # 4. 测试完整获取
    await test_fetch_all_signatures()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
