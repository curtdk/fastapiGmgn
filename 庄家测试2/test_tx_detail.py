"""
测试用例：获取交易详情

支持两种模式：
1. 钱包地址模式 - 使用 getTransactionsForAddress
2. 签名模式 - 使用 getTransaction

修复内容：
1. base58 import 移到文件头部
2. 修复单位换算（microlamports -> SOL 除以 10^9）
3. 修复 Priority Fee 计算（使用 Limit 而不是 Consumed）
4. 修复基础费用计算（签名数量 * 5000）
5. 正确解析 CU Limit 和 CU Price
"""
import asyncio
import base58
from datetime import datetime
from typing import Dict, Any, List, Optional
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

# 默认测试钱包地址
DEFAULT_WALLET = "8u43z8r32qzt87F5PntBs2tCFjnWNmNqFPv4z3XZuX"


def extract_mints_from_transaction(tx_raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从交易中提取所有涉及的 mint 和余额变化"""
    meta = tx_raw.get("meta", {})
    pre_tokens = meta.get("preTokenBalances", [])
    post_tokens = meta.get("postTokenBalances", [])
    
    mints_info = {}
    
    for b in post_tokens:
        mint = b.get("mint", "")
        if mint:
            ui_amount = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
            if mint not in mints_info:
                mints_info[mint] = {"pre_amount": 0.0, "post_amount": ui_amount}
            else:
                mints_info[mint]["post_amount"] += ui_amount
    
    for b in pre_tokens:
        mint = b.get("mint", "")
        if mint:
            ui_amount = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
            if mint not in mints_info:
                mints_info[mint] = {"pre_amount": ui_amount, "post_amount": 0.0}
            else:
                mints_info[mint]["pre_amount"] += ui_amount
    
    result = []
    for mint, info in mints_info.items():
        delta = info["post_amount"] - info["pre_amount"]
        result.append({
            "mint": mint,
            "pre_amount": info["pre_amount"],
            "post_amount": info["post_amount"],
            "delta": delta,
            "abs_delta": abs(delta)
        })
    
    result.sort(key=lambda x: x["abs_delta"], reverse=True)
    return result


def extract_compute_unit_info_from_instructions(transaction: Dict[str, Any]) -> Dict[str, int]:
    """从交易指令中解析 ComputeUnitLimit 和 ComputeUnitPrice"""
    result = {"cu_limit": 200000, "cu_price": 0}
    
    try:
        message = transaction.get("message", {})
        instructions = message.get("instructions", [])
        
        for ix in instructions:
            program_id = ix.get("programId", "")
            if program_id == "ComputeBudget111111111111111111111111111111":
                data = ix.get("data", "")
                if data and len(data) > 2:
                    decoded = base58.b58decode(data)
                    if len(decoded) >= 9:
                        instruction_type = decoded[0]
                        value = int.from_bytes(decoded[1:9], 'little')
                        
                        if instruction_type == 2:
                            result["cu_limit"] = value
                        elif instruction_type == 3:
                            result["cu_price"] = value
    except Exception:
        pass
    
    return result


def extract_compute_and_fee_info(meta: Dict[str, Any], transaction: Dict[str, Any]) -> Dict[str, Any]:
    """从交易 meta 中提取计算单元、Priority Fee 等信息"""
    result = {
        "cu_consumed": 0, "cu_limit": 200000, "priority_fee": 0.0,
        "cu_price": 0, "base_fee": 0, "total_fee": 0, "signers_count": 0,
    }
    
    result["cu_consumed"] = meta.get("computeUnitsConsumed", 0)
    result["total_fee"] = meta.get("fee", 0)
    
    cu_info = extract_compute_unit_info_from_instructions(transaction)
    result["cu_limit"] = cu_info["cu_limit"]
    result["cu_price"] = cu_info["cu_price"]
    
    # Priority Fee = cu_limit * cu_price / 1e9
    if result["cu_price"] > 0 and result["cu_limit"] > 0:
        result["priority_fee"] = (result["cu_limit"] * result["cu_price"]) / 1e9
    
    # 计算签名者数量
    message = transaction.get("message", {})
    account_keys = message.get("accountKeys", [])
    signers = 0
    for key in account_keys:
        if isinstance(key, dict) and key.get("signer"):
            signers += 1
        elif not isinstance(key, dict):
            signers += 1
            break
    result["signers_count"] = signers if signers > 0 else 1
    
    # 基础费 = 签名数 * 5000 lamports
    result["base_fee"] = result["signers_count"] * 5000
    
    # 如果 priority_fee 为 0，用总费用减去基础费
    if result["priority_fee"] == 0 and result["total_fee"] > result["base_fee"]:
        result["priority_fee"] = (result["total_fee"] - result["base_fee"]) / 1e9
    
    return result


KNOWN_BOT_PROGRAMS = {
    "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9": "Axiom (高频交易机器人)",
    "LBUZGfxFaB9nmFEHhCmDmcYfquaxK8bC2yk3XtgEbN3q": "Jito Bot",
}

DEX_PROGRAMS = {
    "pump": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"],
    "raydium": ["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"],
}


def extract_dealer_hints(meta: Dict[str, Any], message: Dict[str, Any]) -> Dict[str, Any]:
    """提取庄家判断辅助信息"""
    result = {
        "signers_count": 0, "instructions_count": 0, "account_keys_count": 0,
        "has_writable_signer": False, "program_ids": [], "is_simple_transfer": False,
        "uses_lookup_table": False, "has_known_bot_program": False,
        "known_bot_programs": [], "is_dex_swap": False, "dex_type": "",
        # 新增：innerInstructions 相关
        "inner_instructions": [],  # 包含的所有内置命令列表
        "inner_programs": [],     # 涉及的所有程序 ID
        "total_instruction_count": 0,  # 总命令数（主指令 + 内部指令）
    }
    
    account_keys = message.get("accountKeys", [])
    writable_signers = []
    
    for key in account_keys:
        if isinstance(key, dict):
            key_pubkey = key.get("pubkey", "")
            is_signer = key.get("signer", False)
            is_writable = key.get("writable", False)
            source = key.get("source", "")
            
            if source == "lookupTable":
                result["uses_lookup_table"] = True
            if is_signer:
                result["signers_count"] += 1
                if is_writable:
                    writable_signers.append(key_pubkey)
        else:
            if result["signers_count"] == 0:
                result["signers_count"] = 1
    
    result["has_writable_signer"] = len(writable_signers) > 0
    result["account_keys_count"] = len(account_keys)
    
    instructions = message.get("instructions", [])
    result["instructions_count"] = len(instructions)
    
    # 主指令
    for ix in instructions:
        program_id = ix.get("programId", "")
        if program_id and program_id not in result["program_ids"]:
            result["program_ids"].append(program_id)
    
    # 内部指令 (innerInstructions)
    inner_instr_count = 0
    for ix_group in meta.get("innerInstructions", []):
        for ix in ix_group.get("instructions", []):
            inner_instr_count += 1
            program_id = ix.get("programId", "")
            
            # 记录程序 ID
            if program_id and program_id not in result["inner_programs"]:
                result["inner_programs"].append(program_id)
            
            # 提取指令类型 (parsed type)
            ix_type = ix.get("parsed", {}).get("type", "")
            if ix_type:
                result["inner_instructions"].append(ix_type)
            else:
                # 如果没有 parsed，尝试从 data 解码
                result["inner_instructions"].append(f"raw:{program_id[:10]}...")
            
            if program_id and program_id not in result["program_ids"]:
                result["program_ids"].append(program_id)
    
    result["total_instruction_count"] = result["instructions_count"] + inner_instr_count
    
    for pid in result["program_ids"]:
        for dex_name, dex_pids in DEX_PROGRAMS.items():
            if pid in dex_pids:
                result["is_dex_swap"] = True
                result["dex_type"] = dex_name
                break
    
    for pid in result["program_ids"]:
        if pid in KNOWN_BOT_PROGRAMS:
            result["has_known_bot_program"] = True
            result["known_bot_programs"].append({"program_id": pid, "name": KNOWN_BOT_PROGRAMS[pid]})
    
    system_program = "11111111111111111111111111111111"
    non_system_programs = [p for p in result["program_ids"] if p != system_program]
    result["is_simple_transfer"] = len(non_system_programs) == 0 and result["instructions_count"] == 1
    
    return result


def analyze_dealer_indicators(result: Dict[str, Any], priority_fee: float) -> Dict[str, Any]:
    """分析庄家指标"""
    indicators = []
    score = 0
    
    if result["account_keys_count"] > 20:
        indicators.append(f"高账户数: {result['account_keys_count']}")
        score += 25
    elif result["account_keys_count"] > 15:
        indicators.append(f"中等账户数: {result['account_keys_count']}")
        score += 10
    
    if result["uses_lookup_table"]:
        indicators.append("使用了地址查找表 (ALT)")
        score += 20
    
    if result["has_known_bot_program"]:
        for bp in result["known_bot_programs"]:
            indicators.append(f"检测到机器人程序: {bp['name']}")
            score += 35
    
    if priority_fee > 0.5:
        indicators.append(f"高 Priority Fee: {priority_fee:.6f} SOL")
        score += 20
    elif priority_fee > 0.1:
        indicators.append(f"中等 Priority Fee: {priority_fee:.6f} SOL")
        score += 10
    
    if not result["is_simple_transfer"]:
        indicators.append("复杂交易 (非简单转账)")
        score += 10
    
    if result["is_dex_swap"]:
        indicators.append(f"DEX 交易: {result['dex_type']}")
        score += 5
    
    if score >= 60:
        verdict = "⚠️ 高风险 - 很可能为机器人/量化交易"
    elif score >= 30:
        verdict = "⚡ 中等风险 - 有机器人特征"
    elif score >= 15:
        verdict = "🤔 低风险 - 部分特征需关注"
    else:
        verdict = "✅ 低风险 - 普通用户交易"
    
    return {"score": min(score, 100), "indicators": indicators, "verdict": verdict}


# ==================== API 函数 ====================

async def get_transaction_by_sig(sig: str) -> Optional[Dict[str, Any]]:
    """使用 getTransaction API 获取单个交易"""
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
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            print(f"[错误] API 返回错误: {data['error']}")
            return None
        
        return data.get("result", None)


async def get_transactions_for_address(address: str, limit: int = 10) -> List[Dict[str, Any]]:
    """使用 getTransactionsForAddress API 获取地址的交易列表"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            address,
            {
                "transactionDetails": "full",
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "sortOrder": "desc",
                "limit": limit
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            print(f"[错误] API 返回错误: {data['error']}")
            return []
        
        return data.get("result", {}).get("data", [])


def parse_and_display_transaction(tx_entry: Dict[str, Any], is_raw: bool = False) -> bool:
    """解析并显示交易信息，返回是否成功"""
    
    # 从交易数据中提取信息
    if is_raw:
        # getTransaction 返回的数据格式
        tx_raw = tx_entry
        signatures = tx_raw.get("transaction", {}).get("signatures", [])
        sig = signatures[0] if signatures else ""
        slot = tx_raw.get("slot", 0)
        block_time = tx_raw.get("blockTime")
        transaction = tx_raw.get("transaction", {})
        meta = tx_raw.get("meta", {})
    else:
        # getTransactionsForAddress 返回的数据格式
        transaction = tx_entry.get("transaction", {})
        signatures = transaction.get("signatures", [])
        sig = signatures[0] if signatures else "N/A"
        slot = tx_entry.get("slot", 0)
        block_time = tx_entry.get("blockTime")
        meta = tx_entry.get("meta", {})
        tx_raw = transaction
    
    if not sig:
        print("   ⚠️ 无法获取签名")
        return False
    
    message = transaction.get("message", {})
    account_keys = message.get("accountKeys", [])
    
    # 获取签名者
    signer = ""
    signer_idx = 0
    for idx, key in enumerate(account_keys):
        if isinstance(key, dict) and key.get("signer"):
            signer = key.get("pubkey", "")
            signer_idx = idx
            break
        elif not isinstance(key, dict):
            signer = key
            signer_idx = idx
            break
    
    if not signer:
        print("   ⚠️ 无法确定签名者")
        return False
    
    # SOL 变化
    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])
    fee_lamports = meta.get("fee", 0)
    sol_spent = 0.0
    if len(pre_balances) > signer_idx and len(post_balances) > signer_idx:
        sol_spent = (pre_balances[signer_idx] - post_balances[signer_idx] - fee_lamports) / 1e9
    
    # Token 变化 - 检查 signer 的 token 余额变化
    pre_amt = 0.0
    post_amt = 0.0
    
    # 找到 signer 的 pre 和 post token 余额
    for b in meta.get("preTokenBalances", []):
        if b.get("owner") == signer:
            pre_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
            break
    
    for b in meta.get("postTokenBalances", []):
        if b.get("owner") == signer:
            post_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
            break
    
    # 计算 delta（post - pre）
    delta = post_amt - pre_amt
    amount = abs(delta)
    
    # 获取涉及的主要 mint（用于显示）
    mints_info = extract_mints_from_transaction({"meta": meta})
    selected_mint = mints_info[0]["mint"] if mints_info else ""
    
    # 判断交易类型
    # - token 增加（delta > 0）且 SOL 减少 → BUY（花钱买币）
    # - token 减少（delta < 0）且 SOL 增加 → SELL（卖币得钱）
    if delta > 0 and sol_spent > 0:
        tx_type = "BUY"
    elif delta < 0 or (delta == 0 and sol_spent < 0):
        tx_type = "SELL"
    else:
        tx_type = "TRANSFER"
    
    # DEX 检测
    dex = ""
    for ix_group in meta.get("innerInstructions", []):
        for ix in ix_group.get("instructions", []):
            pid = ix.get("programId", "")
            if "6EF8rrecth" in pid:
                dex = "pump.fun"
                break
            elif "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in pid:
                dex = "raydium"
                break
    
    # 计算 CU 和 Fee
    compute_info = extract_compute_and_fee_info(meta, tx_raw)
    dealer_hints = extract_dealer_hints(meta, message)
    dealer_analysis = analyze_dealer_indicators(dealer_hints, compute_info["priority_fee"])
    
    # 时间格式化
    if block_time:
        block_time = datetime.utcfromtimestamp(block_time)
    
    # 总费用 = 基础费 + Priority Fee
    total_fee_sol = (compute_info["total_fee"] + compute_info["priority_fee"] * 1e9) / 1e9 if compute_info["priority_fee"] == 0 else (compute_info["total_fee"]) / 1e9
    
    # 打印结果
    print(f"   签名:     {sig[:40]}...")
    print(f"   Slot:     {slot:,}")
    print(f"   时间:     {block_time}")
    
    tx_icon = {"BUY": "🟢", "SELL": "🔴", "TRANSFER": "🟡"}.get(tx_type, "⚪")
    print(f"   类型:     {tx_icon} {tx_type}")
    
    if selected_mint:
        print(f"   数量:     {amount:.2f}")
    
    print(f"   SOL 花费: {sol_spent:.6f} SOL")
    print(f"   Fee (Gas): {compute_info['total_fee'] / 1e9:.9f} SOL")
    print(f"   Priority:  {compute_info['priority_fee']:.9f} SOL")
    print(f"   CU:        {compute_info['cu_consumed']:,} / {compute_info['cu_limit']:,}")
    print(f"   DEX:       {dex or 'N/A'}")
    
    # 指令统计
    print(f"\n   📋 指令统计:")
    print(f"     主指令数:    {dealer_hints['instructions_count']}")
    print(f"     内部指令数:   {len(dealer_hints['inner_instructions'])}")
    print(f"     总指令数:     {dealer_hints['total_instruction_count']}")
    
    if dealer_hints['inner_instructions']:
        print(f"\n     🔧 内部命令列表:")
        # 去重并计数
        from collections import Counter
        ix_counts = Counter(dealer_hints['inner_instructions'])
        for ix_type, count in ix_counts.most_common(5):
            print(f"       - {ix_type}: {count}次")
    
    print(f"\n   风险评估:  {dealer_analysis['verdict']} ({dealer_analysis['score']}/100)")
    
    if dealer_analysis['indicators']:
        print(f"\n   🚨 触发指标:")
        for j, indicator in enumerate(dealer_analysis['indicators'][:3]):
            print(f"     [{j+1}] {indicator}")
    
    # 庄家检测
    dealer_check = check_dealer_c001(tx_entry, signer)
    if dealer_check:
        print(f"\n   ⚠️ 庄家检测: C001 触发!")
        print(f"     - 首笔交易包含 closeAccount")
        print(f"     - 交易后 SOL 余额为 0")
        print(f"     - 判定为: 庄家地址")
    else:
        print(f"\n   ✅ 庄家检测: 未触发 C001")
    
    return True


