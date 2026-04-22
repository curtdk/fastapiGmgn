"""庄家检测测试 v2 - 基于 CU 值和 Token Balance 模式
根据调试结果，Helius API 不返回完整 logs，所以改用其他特征：
1. C003: Compute Units 在 105,000-115,000 范围
2. C005: Token Balance 大幅减少（卖出代币）
3. C006: 时间戳分析（同一天内多笔交易）
"""
import asyncio
import json
import httpx
from typing import List, Dict, Optional, Tuple

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

# 测试钱包
DEALER_WALLETS = [
    "F7Lyr3ho7BgVeDhsATn161rx2GbRhBh3V5a1s7TFZ8fG",  # 庄家1 - 充值
    "6EssiA1oHwrhtFAGobtSyWrh9foJVfmaXDbioihAdmh3",  # 庄家2 - Sell
    "91zFBYzxyBFbGNw4YRhn3U7WJkm7hPTwYgaYrmHRxCtR",  # 庄家3
    "6zV9MNsXE2EJxFNvSTVknFNQi4MCN4oaCiRtG14TsZ2n",  # 庄家
    "3fUY8hMDaFDnWpdFZCWT9XcEZ7ZPheh9o2L7sv2XNvbh",  # 庄家
    "4J2VVcXrFQi2y8woNA2SXi4a3NHZvX3irNs5H7LJUqwF",  # 庄家4 - Sell
]

RETAIL_WALLETS = [
    "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS",
    "9DXAFzcppSQYewAh4TQMTSpd2nvBdCXykvmRUzUmPPn9",
    "3d9yoXk2DNoajkp2WB8tYy7CXpTpJB2TY8kjcpRobDJu",
]


def analyze_transaction(tx_data: dict) -> dict:
    """分析交易特征"""
    try:
        tx = tx_data.get("transaction", {})
        meta = tx_data.get("meta", {})
        
        # 1. Compute Units
        cu_consumed = meta.get("computeUnitsConsumed", 0)
        
        # 2. Token Balance 变化
        pre_token = meta.get("preTokenBalances", [])
        post_token = meta.get("postTokenBalances", [])
        
        # 找出该钱包的 token 变化
        account_keys = tx.get("message", {}).get("accountKeys", [])
        signer_pubkey = account_keys[0] if account_keys else None
        if isinstance(signer_pubkey, dict):
            signer_pubkey = signer_pubkey.get("pubkey", "")
        
        token_balance_changes = []
        for pre, post in zip(pre_token, post_token):
            if pre.get("owner") == signer_pubkey:
                pre_amt = int(pre.get("uiTokenAmount", {}).get("amount", 0))
                post_amt = int(post.get("uiTokenAmount", {}).get("amount", 0))
                if pre_amt != post_amt:
                    token_balance_changes.append({
                        "mint": pre.get("mint", ""),
                        "pre": pre_amt,
                        "post": post_amt,
                        "change": post_amt - pre_amt
                    })
        
        # 3. SOL 变化
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        sol_change = 0
        if pre_balances and post_balances and len(pre_balances) > 0:
            sol_change = (post_balances[0] - pre_balances[0]) / 1e9
        
        # 4. 解析交易类型
        # 检查是否是 Pump.fun 交易（通过 innerInstructions）
        inner_ixs = meta.get("innerInstructions", [])
        instructions = []
        for ix_group in inner_ixs:
            for ix in ix_group.get("instructions", []):
                parsed = ix.get("parsed", {})
                if parsed:
                    instructions.append(parsed.get("type", ""))
        
        # 检查是否有 Pump.fun 相关的程序
        has_pump = False
        for ix in instructions:
            if "pump" in ix.lower() or "Pump" in ix:
                has_pump = True
                break
        
        return {
            "cu_consumed": cu_consumed,
            "sol_change": sol_change,
            "token_balance_changes": token_balance_changes,
            "instructions": instructions,
            "has_pump": has_pump,
            "success": not meta.get("err")
        }
    except Exception as e:
        return {"error": str(e)}


def check_c003_cu_range(info: dict, min_cu: int = 105000, max_cu: int = 115000) -> bool:
    """C003: Compute Units 在指定范围内"""
    cu = info.get("cu_consumed", 0)
    return min_cu <= cu <= max_cu


def check_c005_token_dump(info: dict) -> bool:
    """C005: Token Balance 大幅减少（卖出代币）
    
    庄家特征：卖出大量代币，token 余额大幅减少
    """
    changes = info.get("token_balance_changes", [])
    for change in changes:
        # 检查是否有大幅减少（负值变化）
        if change["change"] < -1000000:  # 超过 1,000,000 单位（假设 6 位小数）
            return True
    return False


def check_c006_multiple_txs_same_day(wallet: str, txs: List[dict]) -> bool:
    """C006: 同一天内有多笔交易
    
    庄家特征：同一 token 的子钱包通常在短时间内有多笔交易
    """
    # 这里简化为检查交易数量
    return len(txs) >= 3


