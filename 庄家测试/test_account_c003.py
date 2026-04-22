"""庄家检测测试 v3 - 使用 C003 条件

问题：C001 检测第一笔交易，但庄家第一笔是充值，不是 Sell
解决：检测任意交易中的 CU 值（庄家 Sell 交易 CU ≈ 109,000）
"""
import asyncio
import logging
import httpx
from typing import Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

# 从账户判断.md 提取的地址
DEALER_WALLETS = [
    "F7Lyr3ho7BgVeDhsATn161rx2GbRhBh3V5a1s7TFZ8fG",
    "6EssiA1oHwrhtFAGobtSyWrh9foJVfmaXDbioihAdmh3",
    "91zFBYzxyBFbGNw4YRhn3U7WJkm7hPTwYgaYrmHRxCtR",
    "6zV9MNsXE2EJxFNvSTVknFNQi4MCN4oaCiRtG14TsZ2n",
    "3fUY8hMDaFDnWpdFZCWT9XcEZ7ZPheh9o2L7sv2XNvbh",
    "6GsfLM328dgB9QtK54JSb1N8K2BisqPMVBUpLCAcAhZQ",
    "8GmU23kLS8yATJfJxCMSjJTJSbvb3iREUFdMegnFJ7Ab",
    "6b8U75M1PXzmsi5Kv5DD5dhZFERMTPXfGckeBxJLzNPG",
    "7YgkezrtWxZ1RzepbxiiPWR8yH2UAeUjps1YVB5f3Ftb",
    "CW2nd7jjNP8T9AAPcKdq6uZ57fKDJHtF9N6bQAArHpGt",
    "9GmSd7XT3rCqKXYZq8RwMS2yL5An5o3kvDQWa82D32rq",
    "4J2VVcXrFQi2y8woNA2SXi4a3NHZvX3irNs5H7LJUqwF",
    "7zWFc6FpUTpMKbnUyJQNiF8Bp1taqJ2jyHz8aWKefDuH",
    "4ugrnVtcaXwDiazYCKXfbU1MRv9bHZod2wnc34TgCFDn",
    "q4RPes328HbgswAihSf8tCtDtDAsEDWgDPgXkQcaR4p",
    "DAxXmeY4DPYteE4UuHmZ8fTfKxZJsZqjHAqsnk5XNmDo",
]

RETAIL_WALLETS = [
    "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS",
    "9DXAFzcppSQYewAh4TQMTSpd2nvBdCXykvmRUzUmPPn9",
    "3d9yoXk2DNoajkp2WB8tYy7CXpTpJB2TY8kjcpRobDJu",
    "Gwh9U4mcXTfrGkZr1QnxmZcGDAQjW8XPS4FzUsxpDnUu",
    "HXyrdkKGayP8XreGPdQh8gMMR72eCS9G6M6VzMRtEic4",
    "BhyxowBgd7ZvC5mwKbn3V447t7oisw63KfBBSwLMt8x8",
    "6JLCzvR6xjXLx8o142AjakTgVKaikHAW99Ca6NumNbUw",
    "GDAtwgeoSik1aAnqPSehe8mgL4fZRhQmYfyxGw5tZ7tX",
    "9Kxxhq31nWSmqDjeXjyD2ziSpKQUGv16b5XzbWi59P3r",
    "BnnNJJgy9w2MLQ9XBKJKG9FQa2r9qdW7u5VpzEkwUcc3",
    "6e8Ar7VjE3R9Ggis6gXeatB2XewwiQeJgSbpfoLW1hv6",
]


def check_c003(tx_data: dict, min_cu: int = 105000, max_cu: int = 115000) -> dict:
    """C003 检测：Compute Units 在指定范围内
    
    庄家 Sell 交易的 CU 值稳定在 ~109,000
    """
    result = {
        "cu_consumed": 0,
        "in_range": False,
        "error": None
    }
    
    try:
        meta = tx_data.get("meta", {})
        cu = meta.get("computeUnitsConsumed", 0)
        result["cu_consumed"] = cu
        result["in_range"] = min_cu <= cu <= max_cu
    except Exception as e:
        result["error"] = str(e)
    
    return result


