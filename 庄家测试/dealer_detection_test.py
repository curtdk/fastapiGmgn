"""庄家检测测试 - 验证新检测条件 C002/C003/C004"""
import asyncio
import json
import httpx
from typing import List, Dict, Optional

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

# 测试钱包
DEALER_WALLETS = [
    "F7Lyr3ho7BgVeDhsATn161rx2GbRhBh3V5a1s7TFZ8fG",  # 庄家1 - 充值
    "6EssiA1oHwrhtFAGobtSyWrh9foJVfmaXDbioihAdmh3",  # 庄家2 - Sell
    "91zFBYzxyBFbGNw4YRhn3U7WJkm7hPTwYgaYrmHRxCtR",  # 庄家3
    "6zV9MNsXE2EJxFNvSTVknFNQi4MCN4oaCiRtG14TsZ2n",  # 庄家
    "3fUY8hMDaFDnWpdFZCWT9XcEZ7ZPheh9o2L7sv2XNvbh",  # 庄家
    "6GsfLM328dgB9QtK54JSb1N8K2BisqPMVBUpLCAcAhZQ",  # 庄家
    "8GmU23kLS8yATJfJxCMSjJTJSbvb3iREUFdMegnFJ7Ab",  # 庄家
    "6b8U75M1PXzmsi5Kv5DD5dhZFERMTPXfGckeBxJLzNPG",  # 庄家
    "7YgkezrtWxZ1RzepbxiiPWR8yH2UAeUjps1YVB5f3Ftb",  # 庄家
    "CW2nd7jjNP8T9AAPcKdq6uZ57fKDJHtF9N6bQAArHpGt",  # 庄家
    "9GmSd7XT3rCqKXYZq8RwMS2yL5An5o3kvDQWa82D32rq",  # 庄家
    "4J2VVcXrFQi2y8woNA2SXi4a3NHZvX3irNs5H7LJUqwF",  # 庄家4 - Sell
    "7zWFc6FpUTpMKbnUyJQNiF8Bp1taqJ2jyHz8aWKefDuH",  # 庄家
    "4ugrnVtcaXwDiazYCKXfbU1MRv9bHZod2wnc34TgCFDn",  # 庄家
    "q4RPes328HbgswAihSf8tCtDtDAsEDWgDPgXkQcaR4p",  # 庄家
    "DAxXmeY4DPYteE4UuHmZ8fTfKxZJsZqjHAqsnk5XNmDo",  # 庄家
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


def extract_instructions(tx_data: dict) -> dict:
    """从交易中提取指令信息"""
    try:
        tx = tx_data.get("transaction", {})
        meta = tx_data.get("meta", {})
        inner_ixs = meta.get("innerInstructions", [])
        
        instructions = []
        for ix_group in inner_ixs:
            for ix in ix_group.get("instructions", []):
                parsed = ix.get("parsed", {})
                program_id = ix.get("programId", "")
                ix_type = parsed.get("type", "")
                instructions.append({
                    "program": program_id,
                    "type": ix_type,
                    "raw": ix
                })
        
        # 计算总 Compute Units
        cu_consumed = meta.get("computeUnitsConsumed", 0)
        
        # 提取 Priority Fee
        compute_budget_ixs = []
        for ix in tx.get("message", {}).get("instructions", []):
            if "ComputeBudget" in str(ix.get("programId", "")):
                compute_budget_ixs.append(ix)
        
        # 从 program logs 中提取指令
        program_logs = meta.get("logs", [])
        
        return {
            "instructions": instructions,
            "cu_consumed": cu_consumed,
            "compute_budget_ixs": compute_budget_ixs,
            "success": not meta.get("err"),
            "program_logs": program_logs
        }
    except Exception as e:
        return {"error": str(e)}


def check_c002_has_close_account(info: dict) -> bool:
    """C002: Sell 交易包含 closeAccount 指令
    
    检测方式：
    1. innerInstructions 中有 closeAccount 指令
    2. program logs 中有 "Instruction: CloseAccount"
    """
    # 方式1: 检查 innerInstructions
    for ix in info.get("instructions", []):
        if ix["type"] in ("closeAccount", "CloseAccount"):
            return True
    
    # 方式2: 检查 program logs
    logs = info.get("program_logs", [])
    for log in logs:
        if "Instruction: CloseAccount" in log or "Instruction: closeAccount" in log:
            return True
    
    return False


def check_c003_cu_range(info: dict, min_cu: int = 105000, max_cu: int = 115000) -> bool:
    """C003: Compute Units 在指定范围内"""
    cu = info.get("cu_consumed", 0)
    return min_cu <= cu <= max_cu


def check_c004_five_instructions(info: dict) -> bool:
    """C004: 固定 5 个指令序列"""
    # 庄家标准 Sell 序列：
    # 1. SetComputeUnitPrice
    # 2. SetComputeUnitLimit
    # 3. createIdempotent
    # 4. sell
    # 5. closeAccount
    ixs = info.get("instructions", [])
    if len(ixs) != 5:
        return False
    
    expected_types = ["SetComputeUnitPrice", "SetComputeUnitLimit", "createIdempotent", "sell", "closeAccount"]
    actual_types = [ix.get("type", "") for ix in ixs]
    
    # 简化检查：至少包含 closeAccount 和 sell
    has_sell = any("sell" in t.lower() for t in actual_types)
    has_close = any("close" in t.lower() for t in actual_types)
    
    return has_sell and has_close


def is_dealer_by_conditions(info: dict) -> tuple:
    """综合判断是否为庄家"""
    conditions = []
    
    has_close = check_c002_has_close_account(info)
    if has_close:
        conditions.append("C002")
    
    in_cu_range = check_c003_cu_range(info)
    if in_cu_range:
        conditions.append("C003")
    
    has_five = check_c004_five_instructions(info)
    if has_five:
        conditions.append("C004")
    
    # 庄家判定：满足 C002 + (C003 或 C004)
    is_dealer = has_close and (in_cu_range or has_five)
    
    return is_dealer, conditions


async def get_recent_transactions(address: str, limit: int = 5) -> List[dict]:
    """获取钱包最近的交易"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            address,
            {
                "transactionDetails": "full",
                "sortOrder": "desc",
                "limit": limit,
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            return []
        
        return data.get("result", {}).get("data", [])


async def get_first_transaction(address: str) -> Optional[dict]:
    """获取钱包的第一笔交易"""
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
                "filters": {"status": "succeeded"}
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            return None
        
        txs = data.get("result", {}).get("data", [])
        return txs[0] if txs else None


async def test_dealer_wallets():
    """测试庄家钱包"""
    print("\n" + "=" * 80)
    print(" 测试庄家钱包")
    print("=" * 80)
    
    results = []
    
    for wallet in DEALER_WALLETS[:5]:  # 只测试前5个
        print(f"\n🔍 测试钱包: {wallet[:20]}...")
        
        # 获取最近交易
        recent_txs = await get_recent_transactions(wallet, 3)
        
        if not recent_txs:
            print(f"   ❌ 无交易记录")
            results.append({"wallet": wallet, "status": "no_txs"})
            continue
        
        dealer_found = False
        for i, tx in enumerate(recent_txs):
            info = extract_instructions(tx)
            if "error" in info:
                continue
            
            is_dealer, conditions = is_dealer_by_conditions(info)
            
            if is_dealer:
                dealer_found = True
                print(f"   ✅ 命中庄家特征! 条件: {conditions}")
                print(f"      CU: {info.get('cu_consumed', 0):,}")
                print(f"      指令数: {len(info.get('instructions', []))}")
                results.append({
                    "wallet": wallet,
                    "status": "dealer",
                    "conditions": conditions,
                    "cu": info.get('cu_consumed', 0)
                })
                break
        
        if not dealer_found:
            print(f"   ⚠️  未命中庄家特征")
            results.append({"wallet": wallet, "status": "not_dealer"})
    
    return results


async def test_retail_wallets():
    """测试散户钱包"""
    print("\n" + "=" * 80)
    print(" 测试散户钱包")
    print("=" * 80)
    
    results = []
    
    for wallet in RETAIL_WALLETS[:5]:  # 只测试前5个
        print(f"\n🔍 测试钱包: {wallet[:20]}...")
        
        recent_txs = await get_recent_transactions(wallet, 3)
        
        if not recent_txs:
            print(f"   ❌ 无交易记录")
            results.append({"wallet": wallet, "status": "no_txs"})
            continue
        
        dealer_found = False
        for i, tx in enumerate(recent_txs):
            info = extract_instructions(tx)
            if "error" in info:
                continue
            
            is_dealer, conditions = is_dealer_by_conditions(info)
            
            if is_dealer:
                dealer_found = True
                print(f"   ⚠️  误判为庄家! 条件: {conditions}")
                results.append({
                    "wallet": wallet,
                    "status": "false_positive",
                    "conditions": conditions
                })
                break
        
        if not dealer_found:
            print(f"   ✅ 正确识别为散户")
            results.append({"wallet": wallet, "status": "retail"})
    
    return results


async def analyze_transfer_source():
    """分析充值来源（母钱包追踪）"""
    print("\n" + "=" * 80)
    print(" 母钱包追踪分析")
    print("=" * 80)
    
    # 庄家1 的第一笔交易是充值
    wallet = "F7Lyr3ho7BgVeDhsATn161rx2GbRhBh3V5a1s7TFZ8fG"
    
    first_tx = await get_first_transaction(wallet)
    if not first_tx:
        print("❌ 获取第一笔交易失败")
        return
    
    tx = first_tx.get("transaction", {})
    meta = first_tx.get("meta", {})
    
    # 提取 Transfer 信息
    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])
    
    print(f"\n💰 第一笔交易分析:")
    print(f"   签名: {tx.get('signatures', [''])[0][:40]}...")
    print(f"   Slot: {first_tx.get('slot', 0):,}")
    
    # 计算 SOL 变化
    if pre_balances and post_balances:
        sol_change = (post_balances[0] - pre_balances[0]) / 1e9
        print(f"   SOL 变化: {sol_change:+.9f} SOL")
    
    # 分析所有参与地址
    account_keys = tx.get("message", {}).get("accountKeys", [])
    if len(account_keys) >= 2:
        print(f"   发送方: {account_keys[0].get('pubkey', '') if isinstance(account_keys[0], dict) else account_keys[0]}")
        print(f"   接收方: {account_keys[1].get('pubkey', '') if isinstance(account_keys[1], dict) else account_keys[1]}")


async def main():
    print("=" * 80)
    print(" 庄家检测测试 - C002/C003/C004 条件验证")
    print("=" * 80)
    
    # 测试庄家钱包
    dealer_results = await test_dealer_wallets()
    
    # 测试散户钱包
    retail_results = await test_retail_wallets()
    
    # 分析充值来源
    await analyze_transfer_source()
    
    # 汇总
    print("\n" + "=" * 80)
    print(" 测试汇总")
    print("=" * 80)
    
    dealer_count = sum(1 for r in dealer_results if r["status"] == "dealer")
    dealer_total = len(dealer_results)
    retail_count = sum(1 for r in retail_results if r["status"] == "retail")
    retail_total = len(retail_results)
    false_positive = sum(1 for r in retail_results if r["status"] == "false_positive")
    
    print(f"\n庄家钱包检测:")
    print(f"  正确识别: {dealer_count}/{dealer_total}")
    print(f"\n散户钱包检测:")
    print(f"  正确识别: {retail_count}/{retail_total}")
    print(f"  误判为庄家: {false_positive}")


if __name__ == "__main__":
    asyncio.run(main())
