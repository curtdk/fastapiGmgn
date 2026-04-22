"""庄家检测测试 - 验证账户判断.md 中的地址

目的：测试 mint 交易中的钱包哪些是被庄家批量操作的账户
使用现有代码的 C001 条件进行检测
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


def check_c001(tx_data: dict, address: str) -> dict:
    """C001 检测：首笔交易包含 closeAccount 指令 且 交易后 SOL 余额为 0
    
    返回检测结果详情
    """
    result = {
        "has_close_account": False,
        "sol_balance_zero": False,
        "is_dealer": False,
        "cu_consumed": 0,
        "sol_change": 0,
        "error": None
    }
    
    try:
        meta = tx_data.get("meta", {})
        if meta.get("err"):
            result["error"] = "交易失败"
            return result
        
        # 检测 closeAccount 指令
        for ix_group in meta.get("innerInstructions", []):
            for ix in ix_group.get("instructions", []):
                ix_type = ix.get("parsed", {}).get("type", "")
                if ix_type in ("closeAccount", "CloseAccount"):
                    result["has_close_account"] = True
                    break
        
        # 检测交易后 SOL 余额
        inner_tx = tx_data.get("transaction", {})
        message = inner_tx.get("message", {})
        account_keys = message.get("accountKeys", [])
        
        # 找 address 在 accountKeys 中的 index
        addr_index = None
        for i, key in enumerate(account_keys):
            pubkey = key.get("pubkey", "") if isinstance(key, dict) else key
            if pubkey == address:
                addr_index = i
                break
        
        if addr_index is not None:
            post_balances = meta.get("postBalances", [])
            pre_balances = meta.get("preBalances", [])
            
            if addr_index < len(post_balances):
                result["sol_balance_zero"] = post_balances[addr_index] == 0
                result["sol_change"] = (post_balances[addr_index] - pre_balances[addr_index]) / 1e9 if addr_index < len(pre_balances) else 0
        
        # C001 判定
        result["is_dealer"] = result["has_close_account"] and result["sol_balance_zero"]
        
        # 记录 CU
        result["cu_consumed"] = meta.get("computeUnitsConsumed", 0)
        
    except Exception as e:
        result["error"] = str(e)
    
    return result


async def fetch_first_transaction(address: str) -> Optional[dict]:
    """获取钱包最旧一笔成功交易"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            address,
            {
                "transactionDetails": "full",
                "sortOrder": "asc",
                "limit": 1,
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "filters": {"status": "succeeded"},
            },
        ],
    }
    
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
            data = resp.json()
            
            if "error" in data:
                return None
            
            txs = data.get("result", {}).get("data", [])
            return txs[0] if txs else None
    except Exception as e:
        logger.error(f"获取交易失败: {e}")
        return None


async def test_wallet(address: str, expected_type: str) -> dict:
    """测试单个钱包"""
    tx = await fetch_first_transaction(address)
    
    if not tx:
        return {
            "address": address,
            "expected": expected_type,
            "detected": "unknown",
            "reason": "无交易记录",
            "c001": None
        }
    
    c001_result = check_c001(tx, address)
    
    detected = "dealer" if c001_result["is_dealer"] else "retail"
    match = "✅" if detected == expected_type else "❌"
    
    return {
        "address": address,
        "expected": expected_type,
        "detected": detected,
        "match": match,
        "c001": c001_result
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
        
        if result["c001"]:
            c001 = result["c001"]
            print(f"   C001 检测结果:")
            print(f"      closeAccount: {c001['has_close_account']}")
            print(f"      SOL余额=0: {c001['sol_balance_zero']}")
            print(f"      CU: {c001['cu_consumed']:,}")
            print(f"      SOL变化: {c001['sol_change']:+.9f}")
            print(f"   判定: {result['match']} 预期={result['expected']}, 检测={result['detected']}")
        else:
            print(f"   错误: {result['reason']}")
        
        await asyncio.sleep(0.3)  # 避免请求过快
    
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
        
        if result["c001"]:
            c001 = result["c001"]
            print(f"   C001 检测结果:")
            print(f"      closeAccount: {c001['has_close_account']}")
            print(f"      SOL余额=0: {c001['sol_balance_zero']}")
            print(f"      CU: {c001['cu_consumed']:,}")
            print(f"      SOL变化: {c001['sol_change']:+.9f}")
            print(f"   判定: {result['match']} 预期={result['expected']}, 检测={result['detected']}")
        else:
            print(f"   错误: {result['reason']}")
        
        await asyncio.sleep(0.3)
    
    return results


async def main():
    print("=" * 80)
    print(" 庄家检测测试 - 使用 C001 条件验证账户判断.md")
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
    dealer_total = len(dealer_results)
    
    retail_correct = sum(1 for r in retail_results if r.get("match") == "✅")
    retail_total = len(retail_results)
    
    print(f"\n庄家钱包检测:")
    print(f"  正确识别: {dealer_correct}/{dealer_total}")
    print(f"  准确率: {dealer_correct/dealer_total*100:.1f}%")
    
    print(f"\n散户钱包检测:")
    print(f"  正确识别: {retail_correct}/{retail_total}")
    print(f"  准确率: {retail_correct/retail_total*100:.1f}%")
    
    # 分析 C001 条件的问题
    print("\n" + "=" * 80)
    print(" C001 条件分析")
    print("=" * 80)
    
    dealer_c001 = [r for r in dealer_results if r.get("c001")]
    if dealer_c001:
        close_count = sum(1 for r in dealer_c001 if r["c001"]["has_close_account"])
        zero_count = sum(1 for r in dealer_c001 if r["c001"]["sol_balance_zero"])
        print(f"\n庄家钱包 closeAccount 检测: {close_count}/{len(dealer_c001)}")
        print(f"庄家钱包 SOL余额=0 检测: {zero_count}/{len(dealer_c001)}")
        
        # 统计 CU 值
        cu_values = [r["c001"]["cu_consumed"] for r in dealer_c001 if r["c001"]["cu_consumed"] > 0]
        if cu_values:
            avg_cu = sum(cu_values) / len(cu_values)
            print(f"庄家钱包平均 CU: {avg_cu:,.0f}")
            print(f"庄家钱包 CU 范围: {min(cu_values):,} - {max(cu_values):,}")


if __name__ == "__main__":
    asyncio.run(main())
