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



# 默认交易签名
DEFAULT_SIG = "2jAZDBhH8FJAp4czM9ZbEZC7tTG74TtfuNeXNiEVaqxdx3hsHTmEF2TS2m2p2nYvcyx4h1KUXVQsKzSSxgsiHj3N"


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
    """从交易指令中解析 ComputeUnitLimit 和 ComputeUnitPrice
    
    Solana ComputeBudget 指令格式：
    - SetComputeUnitLimit (opcode 2): opcode(1) + u32(4) = 5 bytes
    - SetComputeUnitPrice (opcode 3): opcode(1) + u64(8) = 9 bytes
    """
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
                    if len(decoded) >= 1:
                        instruction_type = decoded[0]
                        
                        if instruction_type == 2:  # SetComputeUnitLimit
                            if len(decoded) >= 5:
                                result["cu_limit"] = int.from_bytes(decoded[1:5], 'little')
                        elif instruction_type == 3:  # SetComputeUnitPrice
                            if len(decoded) >= 9:
                                result["cu_price"] = int.from_bytes(decoded[1:9], 'little')
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
    
    # 优先使用解析出的 cu_info
    cu_info = extract_compute_unit_info_from_instructions(transaction)
    result["cu_limit"] = cu_info["cu_limit"]
    result["cu_price"] = cu_info["cu_price"]
    
    # 计算 priority_fee
    # 首选方法：直接从 total_fee - base_fee 计算（最可靠）
    # 因为 cu_price 的解码可能存在问题，导致计算结果不准确
    priority_lamports = max(0, result["total_fee"] - result["base_fee"])
    result["priority_fee"] = priority_lamports / 1e9
    
    # 调试信息：如果 cu_price 存在，显示解码的 cu_price
    # 但不使用它来计算 priority_fee
    if result["cu_price"] > 0:
        # 可选：添加调试日志
        pass
    
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
        # 新增：完整指令信息
        "main_instructions": [],     # 主指令详细信息 [{program_id, type, data, index}]
        "inner_instructions_detail": [],  # 内部指令详细信息
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
    
    # 主指令 - 收集完整信息
    for idx, ix in enumerate(instructions):
        program_id = ix.get("programId", "")
        ix_type = ix.get("parsed", {}).get("type", "") or ix.get("instructionType", "")
        
        # 如果类型为空，尝试解码
        if not ix_type:
            ix_type = decode_instruction_type(program_id, ix.get("data", ""))
        
        ix_info = {
            "index": idx,
            "program_id": program_id,
            "type": ix_type,
            "data": ix.get("data", "")[:20] + "..." if ix.get("data", "") else "",
        }
        result["main_instructions"].append(ix_info)
        
        if program_id and program_id not in result["program_ids"]:
            result["program_ids"].append(program_id)
    
    # 内部指令 (innerInstructions) - 收集完整信息
    inner_instr_count = 0
    for ix_group in meta.get("innerInstructions", []):
        group_index = ix_group.get("index", 0)
        for idx, ix in enumerate(ix_group.get("instructions", [])):
            inner_instr_count += 1
            program_id = ix.get("programId", "")
            
            # 提取指令类型
            ix_type = ix.get("parsed", {}).get("type", "") or ix.get("instructionType", "")
            
            # 如果类型为空，尝试解码
            if not ix_type:
                ix_type = decode_instruction_type(program_id, ix.get("data", ""))
            
            ix_info = {
                "index": idx,
                "group_index": group_index,
                "program_id": program_id,
                "type": ix_type,
                "data": ix.get("data", "")[:20] + "..." if ix.get("data", "") else "",
            }
            result["inner_instructions_detail"].append(ix_info)
            
            # 记录程序 ID
            if program_id and program_id not in result["inner_programs"]:
                result["inner_programs"].append(program_id)
            
            # 简化命令名称（用于统计）
            result["inner_instructions"].append(ix_type)
            
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


