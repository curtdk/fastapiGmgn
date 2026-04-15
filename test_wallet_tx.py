"""测试 Helius getTransactionsForAddress - 获取钱包地址的交易详情"""
import asyncio
import json
from datetime import datetime
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

# 测试钱包集合
WALLET_ADDRESSES = [
    "42BdU4C3FMymU1GN6fSDyho6EpWNyQGF2M78KNfYcxR8",
    "1gC57FkN3eGSrorGLe7PKf9EnvbLeRAcT4duJEfsFyc",
    "EXeScff9vUbmzquVd84FKTxgis2uNZFs9sC6wJSXjqFR",
]


def extract_trade_info(tx_data: dict, wallet: str) -> dict:
    """从交易数据中提取关键信息"""
    try:
        # 获取签名
        tx = tx_data.get("transaction", {})
        sigs = tx.get("signatures", [])
        sig = sigs[0] if sigs else ""
        
        meta = tx_data.get("meta", {})
        message = tx.get("message", {})
        account_keys = message.get("accountKeys", [])
        
        slot = tx_data.get("slot", 0)
        block_time = tx_data.get("blockTime")
        if block_time:
            block_time = datetime.utcfromtimestamp(block_time)
        
        # 获取涉及的代币（找该钱包的）
        token_changes = []
        pre_balances = {b.get("mint"): b for b in meta.get("preTokenBalances", [])}
        post_balances = {b.get("mint"): b for b in meta.get("postTokenBalances", [])}
        
        all_mints = set(pre_balances.keys()) | set(post_balances.keys())
        for mint in all_mints:
            pre = pre_balances.get(mint, {}).get("uiTokenAmount", {}).get("uiAmount", 0) or 0
            post = post_balances.get(mint, {}).get("uiTokenAmount", {}).get("uiAmount", 0) or 0
            delta = post - pre
            if delta != 0:
                token_changes.append({
                    "mint": mint,
                    "delta": delta
                })
        
        # SOL 变化
        sol_pre = meta.get("preBalances", [0])[0] / 1e9
        sol_post = meta.get("postBalances", [0])[0] / 1e9
        fee = meta.get("fee", 0) / 1e9
        net_sol = sol_post - sol_pre + fee
        
        # 从 innerInstructions 提取程序调用
        programs = []
        inner_ixs = meta.get("innerInstructions", [])
        for ix_group in inner_ixs:
            for ix in ix_group.get("instructions", []):
                program_id = ix.get("programId", "")
                if program_id and program_id not in programs:
                    programs.append(program_id)
        
        # 判断 DEX
        dex = None
        for p in programs:
            if "pump" in p.lower() or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" == p:
                dex = "pump.fun"
                break
            elif "raydium" in p.lower() or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" == p:
                dex = "Raydium"
                break
            elif "orca" in p.lower():
                dex = "Orca"
                break
        
        # 检测 CloseAccount 指令
        has_close_account = False
        close_account_details = []
        for ix_group in inner_ixs:
            for ix in ix_group.get("instructions", []):
                ix_type = ix.get("parsed", {}).get("type", "")
                if ix_type == "closeAccount" or ix_type == "CloseAccount":
                    has_close_account = True
                    # 提取详细信息
                    parsed = ix.get("parsed", {}).get("info", {})
                    close_account_details.append({
                        "account": parsed.get("account", "N/A"),
                        "destination": parsed.get("destination", "N/A"),
                    })
            if has_close_account:
                break
        
        return {
            "signature": sig,
            "slot": slot,
            "block_time": block_time,
            "fee": fee,
            "net_sol": net_sol,
            "token_changes": token_changes,
            "dex": dex,
            "success": not meta.get("err"),
            "programs": programs,
            "transaction_index": tx_data.get("transactionIndex"),
            "account_keys": account_keys,
            "pre_balances": meta.get("preBalances", []),
            "post_balances": meta.get("postBalances", []),
            "pre_token_balances": meta.get("preTokenBalances", []),
            "post_token_balances": meta.get("postTokenBalances", []),
            "inner_instructions": inner_ixs,
            "has_close_account": has_close_account,
            "close_account_details": close_account_details,
        }
    except Exception as e:
        return {"error": str(e)}


