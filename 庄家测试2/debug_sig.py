"""根据交易签名(Sig)解析交易详情并打印 Gas 费"""
import asyncio
import json
import sys
from datetime import datetime
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"


async def get_tx_by_signature(sig: str) -> dict:
    """根据签名获取交易详情"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            sig,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            return {"error": data["error"]}
        
        return data.get("result", None)


def parse_tx_info(tx_data: dict) -> dict:
    """解析交易数据，提取关键信息"""
    try:
        tx = tx_data.get("transaction", {})
        meta = tx_data.get("meta", {})
        message = tx.get("message", {})
        
        # 签名
        sigs = tx.get("signatures", [])
        sig = sigs[0] if sigs else ""
        
        # 基础信息
        slot = tx_data.get("slot", 0)
        block_time = tx_data.get("blockTime")
        if block_time:
            block_time = datetime.utcfromtimestamp(block_time)
        
        # ===== Gas 费信息 =====
        fee_lamports = meta.get("fee", 0)
        fee_sol = fee_lamports / 1e9  # 转换为 SOL
        
        # Compute Units
        cu_consumed = meta.get("computeUnitsConsumed", 0)
        
        # SOL 余额变化
        pre_balances = meta.get("preBalances", [])
        post_balances = meta.get("postBalances", [])
        
        sol_pre = pre_balances[0] / 1e9 if pre_balances else 0
        sol_post = post_balances[0] / 1e9 if post_balances else 0
        net_sol = sol_post - sol_pre + fee_sol
        
        # 代币变化
        token_changes = []
        pre_tokens = {b.get("mint"): b for b in meta.get("preTokenBalances", [])}
        post_tokens = {b.get("mint"): b for b in meta.get("postTokenBalances", [])}
        
        all_mints = set(pre_tokens.keys()) | set(post_tokens.keys())
        for mint in all_mints:
            pre_ui = pre_tokens.get(mint, {}).get("uiTokenAmount", {}).get("uiAmount", 0) or 0
            post_ui = post_tokens.get(mint, {}).get("uiTokenAmount", {}).get("uiAmount", 0) or 0
            delta = post_ui - pre_ui
            if delta != 0:
                token_changes.append({
                    "mint": mint,
                    "delta": delta
                })
        
        # 检测 DEX
        dex = None
        inner_ixs = meta.get("innerInstructions", [])
        for ix_group in inner_ixs:
            for ix in ix_group.get("instructions", []):
                program_id = ix.get("programId", "")
                if "pump" in program_id.lower() or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" == program_id:
                    dex = "pump.fun"
                    break
                elif "raydium" in program_id.lower() or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" == program_id:
                    dex = "Raydium"
                    break
        
        return {
            "signature": sig,
            "slot": slot,
            "block_time": block_time,
            "fee_lamports": fee_lamports,
            "fee_sol": fee_sol,
            "cu_consumed": cu_consumed,
            "sol_pre": sol_pre,
            "sol_post": sol_post,
            "net_sol": net_sol,
            "token_changes": token_changes,
            "dex": dex,
            "success": not meta.get("err")
        }
    except Exception as e:
        return {"error": str(e)}


async def main():
    """主函数"""
    # 检查是否有传入签名参数
    if len(sys.argv) < 2:
        print("用法: python debug_sig.py <交易签名>")
        print("示例: python debug_sig.py 3aTX5VRKqnviFe8quEmj1LYpxdynwX7ZLfMVTvRDwM6u")
        return
    
    sig = sys.argv[1]
    
    print("=" * 80)
    print(f"解析交易签名: {sig}")
    print("=" * 80)
    
    # 获取交易
    tx_data = await get_tx_by_signature(sig)
    
    if not tx_data:
        print("❌ 未找到交易或获取失败")
        return
    
    # 解析交易
    info = parse_tx_info(tx_data)
    
    if "error" in info:
        print(f"❌ 解析错误: {info['error']}")
        return
    
    # ===== 打印 Gas 费信息 =====
    print("\n" + "=" * 80)
    print(" 💰 Gas 费信息")
    print("=" * 80)
    print(f"   手续费 (Lamports): {info['fee_lamports']:,}")
    print(f"   手续费 (SOL):      {info['fee_sol']:.9f} SOL")
    print(f"   Compute Units:     {info['cu_consumed']:,}")
    
    # 判断是否庄家交易特征
    if 105000 <= info['cu_consumed'] <= 115000:
        print(f"   ⚠️  庄家交易特征: CU 在 105k-115k 范围内")
    
    # 交易状态
    print(f"\n   交易状态: {'✅ 成功' if info['success'] else '❌ 失败'}")
    print(f"   Slot:     {info['slot']:,}")
    print(f"   时间:     {info['block_time']}")
    
    # SOL 变化
    print("\n" + "=" * 80)
    print(" 💵 SOL 变化")
    print("=" * 80)
    print(f"   交易前余额: {info['sol_pre']:.9f} SOL")
    print(f"   交易后余额: {info['sol_post']:.9f} SOL")
    print(f"   净变化:     {info['net_sol']:+.9f} SOL {'→ 收入' if info['net_sol'] > 0 else '← 支出' if info['net_sol'] < 0 else '= 不变'}")
    
    # 代币变化
    if info['token_changes']:
        print("\n" + "=" * 80)
        print(" 🪙 代币变化")
        print("=" * 80)
        for tc in info['token_changes']:
            mint_short = tc['mint'][:20] + "..." if len(tc['mint']) > 20 else tc['mint']
            print(f"   {mint_short}: {tc['delta']:+.6f}")
    
    # DEX 信息
    if info['dex']:
        print(f"\n   DEX: {info['dex']}")


if __name__ == "__main__":
    asyncio.run(main())
