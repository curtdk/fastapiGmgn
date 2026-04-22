"""调试脚本 - 查看 API 返回的原始数据结构"""
import asyncio
import json
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

DEALER_WALLET = "6EssiA1oHwrhtFAGobtSyWrh9foJVfmaXDbioihAdmh3"  # 庄家2 - 有 Sell + closeAccount


async def debug_transaction():
    """调试交易数据结构"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            DEALER_WALLET,
            {
                "transactionDetails": "full",
                "sortOrder": "desc",
                "limit": 3,
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            print(f"API Error: {data['error']}")
            return
        
        txs = data.get("result", {}).get("data", [])
        print(f"获取到 {len(txs)} 笔交易\n")
        
        for i, tx in enumerate(txs[:2]):  # 只看前2笔
            print("=" * 80)
            print(f"交易 {i+1}")
            print("=" * 80)
            
            meta = tx.get("meta", {})
            tx_obj = tx.get("transaction", {})
            
            # 1. logs
            logs = meta.get("logs", [])
            print(f"\n📋 Logs ({len(logs)} 条):")
            for log in logs:
                # 只打印关键日志
                if "Instruction" in log or "consumed:" in log or "Program returned" in log:
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
                        print(f"      - {parsed.get('type', 'unknown')}")
            
            # 3. computeUnitsConsumed
            cu = meta.get("computeUnitsConsumed", 0)
            print(f"\n📋 Compute Units: {cu:,}")
            
            # 4. 检查是否有 closeAccount
            has_close_in_logs = any("CloseAccount" in log for log in logs)
            has_close_in_ixs = any(
                ix.get("parsed", {}).get("type") == "closeAccount"
                for ix_group in inner_ixs
                for ix in ix_group.get("instructions", [])
            )
            
            print(f"\n📋 检测结果:")
            print(f"   closeAccount in logs: {has_close_in_logs}")
            print(f"   closeAccount in innerInstructions: {has_close_in_ixs}")
            
            # 打印第一笔交易的完整 logs（用于调试）
            if i == 0:
                print(f"\n📋 完整 Logs ({len(logs)} 条):")
                for j, log in enumerate(logs):
                    if j < 50:  # 只打印前50条
                        print(f"   [{j}] {log[:100]}...")


async def main():
    print("=" * 80)
    print(f"调试钱包: {DEALER_WALLET[:20]}...")
    print("=" * 80)
    await debug_transaction()


if __name__ == "__main__":
    asyncio.run(main())
