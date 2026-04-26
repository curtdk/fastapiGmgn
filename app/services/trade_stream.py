"""实时交易流服务 - Helius WebSocket 订阅 + 实时推送"""
import asyncio
import base58
import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
import websockets

from app.models.models import Transaction
from app.services.trade_processor import enqueue_trade
from app.services import tx_redis
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

HELIUS_WS_URL = "wss://mainnet.helius-rpc.com?api-key={api_key}"
HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"


class TradeStream:
    """实时交易流引擎"""

    def __init__(self, mint: str, api_key: str = ""):
        self.mint = mint
        self.api_key = api_key
        self.running = False
        self.ws: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None
        self.sync_point: Optional[str] = None  # 第一条 WS 交易的 signature

    async def start(self):
        """启动实时流"""
        self.running = True
        self._task = asyncio.create_task(self._stream_loop())
        logger.info(f"[实时流] 启动: {self.mint}")

    async def stop(self):
        """停止实时流"""
        self.running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"[实时流] 停止: {self.mint}")

    async def _stream_loop(self):
        """WebSocket 订阅循环"""
        url = HELIUS_WS_URL.format(api_key=self.api_key)

        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    self.ws = ws

                    # 发送订阅请求
                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "transactionSubscribe",
                        "params": [
                            {
                                "accountInclude": [self.mint],
                            },
                            {
                                "commitment": "confirmed",
                                "encoding": "jsonParsed",
                                "transactionDetails": "full",
                                "maxSupportedTransactionVersion": 0
                            }
                        ]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"[实时流] 已发送订阅请求: {self.mint}")

                    # 等待确认
                    resp = await asyncio.wait_for(ws.recv(), timeout=10)
                    logger.info(f"[实时流] 订阅确认: {resp}")

                    await ws_manager.broadcast(self.mint, {
                        "type": "status",
                        "data": {
                            "mint": self.mint,
                            "status": "STREAMING",
                            "message": "实时监听中...",
                        }
                    })

                    # 持续接收消息
                    while self.running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            await self._handle_message(msg)
                        except asyncio.TimeoutError:
                            # 心跳超时，继续等待
                            continue
                        except websockets.ConnectionClosed:
                            logger.warning("[实时流] 连接断开，尝试重连...")
                            break

            except Exception as e:
                logger.error(f"[实时流] 异常: {e}", exc_info=True)
                await ws_manager.broadcast(self.mint, {
                    "type": "error",
                    "data": {"mint": self.mint, "message": f"实时流异常: {str(e)}"}
                })

            # 重连等待
            if self.running:
                await asyncio.sleep(3)

    async def _handle_message(self, msg: str):
        """处理收到的 WebSocket 消息"""
        try:
            data = json.loads(msg)

            # 跳过订阅确认和心跳
            if "result" in data and "params" not in data:
                return
            if data.get("method") == "subscriptionNotification":
                return

            # 解析交易数据
            params = data.get("params", {})
            result = params.get("result", {})
            if not result:
                return

            sig = result.get("signature", "")
            if not sig:
                return

            transaction = result.get("transaction", {})
            meta = transaction.get("meta", {})

            if meta.get("err"):
                logger.debug(f"[实时流] 跳过错误 sig: {sig[:8]}...")
                return

            # 构建 tx_data 结构
            tx_data = {
                "_signature": sig,
                "slot": result.get("slot", 0),
                "blockTime": result.get("blockTime"),
                "transaction": result.get("transaction", {}),
                "meta": meta,
            }

            tx_detail = self._extract_trade_info(tx_data)
            if not tx_detail:
                return

            # 写入 Redis（去重）
            sig = tx_detail.get("sig", "")
            try:
                # 检查是否已存在
                existing = await tx_redis.get_tx(sig)
                if existing:
                    return

                # 保存到 Redis
                await tx_redis.save_tx(tx_detail)
                # 添加到有序集合（ws 数据源）
                await tx_redis.add_tx_to_list(self.mint, sig, "ws")

                if self.sync_point is None:
                    self.sync_point = sig
                    logger.info(f"[实时流] sync_point 已设置: {sig}")

                # 加入处理队列
                await enqueue_trade(tx_detail)
            except Exception as e:
                logger.warning(f"[实时流] Redis 写入失败: {e}")

        except Exception as e:
            logger.error(f"[实时流] 消息处理失败: {e}", exc_info=True)

    # ===== 已知 Jito 小费地址 =====
    KNOWN_JITO_TIP_ACCOUNTS = {
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvQss8hp11i4wT8PQ69LBAatPfitZfFWhS",
        "ADaUMid9yfUytqMBgopwjb2DTLSokTSzLJt6c6GmGwfT",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMFPjRZaLkL3TJppF",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        "E2eSqe33tuhFfLw6XAvz4p88KaYBjmJ8fQiEphb5fMQB",
        "F2ZuGsQdfBh1bxMVRb1x6c84HaYJK2BQ3wJQvYmHhGeh",
        "GhZpKJP1MoBfKtVqR9FqKfXqBmJ8VqJqKqJqKqJqKqJq",
        "9Dv82cYxKPkE7Xh7F2oFFBmFKq7kQXip4HbQJQmGzXo5",
        "DttWaMKVfTmBd9oqJpPwQXV9o3bB6q3qKqJqKqJqKqJq",
    }

    def _resolve_owner_from_index(self, token_balance: Dict[str, Any], account_keys: list, signer: str) -> Optional[str]:
        """当 token balance 缺少 owner 字段时，通过 accountIndex 关联 accountKeys 获取 owner"""
        account_index = token_balance.get("accountIndex")
        if account_index is None:
            return None
        if account_index < 0 or account_index >= len(account_keys):
            return None
        key_entry = account_keys[account_index]
        if isinstance(key_entry, dict):
            return key_entry.get("pubkey", "")
        return key_entry if isinstance(key_entry, str) else ""

    def _detect_jito_tip(self, inner_instructions, signer: str) -> int:
        """检测 Jito 小费金额"""
        transfers = []
        for ix_group in inner_instructions:
            for ix in ix_group.get("instructions", []):
                is_system = (ix.get("program") == "system" or ix.get("programId") == "11111111111111111111111111111111")
                if not is_system:
                    continue
                parsed = ix.get("parsed", {})
                if parsed.get("type") != "transfer":
                    continue
                info = parsed.get("info", {})
                if info.get("source") != signer:
                    continue
                lamports = info.get("lamports", 0)
                dest = info.get("destination", "")
                if lamports > 0 and dest:
                    transfers.append(lamports)

        if len(transfers) >= 2:
            return min(transfers)
        if len(transfers) == 1:
            for ix_group in inner_instructions:
                for ix in ix_group.get("instructions", []):
                    is_system = (ix.get("program") == "system" or ix.get("programId") == "11111111111111111111111111111111")
                    if not is_system:
                        continue
                    parsed = ix.get("parsed", {})
                    if parsed.get("type") != "transfer":
                        continue
                    info = parsed.get("info", {})
                    if (info.get("source") == signer and info.get("lamports", 0) == transfers[0] and info.get("destination", "") in self.KNOWN_JITO_TIP_ACCOUNTS):
                        return transfers[0]
        return 0

    def _scan_dex_recursive(self, inner_instructions) -> str:
        """递归扫描 innerInstructions 识别 DEX"""
        dex = ""
        for ix_group in inner_instructions:
            for ix in ix_group.get("instructions", []):
                program_id = ix.get("programId", "")
                program_name = ix.get("program", "").lower()
                pid_lower = program_id.lower()
                if "pump" in pid_lower or "pump" in program_name or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in program_id:
                    return "pump.fun"
                elif "raydium" in pid_lower or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in program_id:
                    dex = "raydium"
                elif "orca" in pid_lower:
                    dex = "orca"
        return dex

    def _decode_instruction_type(self, program_id: str, data: str) -> str:
        """尝试解码指令类型"""
        if not data:
            return "unknown"
        try:
            decoded = base58.b58decode(data)
            if not decoded:
                return "unknown"
            ix_type = decoded[0]
            # ComputeBudget Program
            if program_id == "ComputeBudget111111111111111111111111111111":
                types = {0: "RequestUnitsDeprecated", 1: "RequestHeapFrame", 2: "SetComputeUnitLimit", 3: "SetComputeUnitPrice"}
                return types.get(ix_type, f"type_{ix_type}")
            # System Program
            elif program_id == "11111111111111111111111111111111":
                types = {0: "createAccount", 1: "assign", 2: "transfer", 8: "setAuthority", 10: "initializeNonce"}
                return types.get(ix_type, f"system_type_{ix_type}")
            # Pump.fun
            elif "6EF8rrecth" in program_id:
                types = {2: "buy", 3: "sell"}
                return types.get(ix_type, f"pump_type_{ix_type}")
        except Exception:
            pass
        return "unknown"

    def _extract_compute_info(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """提取 Compute Unit 和 Priority Fee 信息
        
        Solana ComputeBudget 指令格式：
        - SetComputeUnitLimit (opcode 2): opcode(1) + u32(4) = 5 bytes
        - SetComputeUnitPrice (opcode 3): opcode(1) + u64(8) = 9 bytes
        """
        result = {"cu_limit": 200000, "cu_price": 0, "cu_consumed": 0}
        try:
            instructions = message.get("instructions", [])
            for ix in instructions:
                program_id = ix.get("programId", "")
                if program_id == "ComputeBudget111111111111111111111111111111":
                    data = ix.get("data", "")
                    if data and len(data) > 2:
                        decoded = base58.b58decode(data)
                        if len(decoded) >= 1:
                            ix_type = decoded[0]
                            if ix_type == 2:  # SetComputeUnitLimit
                                if len(decoded) >= 5:
                                    result["cu_limit"] = int.from_bytes(decoded[1:5], 'little')
                            elif ix_type == 3:  # SetComputeUnitPrice
                                if len(decoded) >= 9:
                                    result["cu_price"] = int.from_bytes(decoded[1:9], 'little')
        except Exception:
            pass
        return result

    def _extract_instruction_details(self, message: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
        """提取指令详细信息"""
        result = {
            "instructions_count": 0,
            "inner_instructions_count": 0,
            "total_instruction_count": 0,
            "account_keys_count": 0,
            "uses_lookup_table": False,
            "signers_count": 0,
            "main_instructions": [],
            "inner_instructions": [],
            "program_ids": [],
        }
        
        account_keys = message.get("accountKeys", [])
        result["account_keys_count"] = len(account_keys)
        
        for key in account_keys:
            if isinstance(key, dict):
                if key.get("signer"):
                    result["signers_count"] += 1
                if key.get("source") == "lookupTable":
                    result["uses_lookup_table"] = True
        
        instructions = message.get("instructions", [])
        result["instructions_count"] = len(instructions)
        
        for idx, ix in enumerate(instructions):
            program_id = ix.get("programId", "")
            ix_type = ix.get("parsed", {}).get("type", "") or ix.get("instructionType", "")
            if not ix_type:
                ix_type = self._decode_instruction_type(program_id, ix.get("data", ""))
            
            result["main_instructions"].append({
                "index": idx,
                "program_id": program_id,
                "type": ix_type,
            })
            if program_id and program_id not in result["program_ids"]:
                result["program_ids"].append(program_id)
        
        inner_count = 0
        for ix_group in meta.get("innerInstructions", []):
            group_index = ix_group.get("index", 0)
            for idx, ix in enumerate(ix_group.get("instructions", [])):
                inner_count += 1
                program_id = ix.get("programId", "")
                ix_type = ix.get("parsed", {}).get("type", "") or ix.get("instructionType", "")
                if not ix_type:
                    ix_type = self._decode_instruction_type(program_id, ix.get("data", ""))
                
                result["inner_instructions"].append({
                    "group_index": group_index,
                    "index": idx,
                    "program_id": program_id,
                    "type": ix_type,
                })
                if program_id and program_id not in result["program_ids"]:
                    result["program_ids"].append(program_id)
        
        result["inner_instructions_count"] = inner_count
        result["total_instruction_count"] = result["instructions_count"] + inner_count
        
        return result

    def _analyze_risk(self, hints: Dict[str, Any], priority_fee: float) -> Dict[str, Any]:
        """分析风险指标"""
        indicators = []
        score = 0
        
        if hints["account_keys_count"] > 20:
            indicators.append(f"高账户数: {hints['account_keys_count']}")
            score += 25
        elif hints["account_keys_count"] > 15:
            indicators.append(f"中等账户数: {hints['account_keys_count']}")
            score += 10
        
        if hints["uses_lookup_table"]:
            indicators.append("使用了地址查找表 (ALT)")
            score += 20
        
        if priority_fee > 0.5:
            indicators.append(f"高 Priority Fee: {priority_fee:.6f} SOL")
            score += 20
        elif priority_fee > 0.1:
            indicators.append(f"中等 Priority Fee: {priority_fee:.6f} SOL")
            score += 10
        
        if hints["instructions_count"] > 1:
            indicators.append("复杂交易 (非简单转账)")
            score += 10
        
        dex_programs = ["6EF8rrecth", "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"]
        is_dex = any(pid in hints["program_ids"] for pid in dex_programs)
        if is_dex:
            indicators.append("DEX 交易")
            score += 5
        
        if score >= 60:
            verdict = "高风险"
        elif score >= 30:
            verdict = "中等风险"
        elif score >= 15:
            verdict = "低风险"
        else:
            verdict = "普通"
        
        return {"score": min(score, 100), "verdict": verdict, "indicators": indicators}

    def _extract_trade_info(self, tx_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 Helius WS 交易数据中提取交易信息（增强版）"""
        try:
            import json
            sig = tx_data.get("_signature", "")
            if not sig:
                return None

            meta = tx_data.get("meta", {})
            if meta.get("err"):
                return None

            block_time = tx_data.get("blockTime")
            if block_time:
                block_time = datetime.utcfromtimestamp(block_time)

            slot = tx_data.get("slot", 0)

            inner_tx = tx_data.get("transaction", {}).get("transaction", {})
            if not inner_tx:
                inner_tx = tx_data.get("transaction", {})

            message = inner_tx.get("message", {})
            account_keys = message.get("accountKeys", [])

            signer = ""
            signer_index = 0
            for i, key in enumerate(account_keys):
                if isinstance(key, dict):
                    if key.get("signer"):
                        signer = key.get("pubkey", "")
                        signer_index = i
                        break
                elif isinstance(key, str):
                    signer = key
                    signer_index = i
                    break

            if not signer:
                return None

            pre_sol_balances = meta.get("preBalances", [])
            post_sol_balances = meta.get("postBalances", [])
            fee_lamports = meta.get("fee", 0)
            sol_spent = 0.0
            jito_tip = 0.0
            if (pre_sol_balances and post_sol_balances
                    and len(pre_sol_balances) > signer_index
                    and len(post_sol_balances) > signer_index):
                raw_sol_spent = (pre_sol_balances[signer_index]
                                 - post_sol_balances[signer_index]
                                 - fee_lamports)
                inner_instructions = meta.get("innerInstructions", [])
                jito_tip = self._detect_jito_tip(inner_instructions, signer)
                sol_spent = (raw_sol_spent - jito_tip) / 1e9

            pre_token_list = meta.get("preTokenBalances", [])
            post_token_list = meta.get("postTokenBalances", [])

            pre_amt = 0.0
            post_amt = 0.0
            to_owner = ""

            for b in post_token_list:
                if b.get("mint") != self.mint:
                    continue
                owner = b.get("owner")
                if not owner:
                    owner = self._resolve_owner_from_index(b, account_keys, signer)
                if owner == signer:
                    post_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
                    to_owner = owner
                    break

            for b in pre_token_list:
                if b.get("mint") != self.mint:
                    continue
                owner = b.get("owner")
                if not owner:
                    owner = self._resolve_owner_from_index(b, account_keys, signer)
                if owner == signer:
                    pre_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
                    break

            delta = post_amt - pre_amt
            amount = abs(delta)

            tx_type = "TRANSFER"
            from_addr = signer
            to_addr = ""

            if delta > 0:
                tx_type = "BUY"
                to_addr = to_owner
            elif delta < 0:
                tx_type = "SELL"
            elif abs(sol_spent) <= 0.001:
                tx_type = "TRANSFER"

            dex = self._scan_dex_recursive(meta.get("innerInstructions", []))
            pool_address = ""

            # 新增：指令详细信息（需要在 priority_fee 计算前获取 signers_count）
            instruction_details = self._extract_instruction_details(message, meta)

            # 新增：Compute Unit 和 Priority Fee
            compute_info = self._extract_compute_info(message)
            cu_consumed = meta.get("computeUnitsConsumed", 0)
            cu_limit = compute_info["cu_limit"]
            cu_price = compute_info["cu_price"]
            
            # 计算 priority_fee：从 total_fee - base_fee 计算（更可靠）
            # base_fee = 签名数 * 5000 lamports
            signers_count = instruction_details["signers_count"]
            base_fee = max(1, signers_count) * 5000
            priority_lamports = max(0, fee_lamports - base_fee)
            priority_fee = priority_lamports / 1e9

            # 新增：风险分析
            risk_info = self._analyze_risk(instruction_details, priority_fee)

            return {
                "sig": sig,
                "slot": slot,
                "block_time": block_time,
                "from_address": from_addr,
                "to_address": to_addr,
                "amount": amount,
                "token_mint": self.mint,
                "token_symbol": "",
                "transaction_type": tx_type,
                "dex": dex,
                "pool_address": pool_address,
                "sol_spent": sol_spent,
                "fee": fee_lamports / 1e9,
                "jito_tip": jito_tip / 1e9 if jito_tip else 0.0,
                # 新增字段
                "priority_fee": priority_fee,
                "cu_consumed": cu_consumed,
                "cu_limit": cu_limit,
                "cu_price": cu_price,
                "instructions_count": instruction_details["instructions_count"],
                "inner_instructions_count": instruction_details["inner_instructions_count"],
                "total_instruction_count": instruction_details["total_instruction_count"],
                "account_keys_count": instruction_details["account_keys_count"],
                "uses_lookup_table": instruction_details["uses_lookup_table"],
                "signers_count": instruction_details["signers_count"],
                "main_instructions": json.dumps(instruction_details["main_instructions"]),
                "inner_instructions": json.dumps(instruction_details["inner_instructions"]),
                "program_ids": json.dumps(instruction_details["program_ids"]),
                "risk_score": risk_info["score"],
                "risk_verdict": risk_info["verdict"],
                "risk_indicators": json.dumps(risk_info["indicators"]),
                "raw_data": str(tx_data)[:20000],
                "source": "helius_ws",
            }
        except Exception as e:
            logger.warning(f"[实时流] 解析失败: {e}")
            return None
