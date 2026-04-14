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

            # 构建 tx_data 结构，与 _extract_trade_info 期望的格式一致
            tx_data = {
                "_signature": sig,
                "slot": result.get("slot", 0),
                "blockTime": result.get("blockTime"),
                "transaction": result.get("transaction", {}),
                "meta": result.get("meta", {}),
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

    def _extract_trade_info(self, tx_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 Helius WS 交易数据中提取交易信息（资金流向优先协议）

        提取字段:
        - sig, slot, block_time
        - signer (用户地址)
        - 交易类型 (BUY/SELL/TRANSFER) — SOL 流向 + Token 变化双重校验
        - token 数量变化（遍历所有 ATA，找 signer 拥有的）
        - 消耗 SOL 净值 (preBalances - postBalances - fee)
        - DEX 信息
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

            # 获取 accountKeys — 注意嵌套结构: transaction.transaction.message.accountKeys
            inner_tx = tx_data.get("transaction", {}).get("transaction", {})
            if not inner_tx:
                # 兼容旧格式: transaction.message.accountKeys
                inner_tx = tx_data.get("transaction", {})

            message = inner_tx.get("message", {})
            account_keys = message.get("accountKeys", [])

            # 获取 signer（交易发起者，第一个 signer=true 的 key）
            signer = ""
            for key in account_keys:
                if isinstance(key, dict) and key.get("signer"):
                    signer = key.get("pubkey", "")
                    break
                elif isinstance(key, str):
                    # 兼容旧格式（纯字符串列表）
                    signer = key
                    break

            if not signer:
                return None

            # ===== SOL 流向计算（净值） =====
            pre_sol_balances = meta.get("preBalances", [])
            post_sol_balances = meta.get("postBalances", [])
            fee_lamports = meta.get("fee", 0)
            sol_spent = 0.0
            if pre_sol_balances and post_sol_balances and len(pre_sol_balances) > 0 and len(post_sol_balances) > 0:
                sol_spent = (pre_sol_balances[0] - post_sol_balances[0] - fee_lamports) / 1e9

            # ===== Token 余额变化（遍历所有 ATA，找 signer 拥有的） =====
            pre_token_list = meta.get("preTokenBalances", [])
            post_token_list = meta.get("postTokenBalances", [])

            # 构建 signer 在该 mint 下的 pre/post 余额
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

            if sol_spent > 0.01 and delta > 0:
                # SOL 净流出 + Token 增加 → BUY
                tx_type = "BUY"
                to_addr = to_owner
            elif sol_spent < 0 and delta < 0:
                # SOL 净流入 + Token 减少 → SELL
                tx_type = "SELL"
            elif abs(sol_spent) <= 0.01 and delta != 0:
                # SOL 变化极小 + Token 有变动 → TRANSFER
                tx_type = "TRANSFER"
            elif delta > 0:
                # 兜底：Token 增加 → BUY
                tx_type = "BUY"
                to_addr = to_owner
            elif delta < 0:
                # 兜底：Token 减少 → SELL
                tx_type = "SELL"

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
                "raw_data": str(tx_data)[:5000],
                "source": "helius_ws",
            }
        except Exception as e:
            logger.warning(f"[实时流] 解析失败: {e}")
            return None