async def get_first_transaction(wallet: str) -> dict:
    """获取钱包的第一笔交易"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            wallet,
            {
                "transactionDetails": "full",
                "sortOrder": "asc",  # 从旧到新
                "limit": 1,  # 只获取第一条
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "filters": {
                    "status": "succeeded"  # 只获取成功的交易
                }
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{HELIUS_RPC_URL}/?api-key={API_KEY}",
            json=body
        )
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            return {"error": data["error"], "wallet": wallet}
        
        result = data.get("result", {})
        transactions = result.get("data", [])
        
        if not transactions:
            return {"error": "无交易记录", "wallet": wallet}
        
        return {"wallet": wallet, "transaction": transactions[0]}


async def check_wallets():
    """检查所有钱包的第一笔交易"""
    print("=" * 80)
    print(" 检查钱包集合 - 第一笔交易分析")
    print("=" * 80)
    
    results = []
    
    for wallet in WALLET_ADDRESSES:
        print(f"\n{'─' * 80}")
        print(f"🔍 正在查询钱包: {wallet[:20]}...")
        
        result = await get_first_transaction(wallet)
        
        if "error" in result:
            print(f"   ❌ 错误: {result['error']}")
            results.append({
                "wallet": wallet,
                "status": "error",
                "error": result["error"]
            })
            continue
        
        tx_data = result["transaction"]
        info = extract_trade_info(tx_data, wallet)
        
        if "error" in info:
            print(f"   ❌ 解析错误: {info['error']}")
            results.append({
                "wallet": wallet,
                "status": "error",
                "error": info["error"]
            })
            continue
        
        # 输出第一笔交易的基本信息
        print(f"   📝 第一笔交易:")
        print(f"      签名:     {info['signature'][:40]}...")
        print(f"      Slot:     {info['slot']:,}")
        print(f"      时间:     {info['block_time']}")
        print(f"      手续费:   {info['fee']:.9f} SOL")
        print(f"      SOL变化:  {info['net_sol']:+.9f} SOL {'→ 收入' if info['net_sol'] > 0 else '← 支出' if info['net_sol'] < 0 else '= 不变'}")
        
        # 检查是否有代币变化
        if info['token_changes']:
            print(f"      代币变化:")
            for tc in info['token_changes']:
                mint_short = tc['mint'][:20] + "..."
                print(f"        {mint_short}...: {tc['delta']:+.6f}")
        
        # 检查 CloseAccount
        if info.get('has_close_account'):
            print(f"   ⚠️  ⚠️  ⚠️  包含 CloseAccount 指令! ⚠️  ⚠️  ⚠️")
            for detail in info.get('close_account_details', []):
                print(f"      - 关闭账户: {detail.get('account', 'N/A')[:20]}...")
                print(f"        目标账户: {detail.get('destination', 'N/A')[:20]}...")
            results.append({
                "wallet": wallet,
                "status": "has_close_account",
                "info": info
            })
        else:
            print(f"   ✅ 无 CloseAccount 指令")
            results.append({
                "wallet": wallet,
                "status": "ok",
                "info": info
            })
    
    # 汇总
    print(f"\n{'=' * 80}")
    print(" 汇总结果")
    print("=" * 80)
    
    has_close_count = sum(1 for r in results if r["status"] == "has_close_account")
    ok_count = sum(1 for r in results if r["status"] == "ok")
    error_count = sum(1 for r in results if r["status"] == "error")
    
    print(f"\n总钱包数: {len(WALLET_ADDRESSES)}")
    print(f"✅ 正常 (无 CloseAccount): {ok_count}")
    print(f"⚠️  包含 CloseAccount: {has_close_count}")
    print(f"❌ 错误: {error_count}")
    
    if has_close_count > 0:
        print(f"\n⚠️  以下钱包的第一笔交易包含 CloseAccount:")
        for r in results:
            if r["status"] == "has_close_account":
                print(f"   - {r['wallet'][:20]}...")
    
    return results


async def main():
    """主函数"""
    print("\n" + "=" * 80)
    print(" Helius getTransactionsForAddress - 钱包集合分析")
    print("=" * 80)
    
    await check_wallets()


if __name__ == "__main__":
    asyncio.run(main())