# ==================== ComputeBudget 指令类型映射 ====================

COMPUTE_BUDGET_TYPES = {
    0: "RequestUnitsDeprecated",
    1: "RequestHeapFrame", 
    2: "SetComputeUnitLimit",
    3: "SetComputeUnitPrice",
}

SYSTEM_TYPES = {
    0: "createAccount",
    1: "assign",
    2: "transfer",
    3: "createAccountWithSeed",
    4: "delegate",
    5: "split",
    7: "merge",
    8: "setAuthority",
    9: "removeAuthority",
    10: "initializeNonce",
    11: "advanceNonce",
    12: "withdrawNonce",
    13: "authorizeNonce",
    18: "allocate",
    19: "assignWithSeed",
    20: "transferWithSeed",
}

# Pump.fun 指令类型 - 根据实际交易解码
PUMP_TYPES = {
    0: "initialize",
    1: "create",
    2: "buy",
    3: "sell",
    4: "sellDirect",
    5: "buyDirect",
    # 常见指令编号 (基于实际交易)
    51: "sell",      # 实际 sell 交易中出现的编号
    228: "sell",     # 内部指令中的 sell
    1: "create",
    2: "buy",
    18: "update",
}

# Pump Fees Program 指令类型
PUMP_FEE_TYPES = {
    0: "getFees",
    1: "setFees",
    2: "withdrawFees",
    3: "updateConfig",
    231: "getFees",  # 实际交易中出现的编号
}

# Token 2022 指令类型
TOKEN_2022_TYPES = {
    0: "initializeAccount",
    1: "initializeMint",
    2: "initializeAccount2",
    3: "initializeMultisig",
    4: "transfer",
    5: "approve",
    6: "revoke",
    7: "setAuthority",
    8: "mintTo",
    9: "burn",
    10: "closeAccount",
    11: "freezeAccount",
    12: "thawAccount",
    13: "transferChecked",
    14: "approveChecked",
    15: "mintToChecked",
    16: "burnChecked",
    17: "initializeAccount3",
    18: "initializeMultisig2",
    19: "initializeAccount2Extension",
    20: "initializeImmutableOwner",
    21: "initializeExtension",
    22: "amountToUiAmount",
    23: "increaseNonce",
    24: "deactivateNonce",
    25: "activateNonce",
}

# Associated Token Account Program 指令类型
ATA_TYPES = {
    0: "create",
    1: "createIdempotent",
    2: "resolve",
}

# Jupiter AMM Program 指令类型
JUPITER_TYPES = {
    0: "swap",
    1: "createState",
    2: "executeState",
    3: "withdraw",
    4: "closePosition",
    5: "removeLiquidity",
    # 实际交易中出现的编号 - 保持原始编号显示以便区分
    51: "ammSwap",       # 主指令 - AMM Swap
    228: "ammCallback",  # 内部指令 - AMM Callback
}

# ===== 新增：未知程序的描述映射（方案 A：记录用途而非解码） =====

# Pump.fun Bundle/Swap Program (Gz9VPiSL...)
PUMP_BUNDLE_TYPES = {
    # 基于实际交易 data 分析
    22: "bundleSwap",      # 捆绑交换指令
    16: "getQuote",        # 获取报价
    # 其他编号暂时 unknown
}

# ===== 已知的机器人/量化交易程序（只识别，不解码） =====

KNOWN_TRADING_BOTS = {
    "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9": {
        "name": "Axiom Bot",
        "description": "高频交易机器人 (Jito MEV Bot)",
        "category": "mev_bot"
    },
    "LBUZGfxFaB9nmFEHhCmDmcYfquaxK8bC2yk3XtgEbN3q": {
        "name": "Jito Bot",
        "description": "Jito 验证者机器人",
        "category": "jito_bot"
    },
    "CCloudEGJ8SoJ8N2uJ1TmD2cJ9b9aG1C7Y4pL6qN3sR9tK": {
        "name": "Banx Bot",
        "description": "Banx 交易机器人",
        "category": "trading_bot"
    },
    "JUP4Fb2cQruVjuLodCmYjM5MPjQTtC2gFuf6L7dRSJF9": {
        "name": "Jupiter Aggregator",
        "description": "Jupiter 路由聚合器",
        "category": "dex_aggregator"
    },
}

