"""实时交易流服务 - Helius WebSocket 订阅 + 实时推送"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
import websockets

from app.models.models import Transaction
from app.services.trade_processor import process_trade
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
        """处理收到的 WebSocket 消息

        实际消息格式:
        {
          "jsonrpc": "2.0",
          "method": "transactionNotification",
          "params": {
            "subscription": 8811454,
            "result": {
              "slot": 413098808,
              "signature": "jzbUx...",
              "transaction": { "transaction": { "signatures": [...], "message": {...} } },
              "meta": { "preBalances": [...], "postBalances": [...], ... }
            }
          }
        }
        """
        try:
            data = json.loads(msg)

            # 跳过订阅确认和心跳
            if "result" in data and "params" not in data:
                return
            if data.get("method") == "subscriptionNotification":
                return

            # 解析交易数据 — Helius WS 实际格式
            params = data.get("params", {})
            result = params.get("result", {})
            if not result:
                return

            # 提取 signature
            sig = result.get("signature", "")
            if not sig:
                return

            # 提前检查 meta.err，有错误的 sig 直接跳过
            transaction=result.get("transaction", {})
            meta = transaction.get("meta", {})

            # meta = result.get("meta", {})
            if meta.get("err"):
                logger.debug(f"[实时流] 跳过错误 sig: {sig[:8]}... err={meta['err']}")
                return

            # 构建 tx_data 结构，与 _extract_trade_info 期望的格式一致
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

            # 入库（去重）
            sig = tx_detail.get("sig", "")
            existing = None
            from app.utils.database import SessionLocal
            db = SessionLocal()
            try:
                existing = db.query(Transaction).filter(Transaction.sig == sig).first()
                if not existing:
                    tx_record = Transaction(**tx_detail)
                    db.add(tx_record)
                    db.commit()

                    # 记录第一条 WS 交易作为 sync_point
                    if self.sync_point is None:
                        self.sync_point = sig
                        logger.info(f"[实时流] sync_point 已设置: {sig}")

                    db.close()

                    # 处理并推送到前端（用新 session，确保 db 可用）
                    db2 = SessionLocal()
                    try:
                        await process_trade(db2, tx_detail)
                    finally:
                        db2.close()
            except Exception as e:
                logger.warning(f"[实时流] 入库失败: {e}")
                db.rollback()
            finally:
                db.close()

        except Exception as e:
            logger.error(f"[实时流] 消息处理失败: {e}", exc_info=True)

    # ===== 已知 Jito 小费地址（用于从 sol_spent 中扣除） =====
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
        """检测 Jito 小费金额：从 signer 发出的最小 SOL 转账视为小费"""
        transfers = []
        for ix_group in inner_instructions:
            for ix in ix_group.get("instructions", []):
                is_system = (ix.get("program") == "system"
                             or ix.get("programId") == "11111111111111111111111111111111")
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

        # 多个转账时，最小的视为小费
        if len(transfers) >= 2:
            return min(transfers)
        # 单个转账但目标是已知 Jito 地址
        if len(transfers) == 1:
            for ix_group in inner_instructions:
                for ix in ix_group.get("instructions", []):
                    is_system = (ix.get("program") == "system"
                                 or ix.get("programId") == "11111111111111111111111111111111")
                    if not is_system:
                        continue
                    parsed = ix.get("parsed", {})
                    if parsed.get("type") != "transfer":
                        continue
                    info = parsed.get("info", {})
                    if (info.get("source") == signer
                            and info.get("lamports", 0) == transfers[0]
                            and info.get("destination", "") in self.KNOWN_JITO_TIP_ACCOUNTS):
                        return transfers[0]
        return 0

    def _scan_dex_recursive(self, inner_instructions) -> str:
        """递归扫描 innerInstructions 识别 DEX（处理 Flash 执行器嵌套）"""
        dex = ""
        for ix_group in inner_instructions:
            for ix in ix_group.get("instructions", []):
                program_id = ix.get("programId", "")
                program_name = ix.get("program", "").lower()
                # 检查 programId 和 parsed 中的 program 字段
                pid_lower = program_id.lower()
                if "pump" in pid_lower or "pump" in program_name or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in program_id:
                    return "pump.fun"
                elif "raydium" in pid_lower or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in program_id:
                    dex = "raydium"
                elif "orca" in pid_lower:
                    dex = "orca"
        return dex

    def _extract_trade_info(self, tx_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 Helius WS 交易数据中提取交易信息（资金流向优先协议）

        适配 Helius transactionNotification 真实数据结构:
        - 交易体位于 params.result
        - 双层嵌套: result['transaction']['transaction']
        - accountKeys 是对象列表 {"pubkey": "...", "signer": true}
        - Token balance 可能缺少 owner 字段，需通过 accountIndex 关联
        - 扣除 Jito 小费避免 sol_spent 虚高
        - 递归扫描 innerInstructions 识别嵌套 DEX
        """
        try:
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

            # ===== 获取 accountKeys（适配双层嵌套 + 对象化结构） =====
            inner_tx = tx_data.get("transaction", {}).get("transaction", {})
            if not inner_tx:
                # 兼容旧格式: transaction.message.accountKeys
                inner_tx = tx_data.get("transaction", {})

            message = inner_tx.get("message", {})
            account_keys = message.get("accountKeys", [])

            # 获取 signer（第一个 signer=true 的对象）
            signer = ""
            signer_index = 0
            for i, key in enumerate(account_keys):
                if isinstance(key, dict):
                    if key.get("signer"):
                        signer = key.get("pubkey", "")
                        signer_index = i
                        break
                elif isinstance(key, str):
                    # 兼容旧格式（纯字符串列表，第一个即为 signer）
                    signer = key
                    signer_index = i
                    break

            if not signer:
                return None

            # ===== SOL 流向计算（净值，使用 signer_index 匹配余额数组） =====
            pre_sol_balances = meta.get("preBalances", [])
            post_sol_balances = meta.get("postBalances", [])
            fee_lamports = meta.get("fee", 0)
            sol_spent = 0.0
            if (pre_sol_balances and post_sol_balances
                    and len(pre_sol_balances) > signer_index
                    and len(post_sol_balances) > signer_index):
                raw_sol_spent = (pre_sol_balances[signer_index]
                                 - post_sol_balances[signer_index]
                                 - fee_lamports)

                # 扣除 Jito 小费（避免支出统计虚高）
                inner_instructions = meta.get("innerInstructions", [])
                jito_tip = self._detect_jito_tip(inner_instructions, signer)
                sol_spent = (raw_sol_spent - jito_tip) / 1e9

            # ===== Token 余额变化（适配 owner 缺失时通过 accountIndex 关联） =====
            pre_token_list = meta.get("preTokenBalances", [])
            post_token_list = meta.get("postTokenBalances", [])

            pre_amt = 0.0
            post_amt = 0.0
            to_owner = ""

            for b in post_token_list:
                if b.get("mint") != self.mint:
                    continue
                # 优先使用 owner 字段
                owner = b.get("owner")
                if not owner:
                    # 通过 accountIndex 关联 accountKeys 获取 owner
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

            # ===== DEX 深度扫描（递归 innerInstructions） =====
            dex = self._scan_dex_recursive(meta.get("innerInstructions", []))
            pool_address = ""

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
                "raw_data": str(tx_data),
                "source": "helius_ws",
            }
        except Exception as e:
            logger.warning(f"[实时流] 解析失败: {e}")
            return None
