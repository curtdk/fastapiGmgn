"""调试脚本 - 直接用签名查询交易"""
import asyncio
import json
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

# 庄家2 的 Sell 交易签名（从文档中获取）
DEALER2_SELL_SIG = "3aTX5VRKqnviFe8quEmj1LYpxdynwX7ZLfMVTvRDVdRPST3cDVvKP1oCr1dc1rtFhGWMcfFtEmPVrnLWvrb9vr6z"


async def get_tx_by_signature(sig: str):
    """用签名获取交易详情"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            sig,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "commitment": "finalized"
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            print(f"API Error: {json.dumps(data['error'], indent=2)}")
            return None
        
        return data.get("result")


async def debug_by_signature():
    """用签名调试"""
    print("=" * 80)
    print(f"用签名查询交易: {DEALER2_SELL_SIG[:40]}...")
    print("=" * 80)
    
    tx = await get_tx_by_signature(DEALER2_SELL_SIG)
    
    if not tx:
        print("❌ 获取交易失败")
        return
    
    print(f"\n✅ 获取成功!")
    print(f"   Slot: {tx.get('slot', 0):,}")
    
    meta = tx.get("meta", {})
    
    # 1. logs
    logs = meta.get("logs", [])
    print(f"\n📋 Logs ({len(logs)} 条):")
    for log in logs:
        if "Instruction" in log or "consumed:" in log:
            print(f"   {log}")
    
    # 2. innerInstructions
    inner_ixs = meta.get("innerInstructions", [])
    print(f"\n📋 InnerInstructions ({len(inner_ixs)} 个组):")
    for ig_idx, ix_group in enumerate(inner_ixs):
        ixs = ix_group.get("instructions", [])
        print(f"   组 {ig_idx}: {len(ixs)} 个指令")
        for ix in ixs:
            parsed = ix.get("parsed", {})
            if parsed:
                print(f"      - type: {parsed.get('type', 'unknown')}")
                print(f"        program: {ix.get('programId', '')}")
    
    # 3. computeUnitsConsumed
    cu = meta.get("computeUnitsConsumed", 0)
    print(f"\n📋 Compute Units: {cu:,}")
    
    # 4. 检查是否有 closeAccount
    has_close_in_logs = any("CloseAccount" in log for log in logs)
    has_close_in_ixs = any(
        ix.get("parsed", {}).get("type", "").lower() == "closeaccount"
        for ix_group in inner_ixs
        for ix in ix_group.get("instructions", [])
    )
    
    print(f"\n📋 检测结果:")
    print(f"   closeAccount in logs: {has_close_in_logs}")
    print(f"   closeAccount in innerInstructions: {has_close_in_ixs}")
    
    # 检查日志中的关键特征
    print(f"\n📋 特征检查:")
    print(f"   'Instruction: Sell' in logs: {any('Sell' in log for log in logs)}")
    print(f"   'Instruction: SetComputeUnitPrice' in logs: {any('SetComputeUnitPrice' in log for log in logs)}")
    print(f"   'Instruction: SetComputeUnitLimit' in logs: {any('SetComputeUnitLimit' in log for log in logs)}")


async def get_wallet_txs_with_getTransaction(address: str):
    """使用 getTransaction 获取钱包的所有交易"""
    print("\n" + "=" * 80)
    print(f"使用 getTransaction 重新获取钱包交易: {address[:20]}...")
    print("=" * 80)
    
    # 首先获取签名列表
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            address,
            {"limit": 5}
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            print(f"API Error: {data['error']}")
            return
        
        sigs = data.get("result", [])
        print(f"获取到 {len(sigs)} 个签名\n")
        
        # 逐个用 getTransaction 获取详情
        for i, sig_info in enumerate(sigs[:3]):
            sig = sig_info.get("signature", "")
            print(f"--- 签名 {i+1}: {sig[:40]}... ---")
            
            tx = await get_tx_by_signature(sig)
            if tx:
                meta = tx.get("meta", {})
                cu = meta.get("computeUnitsConsumed", 0)
                logs_count = len(meta.get("logs", []))
                print(f"   CU: {cu:,}, Logs: {logs_count} 条")
                
                # 检查庄家特征
                logs = meta.get("logs", [])
                has_sell = any("Instruction: Sell" in log for log in logs)
                has_close = any("CloseAccount" in log for log in logs)
                print(f"   Has Sell: {has_sell}, Has CloseAccount: {has_close}")
            print()


async def main():
    # 1. 用签名直接查询
    await debug_by_signature()
    
    # 2. 用 getTransaction 获取钱包交易
    wallet = "6EssiA1oHwrhtFAGobtSyWrh9foJVfmaXDbioihAdmh3"
    await get_wallet_txs_with_getTransaction(wallet)


if __name__ == "__main__":
    asyncio.run(main())
