"""历史交易回填服务 - getTransactionsForAddress 一步获取签名+交易详情"""
import asyncio
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any

import httpx
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert

from app.models.models import Transaction
from app.models.trade import TradeAnalysis
from app.services.settings_service import get_int_setting, get_float_setting
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

            # 2. 使用 getTransactionsForAddress 分页获取历史交易（含详情）
            all_tx_details = await self._fetch_all_transactions_before(sync_point)
            logger.info(f"[回填] 共获取 {len(all_tx_details)} 条有效交易")

            if not all_tx_details:
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

            # 3. 批量入库（去重后直接 INSERT）
            await self._batch_insert(all_tx_details)

            # 4. 回填完成，触发指数计算
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

            # 5. 触发全量指数计算
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

    async def _fetch_all_transactions_before(self, sync_point: str) -> List[Dict[str, Any]]:
        """
        使用 getTransactionsForAddress 分页获取历史交易（含完整详情）
        通过 filters.signature.lt=sync_point 获取该签名之前的交易
        使用 paginationToken 分页，返回解析后的 trade_info 列表（从旧到新）
        """
        all_details = []
        pagination_token = None
        batch_size = 100  # transactionDetails: "full" 最大 100
        max_total = 50000
        total_fetched = 0

        while self.running and total_fetched < max_total:
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

            api_key = self._get_api_key()
            async with httpx.AsyncClient(timeout=60) as client:
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

            # 解析每笔交易
            for tx in txs:
                # 从 transaction.signatures 获取签名
                sig = tx.get("transaction", {}).get("signatures", [""])[0]
                if not sig:
                    continue

                # 跳过失败的交易
                meta = tx.get("meta", {})
                if meta.get("err"):
                    continue

                # 构建与 _extract_trade_info 兼容的结构
                tx_data = {
                    "_signature": sig,
                    "slot": tx.get("slot", 0),
                    "blockTime": tx.get("blockTime"),
                    "transaction": tx.get("transaction", {}),
                    "meta": meta,
                }

                detail = self._extract_trade_info(tx_data)
                if detail:
                    all_details.append(detail)

            total_fetched += len(txs)

            # 进度通知
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "FETCHING_HISTORY",
                    "message": f"已获取 {len(all_details)} 条有效交易...",
                    "total_trades": len(all_details),
                }
            })

            # 分页：使用 paginationToken
            pagination_token = result.get("paginationToken")
            if not pagination_token:
                break

        # 数据已从新到旧返回，反转成从旧到新
        all_details.reverse()
        if all_details:
            self.earliest_slot = all_details[0].get("slot")

        return all_details

    async def _batch_insert(self, tx_details: List[Dict[str, Any]]):
        """
        批量入库（去重后直接 INSERT）
        按 batch_size 分批处理，每批完成后更新进度
        """
        batch_size = get_int_setting(self.db, "batch_size", 100)
        interval = get_float_setting(self.db, "request_interval", 0.5)

        # 按 batch_size 分组
        batches = [tx_details[i:i + batch_size] for i in range(0, len(tx_details), batch_size)]
        total_batches = len(batches)

        from app.utils.database import SessionLocal

        for batch_idx, batch in enumerate(batches):
            if not self.running:
                break

            db = SessionLocal()
            try:
                inserted = 0
                skipped = 0
                for detail in batch:
                    sig = detail.get("sig", "")
                    if not sig:
                        continue

                    # 去重检查
                    existing = db.query(Transaction).filter(Transaction.sig == sig).first()
                    if existing:
                        skipped += 1
                        continue

                    try:
                        stmt = insert(Transaction).values(**detail)
                        db.execute(stmt)
                        inserted += 1
                    except Exception as e:
                        logger.warning(f"[回填] 入库失败 {sig}: {e}")
                        db.rollback()

                db.commit()
                self.total_fetched += inserted
                logger.info(f"[回填] 批次 {batch_idx+1}/{total_batches}: 插入 {inserted} 条，跳过 {skipped} 条")

            except Exception as e:
                logger.error(f"[回填] 批次入库异常: {e}")
                db.rollback()
            finally:
                db.close()

            # 进度通知
            progress = ((batch_idx + 1) / total_batches) * 100
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "FILLING",
                    "message": f"入库进度 {progress:.1f}% ({self.total_fetched}/{len(tx_details)})",
                    "progress": round(progress, 1),
                    "total_trades": self.total_fetched,
                }
            })

            # 批次间间隔，避免请求过快
            if interval > 0:
                await asyncio.sleep(interval)

    def _extract_trade_info(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从标准 Solana RPC 交易数据中提取关键信息（资金流向优先协议）"""
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
            if (pre_sol_balances and post_sol_balances
                    and len(pre_sol_balances) > signer_index
                    and len(post_sol_balances) > signer_index):
                sol_spent = (pre_sol_balances[signer_index] - post_sol_balances[signer_index] - fee_lamports) / 1e9

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

            if delta > 0:
                # Token 增加 → BUY（signer 花 SOL 买币）
                tx_type = "BUY"
                to_addr = to_owner
            elif delta < 0:
                # Token 减少 → SELL（signer 卖币得 SOL）
                tx_type = "SELL"
            elif abs(sol_spent) <= 0.001:
                # Token 无变化且 SOL 变化极小 → TRANSFER
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
                "raw_data": str(tx)[:20000],
                "source": "rpc_fill",
            }
        except Exception as e:
            logger.warning(f"[回填] 解析交易失败: {e}")
            return None

    async def _trigger_full_calculation(self):
        """触发全量指数计算"""
        from app.services.trade_processor import run_full_calculation
        try:
            await run_full_calculation(self.db, self.mint)
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "CALCULATION_DONE",
                    "message": "指标计算完成",
                }
            })
        except Exception as e:
            logger.error(f"[回填] 触发计算失败: {e}")

    def stop(self):
        """停止回填"""
        self.running = False
        logger.info("[回填] 收到停止信号")
