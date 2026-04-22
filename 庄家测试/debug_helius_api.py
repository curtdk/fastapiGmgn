"""调试脚本 - 使用 Helius 增强 API"""
import asyncio
import json
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

DEALER2_SELL_SIG = "3aTX5VRKqnviFe8quEmj1LYpxdynwX7ZLfMVTvRDVdRPST3cDVvKP1oCr1dc1rtFhGWMcfFtEmPVrnLWvrb9vr6z"


async def get_helius_enriched_tx(sig: str):
    """使用 Helius enriched transaction API"""
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
        ],
        "extra": {
            "解析": True,
            "include_inner_instructions": True,
            "include_perf_data": True
        }
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        return resp.json()


async def get_tx_with_full_logs(sig: str):
    """获取完整 logs"""
    # 使用 standard RPC 方式
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            sig,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "commitment": "finalized",
                "rewards": False
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        return resp.json()


async def get_tx_raw_response(sig: str):
    """直接看原始响应"""
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
        
        # 打印完整响应（限制长度）
        result = data.get("result")
        if result:
            meta = result.get("meta", {})
            logs = meta.get("logs", [])
            print(f"Logs count: {len(logs)}")
            
            if not logs:
                print("\n❌ Logs 为空！检查其他字段...")
                print(f"Meta keys: {list(meta.keys())}")
                
                # 检查 transaction
                tx = result.get("transaction", {})
                print(f"\nTransaction keys: {list(tx.keys()) if isinstance(tx, dict) else 'list'}")
                
                # 检查 token balances - 可能有信息
                pre_token = meta.get("preTokenBalances", [])
                post_token = meta.get("postTokenBalances", [])
                print(f"\nPreTokenBalances: {len(pre_token)}")
                print(f"PostTokenBalances: {len(post_token)}")
                
                if post_token:
                    print("\nToken changes:")
                    for bal in post_token:
                        print(f"   {bal.get('owner', '')[:20]}... mint={bal.get('mint', '')[:20]}")
            else:
                print("\n✅ 有 logs!")
                for log in logs[:30]:
                    print(f"   {log}")
        else:
            print(f"No result: {data}")


async def main():
    sig = DEALER2_SELL_SIG
    print("=" * 80)
    print(f"调试 Helius API - 签名: {sig[:40]}...")
    print("=" * 80)
    
    await get_tx_raw_response(sig)


if __name__ == "__main__":
    asyncio.run(main())