def check_dealer_c001(tx_data: dict, address: str) -> bool:
    """条件 C001：首笔交易包含 closeAccount 指令 且 交易后该地址 SOL 余额为 0
    
    注意：此函数用于检测地址是否可能是庄家地址
    """
    try:
        meta = tx_data.get("meta", {})
        if meta.get("err"):
            return False

        # 检测 closeAccount 指令
        has_close = False
        for ix_group in meta.get("innerInstructions", []):
            for ix in ix_group.get("instructions", []):
                ix_type = ix.get("parsed", {}).get("type", "")
                if ix_type in ("closeAccount", "CloseAccount"):
                    has_close = True
                    break
            if has_close:
                break

        if not has_close:
            return False

        # 检测交易后该地址 SOL 余额是否为 0
        transaction = tx_data.get("transaction", {})
        message = transaction.get("message", {})
        account_keys = message.get("accountKeys", [])

        # 找 address 在 accountKeys 中的 index
        addr_index = None
        for i, key in enumerate(account_keys):
            pubkey = key.get("pubkey", "") if isinstance(key, dict) else key
            if pubkey == address:
                addr_index = i
                break

        if addr_index is None:
            return False

        post_balances = meta.get("postBalances", [])
        if addr_index < len(post_balances):
            return post_balances[addr_index] == 0

    except Exception:
        pass

    return False


