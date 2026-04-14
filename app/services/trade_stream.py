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
        """处理收到的 WebSocket 消息"""
        try:
            data = json.loads(msg)

            # 跳过订阅确认和心跳
            if "result" in data or data.get("method") == "subscriptionNotification":
                return

            # 解析交易数据 — 标准 Solana WS 格式
            # params.result.value 包含完整交易数据
            params = data.get("params", {})
            tx_data = params.get("result", {}).get("value", {})
            if not tx_data:
                return

            # 附加 signature（从交易本身提取）
            sigs = tx_data.get("transaction", {}).get("signatures", [])
            if sigs:
                tx_data["_signature"] = sigs[0]
            else:
                return

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
        """从标准 Solana RPC 交易数据中提取交易信息"""
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
            message = tx_data.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            # 获取 signer（交易发起者）
            signer = ""
            if account_keys:
                first_key = account_keys[0]
                signer = first_key.get("pubkey", "") if isinstance(first_key, dict) else first_key

            # 从 pre/postTokenBalances 计算 token 变化
            pre_balances = {b["mint"]: b for b in meta.get("preTokenBalances", [])}
            post_balances = {b["mint"]: b for b in meta.get("postTokenBalances", [])}

            pre = pre_balances.get(self.mint)
            post = post_balances.get(self.mint)

            amount = 0.0
            from_addr = signer
            to_addr = ""
            tx_type = "TRANSFER"
            dex = ""
            pool_address = ""
            token_symbol = ""

            if pre and post:
                pre_amt = pre["uiTokenAmount"].get("uiAmount", 0) or 0
                post_amt = post["uiTokenAmount"].get("uiAmount", 0) or 0
                delta = post_amt - pre_amt
                amount = abs(delta)

                if delta > 0:
                    # signer 的 token 增加 → BUY
                    tx_type = "BUY"
                    from_addr = signer
                    to_addr = post.get("owner", "")
                elif delta < 0:
                    # signer 的 token 减少 → SELL
                    tx_type = "SELL"
                    from_addr = signer
                    to_addr = ""
                else:
                    # 无变化，可能是 transfer
                    tx_type = "TRANSFER"

            # 从 inner instructions 中提取 DEX 信息
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
                "token_symbol": token_symbol,
                "transaction_type": tx_type,
                "dex": dex,
                "pool_address": pool_address,
                "raw_data": str(tx_data)[:5000],
                "source": "helius_ws",
            }
        except Exception as e:
            logger.warning(f"[实时流] 解析失败: {e}")
            return None