def is_dealer_by_conditions(info: dict, txs: List[dict]) -> Tuple[bool, List[str]]:
    """综合判断是否为庄家"""
    conditions = []
    
    # C003: CU 范围
    if check_c003_cu_range(info):
        conditions.append("C003")
    
    # C005: Token 大量减少
    if check_c005_token_dump(info):
        conditions.append("C005")
    
    # C006: 多笔交易
    if check_c006_multiple_txs_same_day("", txs):
        conditions.append("C006")
    
    # 庄家判定：C003 是核心条件
    is_dealer = check_c003_cu_range(info)
    
    return is_dealer, conditions


async def get_transactions(address: str, limit: int = 10) -> List[dict]:
    """获取钱包交易"""
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
        data = resp.json()
        
        if "error" in data:
            return []
        
        return data.get("result", {}).get("data", [])


async def test_dealer_wallets():
    """测试庄家钱包"""
    print("\n" + "=" * 80)
    print(" 测试庄家钱包")
    print("=" * 80)
    
    results = []
    
    for wallet in DEALER_WALLETS:
        print(f"\n🔍 测试钱包: {wallet[:20]}...")
        
        txs = await get_transactions(wallet, 10)
        
        if not txs:
            print(f"   ❌ 无交易记录")
            results.append({"wallet": wallet, "status": "no_txs"})
            continue
        
        # 分析每笔交易
        dealer_count = 0
        details = []
        
        for tx in txs[:5]:  # 检查前5笔
            info = analyze_transaction(tx)
            if "error" in info:
                continue
            
            is_dealer, conditions = is_dealer_by_conditions(info, txs)
            if is_dealer:
                dealer_count += 1
                details.append({
                    "cu": info.get("cu_consumed", 0),
                    "conditions": conditions,
                    "token_changes": info.get("token_balance_changes", [])
                })
        
        if dealer_count > 0:
            print(f"   ✅ 命中庄家特征! {dealer_count} 笔交易")
            for d in details[:3]:
                print(f"      CU: {d['cu']:,}, 条件: {d['conditions']}")
            results.append({
                "wallet": wallet,
                "status": "dealer",
                "count": dealer_count
            })
        else:
            print(f"   ⚠️  未命中庄家特征")
            # 打印交易特征用于调试
            for tx in txs[:3]:
                info = analyze_transaction(tx)
                print(f"      交易 CU: {info.get('cu_consumed', 0):,}, SOL变化: {info.get('sol_change', 0):+.4f}")
            results.append({"wallet": wallet, "status": "not_dealer"})
        
        # 避免请求过快
        await asyncio.sleep(0.5)
    
    return results


async def test_retail_wallets():
    """测试散户钱包"""
    print("\n" + "=" * 80)
    print(" 测试散户钱包")
    print("=" * 80)
    
    results = []
    
    for wallet in RETAIL_WALLETS:
        print(f"\n🔍 测试钱包: {wallet[:20]}...")
        
        txs = await get_transactions(wallet, 10)
        
        if not txs:
            print(f"   ❌ 无交易记录")
            results.append({"wallet": wallet, "status": "no_txs"})
            continue
        
        # 检查是否有庄家特征
        dealer_count = 0
        for tx in txs[:5]:
            info = analyze_transaction(tx)
            if "error" in info:
                continue
            
            is_dealer, _ = is_dealer_by_conditions(info, txs)
            if is_dealer:
                dealer_count += 1
        
        if dealer_count == 0:
            print(f"   ✅ 正确识别为散户")
            results.append({"wallet": wallet, "status": "retail"})
        else:
            print(f"   ⚠️  误判为庄家! {dealer_count} 笔交易命中")
            results.append({"wallet": wallet, "status": "false_positive"})
        
        await asyncio.sleep(0.5)
    
    return results


async def main():
    print("=" * 80)
    print(" 庄家检测测试 v2 - C003/C005/C006 条件")
    print("=" * 80)
    
    # 测试庄家钱包
    dealer_results = await test_dealer_wallets()
    
    # 测试散户钱包
    retail_results = await test_retail_wallets()
    
    # 汇总
    print("\n" + "=" * 80)
    print(" 测试汇总")
    print("=" * 80)
    
    dealer_count = sum(1 for r in dealer_results if r["status"] == "dealer")
    dealer_total = len([r for r in dealer_results if r["status"] != "no_txs"])
    retail_count = sum(1 for r in retail_results if r["status"] == "retail")
    retail_total = len([r for r in retail_results if r["status"] != "no_txs"])
    
    print(f"\n庄家钱包检测:")
    print(f"  正确识别: {dealer_count}/{dealer_total}")
    print(f"\n散户钱包检测:")
    print(f"  正确识别: {retail_count}/{retail_total}")


if __name__ == "__main__":
    asyncio.run(main())