async def main():
    """主函数"""
    import sys
    arg = '44HpE6Y21DstMSvuz8DLQaJNwUATa7anifWrBnKYVTWqJ37soLr98Z6udSkLoSkakv8DNL11PGge9E4wCtS7t9au'
    if True:
        # arg = sys.argv[1]
        
        # 判断是签名还是钱包地址 (签名通常 > 50 字符)
        if len(arg) > 50:
            # 签名模式
            sig = arg
            print(f"\n📌 使用签名模式: {sig[:30]}...")
            print("\n" + "=" * 60)
            print(" 🔍 通过 getTransaction 获取交易详情")
            print("=" * 60)
            
            tx = await get_transaction_by_sig(sig)
            if tx:
                print()
                parse_and_display_transaction(tx, is_raw=True)
            else:
                print("❌ 获取交易失败")
        else:
            # 钱包地址模式
            wallet = arg
            print(f"\n📌 使用钱包地址: {wallet}")
            print("\n" + "=" * 60)
            print(" 📋 通过 getTransactionsForAddress 获取交易列表")
            print("=" * 60)
            
            txs = await get_transactions_for_address(wallet, limit=5)
            if txs:
                print(f"\n✅ 成功获取 {len(txs)} 条交易\n")
                for i, tx_entry in enumerate(txs):
                    print(f"{'='*60}")
                    print(f" 📝 交易 #{i+1}")
                    print(f"{'='*60}")
                    parse_and_display_transaction(tx_entry, is_raw=False)
                    print()
            else:
                print("❌ 获取交易失败或无交易")
    else:
        # 默认使用钱包地址模式
        wallet = DEFAULT_WALLET
        print(f"\n📌 使用默认钱包地址: {wallet}")
        print("\n" + "=" * 60)
        print(" 📋 通过 getTransactionsForAddress 获取交易列表")
        print("=" * 60)
        
        txs = await get_transactions_for_address(wallet, limit=5)
        if txs:
            print(f"\n✅ 成功获取 {len(txs)} 条交易\n")
            for i, tx_entry in enumerate(txs):
                print(f"{'='*60}")
                print(f" 📝 交易 #{i+1}")
                print(f"{'='*60}")
                parse_and_display_transaction(tx_entry, is_raw=False)
                print()
        else:
            print("❌ 获取交易失败或无交易")
    
    print(f"\n💡 使用说明:")
    print(f"   # 签名模式 (长字符串):")
    print(f"   python test_tx_detail.py <交易签名>")
    print(f"\n   # 钱包地址模式 (短字符串):")
    print(f"   python test_tx_detail.py <钱包地址>")
    print(f"   python test_tx_detail.py  # 使用默认钱包")


if __name__ == "__main__":
    asyncio.run(main())
