"""历史交易回填服务 - getTransactionsForAddress 一步获取签名+交易详情"""
import asyncio
import base58
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert

from app.services.settings_service import get_int_setting, get_float_setting
from app.services import tx_redis
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"


class TradeBackfill:
    """历史交易回填引擎（getTransactionsForAddress 一步到位）"""

    def __init__(self, db: Session, mint: str, stream=None):
        self.db = db
        self.mint = mint
        self.stream = stream  # TradeStream 引用，用于访问 sync_point
        self.running = False
        self.total_fetched = 0
        self.earliest_slot: Optional[int] = None

    def _get_api_key(self) -> str:
        """获取 Helius API Key"""
        from app.services.settings_service import get_setting
        return get_setting(self.db, "helius_api_key") or ""

    def _get_network(self) -> str:
        """获取网络"""
        import os
        return os.getenv("HELIUS_NETWORK", "mainnet-beta")

    async def run(self):
        """执行回填流程"""
        self.running = True
        self.total_fetched = 0

        try:
            # 1. 等待 TradeStream 的 sync_point 就绪
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "WAITING_SYNC",
                    "message": "等待实时流 sync_point...",
                }
            })

            sync_point = await self._wait_for_sync_point()
            if not sync_point:
                logger.info("[回填] 等待 sync_point 被中断（停止信号）")
                return

            logger.info(f"[回填] sync_point 已就绪: {sync_point}")

            # 2. 边获取边存入 Redis（保证从旧到新排序）
            self.total_fetched = await self._fetch_all_transactions_before(sync_point)
            logger.info(f"[回填] 共获取 {self.total_fetched} 条有效交易")

            if not self.total_fetched:
                await ws_manager.broadcast(self.mint, {
                    "type": "status",
                    "data": {
                        "mint": self.mint,
                        "status": "STREAMING",
                        "message": "无历史数据，直接进入实时流",
                        "total_trades": 0,
                    }
                })
                return

            # 3. 回填完成，触发指数计算
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "DATA_READY",
                    "message": f"回填完成，共 {self.total_fetched} 条历史数据，开始计算指标...",
                    "total_trades": self.total_fetched,
                    "progress": 100.0,
                }
            })

            # 4. 触发全量指数计算
            await self._trigger_full_calculation()

        except Exception as e:
            logger.error(f"[回填] 异常: {e}", exc_info=True)
            await ws_manager.broadcast(self.mint, {
                "type": "error",
                "data": {"mint": self.mint, "message": f"回填异常: {str(e)}"}
            })
        finally:
            self.running = False

    async def _wait_for_sync_point(self, timeout: float = 30.0) -> Optional[str]:
        """等待 TradeStream 的 sync_point 就绪，超时后继续等待，不返回"""
        while self.running:
            if self.stream and self.stream.sync_point:
                return self.stream.sync_point
            await asyncio.sleep(0.5)
        return None

    async def _fetch_all_transactions_before(self, sync_point: str) -> int:
        """
        使用 getTransactionsForAddress 分页获取历史交易（含完整详情）
        通过 filters.signature.lt=sync_point 获取该签名之前的交易
        边获取边存入 Redis（保证从旧到新排序），返回保存数量

        优化：
        - 复用单个 AsyncClient（连接池复用，避免每批新建 TCP 连接）
        - 每批之间加间隔，避免打满 Helius RPS 配额抢占 dealer 请求资源
        - 边获取边存入 Redis，无需二次遍历
        """
        pagination_token = None
        batch_size = 100  # transactionDetails: "full" 最大 100
        max_total = 50000
        total_saved = 0
        # 批次间限速间隔（秒）：给 dealer_detector 等其他请求留出带宽
        _BACKFILL_INTERVAL = 0.2

        api_key = self._get_api_key()

        # 复用同一个 AsyncClient，避免每次分页都新建 TCP 连接
        async with httpx.AsyncClient(timeout=60) as client:
            while self.running and total_saved < max_total:
                params = {
                    "limit": batch_size,
                    "commitment": "confirmed",
                    "transactionDetails": "full",
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                    "filters": {
                        "signature": {"lt": sync_point},
                    },
                }
                if pagination_token:
                    params["paginationToken"] = pagination_token

                body = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransactionsForAddress",
                    "params": [self.mint, params],
                }

                resp = await client.post(
                    f"{HELIUS_RPC_URL}/?api-key={api_key}",
                    json=body
                )
                resp.raise_for_status()
                data = resp.json()

                result = data.get("result", {})
                txs = result.get("data", [])
                if not txs:
                    break

                # 解析并直接存入 Redis
                for tx in txs:
                    sig = tx.get("transaction", {}).get("signatures", [""])[0]
                    if not sig:
                        continue

                    meta = tx.get("meta", {})
                    if meta.get("err"):
                        continue

                    tx_data = {
                        "_signature": sig,
                        "slot": tx.get("slot", 0),
                        "blockTime": tx.get("blockTime"),
                        "transaction": tx.get("transaction", {}),
                        "meta": meta,
                    }

                    detail = self._extract_trade_info(tx_data)
                    if detail:
                        # 检查是否已存在
                        existing = await tx_redis.get_tx(sig)
                        if existing:
                            continue
                        
                        # 直接存入 Redis
                        await tx_redis.save_tx(detail)
                        # 添加到有序集合（seq 作为 score，保证从旧到新）
                        await tx_redis.add_tx_to_list(self.mint, sig, "rpc_fill", score=total_saved)
                        total_saved += 1

                # 进度通知
                await ws_manager.broadcast(self.mint, {
                    "type": "status",
                    "data": {
                        "mint": self.mint,
                        "status": "FILLING",
                        "message": f"已保存 {total_saved} 条交易...",
                        "total_trades": total_saved,
                    }
                })

                # 分页
                pagination_token = result.get("paginationToken")
                if not pagination_token:
                    break

                # 批次间间隔
                await asyncio.sleep(_BACKFILL_INTERVAL)

        return total_saved

    # ===== 辅助方法（与 trade_stream.py 保持一致） =====

    def _decode_instruction_type(self, program_id: str, data: str) -> str:
        """尝试解码指令类型"""
        if not data:
            return "unknown"
        try:
            decoded = base58.b58decode(data)
            if not decoded:
                return "unknown"
            ix_type = decoded[0]
            if program_id == "ComputeBudget111111111111111111111111111111":
                types = {0: "RequestUnitsDeprecated", 1: "RequestHeapFrame", 2: "SetComputeUnitLimit", 3: "SetComputeUnitPrice"}
                return types.get(ix_type, f"type_{ix_type}")
            elif program_id == "11111111111111111111111111111111":
                types = {0: "createAccount", 1: "assign", 2: "transfer", 8: "setAuthority", 10: "initializeNonce"}
                return types.get(ix_type, f"system_type_{ix_type}")
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

    def _detect_jito_tip(self, inner_instructions, signer: str) -> int:
        """检测 Jito 小费金额"""
        KNOWN_JITO_TIP_ACCOUNTS = {
            "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
            "HFqU5x63VTqvQss8hp11i4wT8PQ69LBAatPfitZfFWhS",
            "ADaUMid9yfUytqMBgopwjb2DTLSokTSzLJt6c6GmGwfT",
            "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMFPjRZaLkL3TJppF",
            "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        }
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
                    if (info.get("source") == signer and info.get("lamports", 0) == transfers[0] and info.get("destination", "") in KNOWN_JITO_TIP_ACCOUNTS):
                        return transfers[0]
        return 0

    def _extract_trade_info(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从标准 Solana RPC 交易数据中提取关键信息（资金流向优先协议 + 增强版）"""
        try:
            sig = tx.get("_signature", "")
            if not sig:
                return None

            meta = tx.get("meta", {})
            if meta.get("err"):
                return None  # 跳过失败的交易

            block_time = tx.get("blockTime")
            if block_time:
                block_time = datetime.utcfromtimestamp(block_time)

            slot = tx.get("slot", 0)
            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            # 获取 signer（交易发起者）
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

            # ===== SOL 流向计算（净值） =====
            pre_sol_balances = meta.get("preBalances", [])
            post_sol_balances = meta.get("postBalances", [])
            fee_lamports = meta.get("fee", 0)
            sol_spent = 0.0
            jito_tip = 0.0
            if (pre_sol_balances and post_sol_balances
                    and len(pre_sol_balances) > signer_index
                    and len(post_sol_balances) > signer_index):
                raw_sol_spent = pre_sol_balances[signer_index] - post_sol_balances[signer_index] - fee_lamports
                inner_instructions = meta.get("innerInstructions", [])
                jito_tip = self._detect_jito_tip(inner_instructions, signer)
                sol_spent = (raw_sol_spent - jito_tip) / 1e9

            # ===== Token 余额变化（遍历所有 ATA，找 signer 拥有的） =====
            pre_token_list = meta.get("preTokenBalances", [])
            post_token_list = meta.get("postTokenBalances", [])

            pre_amt = 0.0
            post_amt = 0.0
            to_owner = ""

            for b in post_token_list:
                if b.get("mint") == self.mint and b.get("owner") == signer:
                    post_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
                    to_owner = b.get("owner", "")
                    break

            for b in pre_token_list:
                if b.get("mint") == self.mint and b.get("owner") == signer:
                    pre_amt = b.get("uiTokenAmount", {}).get("uiAmount", 0) or 0.0
                    break

            delta = post_amt - pre_amt
            amount = abs(delta)

            # ===== 交易类型判定（资金流向优先） =====
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

            # ===== DEX 检测 =====
            dex = ""
            pool_address = ""
            inner_instructions = meta.get("innerInstructions", [])
            for ix_group in inner_instructions:
                for ix in ix_group.get("instructions", []):
                    program_id = ix.get("programId", "")
                    if "pump" in program_id.lower() or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in program_id:
                        dex = "pump.fun"
                    elif "raydium" in program_id.lower() or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in program_id:
                        dex = "raydium"
                    elif "orca" in program_id.lower():
                        dex = "orca"

            # ===== 新增：指令详细信息（需要在 priority_fee 计算前获取 signers_count） =====
            instruction_details = self._extract_instruction_details(message, meta)

            # ===== 新增：Compute Unit 和 Priority Fee =====
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

            # ===== 新增：风险分析 =====
            risk_info = self._analyze_risk(instruction_details, priority_fee)

            # JSON 序列化列表字段
            import json
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
                "raw_data": str(tx)[:20000],
                "source": "rpc_fill",
            }
        except Exception as e:
            logger.warning(f"[回填] 解析交易失败: {e}")
            return None

    async def _trigger_full_calculation(self):
        """触发全量指数计算"""
        from app.services.trade_processor import run_full_calculation, start_consumer
        from app.services.dealer_detector import start_dealer_consumer
        try:
            # 1. 启动庄家检测消费者
            start_dealer_consumer()
            
            # 2. 历史 tx 直接处理，同时进行庄家检测
            await run_full_calculation(self.db, self.mint)
            
            # 3. 启动消费者，消化队列中积压的 WS 消息
            await start_consumer(self.mint)
            
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "CALCULATION_DONE",
                    "message": "指标计算完成，消费者已启动",
                }
            })
        except Exception as e:
            logger.error(f"[回填] 触发计算失败: {e}")

    def stop(self):
        """停止回填"""
        self.running = False
        logger.info("[回填] 收到停止信号")