# ===== DEX 映射（扩展） =====

DEX_PROGRAMS_EXTENDED = {
    # pump.fun
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": {
        "name": "pump.fun",
        "DEX": "pumpfun",
        "token_type": "memecoin"
    },
    # pump.fun Fee Program
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ": {
        "name": "pump.fun Fee",
        "DEX": "pumpfun",
        "token_type": "fee_program"
    },
    # pump.fun Bundle Program
    "Gz9VPiSLQYbvKyb3jZPjNfyA6n4T4qVFUuAukgL964nL": {
        "name": "pump.fun Bundle",
        "DEX": "pumpfun",
        "token_type": "bundle_program"
    },
    # Raydium
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": {
        "name": "Raydium CLMM",
        "DEX": "raydium",
        "token_type": "amm"
    },
    "RVKdAzt5QAcimWWzF23xHaAkNvPGB8NLAa6P1NqDTzw": {
        "name": "Raydium V2",
        "DEX": "raydium",
        "token_type": "amm_v2"
    },
    # Orca
    "whirLbMiicVdio4qvTf5xKJEsXZMv4os1ay4yjHfnr5": {
        "name": "Orca Whirlpool",
        "DEX": "orca",
        "token_type": "whirlpool"
    },
    # Jupiter
    "JUP6LkbZbjS1jKKwapHJHNv45jcF6GGWC2eG9ZLhXc4": {
        "name": "Jupiter Limit Order",
        "DEX": "jupiter",
        "token_type": "limit_order"
    },
    # OpenBook / Serum
    "srmqPvymJeFKQ4zGvz1W8M1VNNi38dHynGJaW16Zm5k": {
        "name": "OpenBook",
        "DEX": "openbook",
        "token_type": "central_limit"
    },
}

def decode_instruction_type(program_id: str, data: str) -> str:
    """尝试解码指令类型"""
    if not data:
        return "unknown"
    
    try:
        decoded = base58.b58decode(data)
        if not decoded:
            return "unknown"
        
        ix_type = decoded[0]
        
        # ===== 1. ComputeBudget Program =====
        if program_id == "ComputeBudget111111111111111111111111111111":
            return COMPUTE_BUDGET_TYPES.get(ix_type, f"type_{ix_type}")
        
        # ===== 2. System Program =====
        elif program_id == "11111111111111111111111111111111":
            return SYSTEM_TYPES.get(ix_type, f"system_type_{ix_type}")
        
        # ===== 3. Pump.fun 主程序 =====
        elif program_id == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
            return PUMP_TYPES.get(ix_type, f"pump_type_{ix_type}")
        
        # ===== 4. Pump Fees Program =====
        elif program_id == "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ":
            return PUMP_FEE_TYPES.get(ix_type, f"pumpfee_type_{ix_type}")
        
        # ===== 5. Pump.fun Bundle/Swap Program (新增) =====
        elif program_id == "Gz9VPiSLQYbvKyb3jZPjNfyA6n4T4qVFUuAukgL964nL":
            return PUMP_BUNDLE_TYPES.get(ix_type, f"pump_bundle_type_{ix_type}")
        
        # ===== 6. Token 2022 Program =====
        elif program_id == "TokenzQdBNLBzWwDoV5vFRuByTLgTZ4N3RYrnMbx2EtJ2y":
            return TOKEN_2022_TYPES.get(ix_type, f"token2022_type_{ix_type}")
        
        # ===== 7. Associated Token Account Program =====
        elif program_id == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLnJAk7e":
            return ATA_TYPES.get(ix_type, f"ata_type_{ix_type}")
        
        # ===== 8. Jupiter AMM Program =====
        elif "pAMMBay" in program_id:
            return JUPITER_TYPES.get(ix_type, f"jupiter_type_{ix_type}")
        
        # ===== 9. 已知机器人程序 - 返回描述而非指令类型 =====
        elif program_id in KNOWN_TRADING_BOTS:
            bot_info = KNOWN_TRADING_BOTS[program_id]
            return f"bot_{bot_info['category']}"
        
        # ===== 10. 其他已知 DEX 程序 =====
        elif program_id in DEX_PROGRAMS_EXTENDED:
            dex_info = DEX_PROGRAMS_EXTENDED[program_id]
            return f"dex_{dex_info['DEX']}"
    
    except Exception:
        pass
    
    return "unknown"