async def fetch_transactions(address: str, limit: int = 10) -> list:
    """获取钱包交易（获取多笔，不只是第一笔）"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            address,
            {
                "transactionDetails": "full",
                "sortOrder": "desc",  # 从最新到最旧
                "limit": limit,
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            },
        ],
    }
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
            data = resp.json()
            
            if "error" in data:
                return []
            
            return data.get("result", {}).get("data", [])
    except Exception as e:
        logger.error(f"获取交易失败: {e}")
        return []


async def test_wallet(address: str, expected_type: str) -> dict:
    """测试单个钱包"""
    txs = await fetch_transactions(address)
    
    if not txs:
        return {
            "address": address,
            "expected": expected_type,
            "detected": "unknown",
            "reason": "无交易记录",
            "c003_result": None,
            "has_c003": False,
            "c003_count": 0,
            "max_cu": 0,
            "match": "⚠️"  # 未知
        }
    
    # 检查所有交易，寻找 CU 在 105k-115k 范围的
    max_cu = 0
    has_c003 = False
    c003_count = 0
    
    for tx in txs[:10]:
        result = check_c003(tx)
        if result["in_range"]:
            has_c003 = True
            c003_count += 1
        if result["cu_consumed"] > max_cu:
            max_cu = result["cu_consumed"]
    
    detected = "dealer" if has_c003 else "retail"
    match = "✅" if detected == expected_type else "❌"
    
    return {
        "address": address,
        "expected": expected_type,
        "detected": detected,
        "match": match,
        "has_c003": has_c003,
        "c003_count": c003_count,
        "max_cu": max_cu
    }


async def test_dealers():
    """测试庄家钱包"""
    print("\n" + "=" * 80)
    print(" 测试庄家钱包 (预期: dealer)")
    print("=" * 80)
    
    results = []
    for wallet in DEALER_WALLETS:
        print(f"\n🔍 测试: {wallet[:20]}...")
        result = await test_wallet(wallet, "dealer")
        results.append(result)
        
        print(f"   C003 检测: has_c003={result['has_c003']}, c003_count={result['c003_count']}")
        print(f"   最大 CU: {result['max_cu']:,}")
        print(f"   判定: {result['match']} 预期={result['expected']}, 检测={result['detected']}")
        
        await asyncio.sleep(0.3)
    
    return results


async def test_retails():
    """测试散户钱包"""
    print("\n" + "=" * 80)
    print(" 测试散户钱包 (预期: retail)")
    print("=" * 80)
    
    results = []
    for wallet in RETAIL_WALLETS:
        print(f"\n🔍 测试: {wallet[:20]}...")
        result = await test_wallet(wallet, "retail")
        results.append(result)
        
        print(f"   C003 检测: has_c003={result['has_c003']}, c003_count={result['c003_count']}")
        print(f"   最大 CU: {result['max_cu']:,}")
        print(f"   判定: {result['match']} 预期={result['expected']}, 检测={result['detected']}")
        
        await asyncio.sleep(0.3)
    
    return results


async def main():
    print("=" * 80)
    print(" 庄家检测测试 v3 - 使用 C003 条件")
    print("=" * 80)
    
    # 测试庄家钱包
    dealer_results = await test_dealers()
    
    # 测试散户钱包
    retail_results = await test_retails()
    
    # 汇总
    print("\n" + "=" * 80)
    print(" 测试汇总")
    print("=" * 80)
    
    dealer_correct = sum(1 for r in dealer_results if r.get("match") == "✅")
    dealer_total = len([r for r in dealer_results if r.get("max_cu", 0) > 0 or r.get("has_c003")])
    
    retail_correct = sum(1 for r in retail_results if r.get("match") == "✅")
    retail_total = len([r for r in retail_results if r.get("max_cu", 0) > 0 or r.get("has_c003")])
    
    print(f"\n庄家钱包检测:")
    print(f"  正确识别: {dealer_correct}/{dealer_total}")
    if dealer_total > 0:
        print(f"  准确率: {dealer_correct/dealer_total*100:.1f}%")
    
    print(f"\n散户钱包检测:")
    print(f"  正确识别: {retail_correct}/{retail_total}")
    if retail_total > 0:
        print(f"  准确率: {retail_correct/retail_total*100:.1f}%")
    
    # 分析 CU 值分布
    print("\n" + "=" * 80)
    print(" CU 值分布分析")
    print("=" * 80)
    
    dealer_cu = [r["max_cu"] for r in dealer_results if r.get("max_cu", 0) > 0]
    retail_cu = [r["max_cu"] for r in retail_results if r.get("max_cu", 0) > 0]
    
    if dealer_cu:
        print(f"\n庄家钱包最大 CU:")
        print(f"  平均: {sum(dealer_cu)/len(dealer_cu):,.0f}")
        print(f"  范围: {min(dealer_cu):,} - {max(dealer_cu):,}")
        print(f"  >105k: {sum(1 for cu in dealer_cu if cu > 105000)}")
    
    if retail_cu:
        print(f"\n散户钱包最大 CU:")
        print(f"  平均: {sum(retail_cu)/len(retail_cu):,.0f}")
        print(f"  范围: {min(retail_cu):,} - {max(retail_cu):,}")
        print(f"  >105k: {sum(1 for cu in retail_cu if cu > 105000)}")


if __name__ == "__main__":
    asyncio.run(main())