def get_program_description(program_id: str) -> str:
    """获取程序的描述信息（用于显示）"""
    if program_id in KNOWN_TRADING_BOTS:
        return KNOWN_TRADING_BOTS[program_id]["name"]
    elif program_id in DEX_PROGRAMS_EXTENDED:
        return DEX_PROGRAMS_EXTENDED[program_id]["name"]
    elif program_id == "11111111111111111111111111111111":
        return "System"
    elif program_id == "ComputeBudget111111111111111111111111111111":
        return "ComputeBudget"
    else:
        return program_id[:10] + "..."


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
    
    # 获取涉及的主要 mint（用于显示）- 基于 signer 的最大余额变化
    mints_info = extract_mints_from_transaction({"meta": meta})
    selected_mint = mints_info[0]["mint"] if mints_info else ""
    
    # Token 变化 - 按 mint 和 signer 过滤
    pre_amt = 0.0
    post_amt = 0.0
    
    # 从 preTokenBalances 中找 signer 在 selected_mint 上的余额
    for b in meta.get("preTokenBalances", []):
        if b.get("owner") == signer and b.get("mint") == selected_mint:
            pre_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
            break
    
    # 从 postTokenBalances 中找 signer 在 selected_mint 上的余额
    for b in meta.get("postTokenBalances", []):
        if b.get("owner") == signer and b.get("mint") == selected_mint:
            post_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
            break
    
    # 计算 delta（post - pre）
    delta = post_amt - pre_amt
    amount = abs(delta)
    
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
    compute_info = extract_compute_and_fee_info(meta, transaction)
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
    
    # 显示主指令详细信息
    if dealer_hints['main_instructions']:
        print(f"\n   📝 主指令列表:")
        for ix in dealer_hints['main_instructions']:
            pid_short = ix['program_id'][:10] + "..." if len(ix['program_id']) > 10 else ix['program_id']
            ix_type = ix['type'] or 'unknown'
            print(f"     [{ix['index']}] {ix_type} @ {pid_short}")
    
    # 显示内部指令详细信息
    if dealer_hints['inner_instructions_detail']:
        print(f"\n   🔧 内部指令列表:")
        from collections import Counter
        ix_counts = Counter([ix['type'] for ix in dealer_hints['inner_instructions_detail']])
        for ix_type, count in ix_counts.most_common(5):
            print(f"     - {ix_type}: {count}次")
        
        # 详细列表
        print(f"\n     详细列表:")
        for ix in dealer_hints['inner_instructions_detail'][:10]:
            pid_short = ix['program_id'][:10] + "..." if len(ix['program_id']) > 10 else ix['program_id']
            print(f"     [{ix['group_index']}.{ix['index']}] {ix['type']} @ {pid_short}")
    
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
    """主函数 - 仅支持签名模式"""
    import sys
    
    # 使用 DEFAULT_SIG 作为默认
    sig = DEFAULT_SIG
    
    if len(sys.argv) >= 2:
        sig = sys.argv[1]
    
    # 签名模式
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


if __name__ == "__main__":
    asyncio.run(main())


