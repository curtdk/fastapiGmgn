"""历史交易回填服务 - 接力模式：占位创建 + 批量填充"""
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
    """历史交易回填引擎（接力模式）"""

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
        """执行回填流程（接力模式）"""
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

            # 2. 使用 before=sync_point 分页获取历史签名
            signatures = await self._fetch_all_signatures_before(sync_point)
            logger.info(f"[回填] 共获取 {len(signatures)} 条历史签名")

            if not signatures:
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

            # 反转：从旧到新
            signatures.reverse()
            self.earliest_slot = signatures[0].get("slot")

            # 3. 创建占位记录
            placeholder_count = await self._create_placeholders(signatures)
            logger.info(f"[回填] 创建占位记录 {placeholder_count} 条")

            # 4. 批量解析并填充占位记录
            await self._batch_parse_and_fill(signatures)

            # 5. 回填完成，触发指数计算
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

            # 6. 触发全量指数计算
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

    async def _fetch_all_signatures_before(self, sync_point: str) -> List[Dict[str, Any]]:
        """
        使用 before=sync_point 分页获取历史签名
        从新到旧排列，上限 50,000 条
        """
        all_sigs = []
        before_sig = None
        batch_size = 1000
        max_total = 50000

        while self.running and len(all_sigs) < max_total:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    self.mint,
                    {
                        "limit": batch_size,
                        "commitment": "confirmed",
                        "before": sync_point,
                    }
                ]
            }
            if before_sig:
                body["params"][1]["before"] = before_sig

            api_key = self._get_api_key()
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{HELIUS_RPC_URL}/?api-key={api_key}",
                    json=body
                )
                resp.raise_for_status()
                data = resp.json()

            sigs = data.get("result", [])
            if not sigs:
                break

            all_sigs.extend(sigs)

            # 进度通知
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "FETCHING_HISTORY",
                    "message": f"已获取 {len(all_sigs)} 条历史签名...",
                    "total_trades": len(all_sigs),
                }
            })

            # 如果返回不足 batch_size，说明到头了
            if len(sigs) < batch_size:
                break

            before_sig = sigs[-1]["signature"]

        return all_sigs

    async def _create_placeholders(self, signatures: List[Dict[str, Any]]) -> int:
        """
        创建占位记录（只存 sig + slot + block_time，source='rpc_fill'）
        遍历签名（从旧到新），使用 INSERT OR IGNORE 防止重复
        """
        count = 0
        from app.utils.database import SessionLocal

        for sig_info in signatures:
            sig = sig_info["signature"]
            slot = sig_info.get("slot", 0)
            block_time_raw = sig_info.get("blockTime")
            block_time = datetime.utcfromtimestamp(block_time_raw) if block_time_raw else None

            try:
                db = SessionLocal()
                # 先检查是否已存在
                existing = db.query(Transaction).filter(Transaction.sig == sig).first()
                if existing:
                    db.close()
                    continue

                stmt = insert(Transaction).values(
                    sig=sig,
                    slot=slot,
                    block_time=block_time,
                    source="rpc_fill",
                    token_mint=self.mint,
                )
                stmt = stmt.prefix_with("OR IGNORE")
                db.execute(stmt)
                db.commit()
                count += 1
            except Exception as e:
                logger.warning(f"[回填] 创建占位失败 {sig}: {e}")
                db.rollback()
            finally:
                db.close()

        return count

    async def _batch_parse_and_fill(self, signatures: List[Dict[str, Any]]):
        """
        批量解析交易详情并填充占位记录
        使用 UPDATE 填充 source='rpc_fill' 的记录，不 INSERT 新记录
        """
        batch_size = get_int_setting(self.db, "batch_size", 100)
        concurrent = get_int_setting(self.db, "concurrent_requests", 3)
        interval = get_float_setting(self.db, "request_interval", 0.5)

        # 按 batch_size 分组
        batches = [signatures[i:i + batch_size] for i in range(0, len(signatures), batch_size)]
        total_batches = len(batches)

        semaphore = asyncio.Semaphore(concurrent)

        async def parse_batch(batch_idx: int, batch_sigs: List[Dict[str, Any]]):
            """解析一批交易并填充"""
            async with semaphore:
                sig_list = [s["signature"] for s in batch_sigs]
                tx_details = await self._parse_transactions(sig_list)

                # 填充占位记录（UPDATE）
                if tx_details:
                    await self._fill_placeholders(tx_details)

                # 更新进度
                self.total_fetched += len(tx_details)
                progress = ((batch_idx + 1) / total_batches) * 100

                await ws_manager.broadcast(self.mint, {
                    "type": "status",
                    "data": {
                        "mint": self.mint,
                        "status": "FILLING",
                        "message": f"填充进度 {progress:.1f}% ({self.total_fetched}/{len(signatures)})",
                        "progress": round(progress, 1),
                        "total_trades": self.total_fetched,
                    }
                })

        # 并发执行所有批次
        tasks = []
        for idx, batch in enumerate(batches):
            tasks.append(parse_batch(idx, batch))
            # 每发 concurrent 个任务，等一下
            if (idx + 1) % concurrent == 0:
                await asyncio.gather(*tasks[-concurrent:])
                await asyncio.sleep(interval)

        # 等待剩余任务
        if tasks:
            await asyncio.gather(*tasks)

    async def _parse_transactions(self, signatures: List[str]) -> List[Dict[str, Any]]:
        """
        批量解析交易
        使用标准 Solana RPC getTransaction（并发请求）
        """
        api_key = self._get_api_key()
        concurrent = get_int_setting(self.db, "concurrent_requests", 3)
        semaphore = asyncio.Semaphore(concurrent)

        async def fetch_tx(sig: str) -> Optional[Dict[str, Any]]:
            async with semaphore:
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(
                            f"{HELIUS_RPC_URL}/?api-key={api_key}",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
                            }
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        result = data.get("result")
                        if result is None:
                            return None
                        result["_signature"] = sig
                        return result
                except Exception as e:
                    logger.warning(f"[回填] getTransaction 失败 {sig}: {e}")
                    return None

        tasks = [fetch_tx(sig) for sig in signatures]
        results = await asyncio.gather(*tasks)

        tx_details = []
        for tx in results:
            if tx is not None:
                detail = self._extract_trade_info(tx)
                if detail:
                    tx_details.append(detail)

        return tx_details

    def _extract_trade_info(self, tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从标准 Solana RPC 交易数据中提取关键信息"""
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
                    tx_type = "BUY"
                    from_addr = signer
                    to_addr = post.get("owner", "")
                elif delta < 0:
                    tx_type = "SELL"
                    from_addr = signer
                    to_addr = ""
                else:
                    tx_type = "TRANSFER"

            # 从 inner instructions 中提取 DEX 信息
            inner_instructions = meta.get("innerInstructions", [])
            for ix_group in inner_instructions:
                for ix in ix_group.get("instructions", []):
                    program_id = ix.get("programId", "")
                    if "pump" in program_id.lower() or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in program_id:
                        dex = "pump.fun"
                        tx_type = tx_type or "SWAP"
                    elif "raydium" in program_id.lower() or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in program_id:
                        dex = "raydium"
                        tx_type = tx_type or "SWAP"
                    elif "orca" in program_id.lower():
                        dex = "orca"
                        tx_type = tx_type or "SWAP"

            # 计算消耗 SOL: preBalances[0] - postBalances[0]
            pre_sol_balances = meta.get("preBalances", [])
            post_sol_balances = meta.get("postBalances", [])
            fee = meta.get("fee", 0)
            sol_spent = 0.0
            if pre_sol_balances and post_sol_balances and len(pre_sol_balances) > 0 and len(post_sol_balances) > 0:
                sol_spent = (pre_sol_balances[0] - post_sol_balances[0]) / 1e9

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
                "sol_spent": sol_spent,
                "fee": fee / 1e9,
                "raw_data": str(tx)[:5000],
                "source": "solana_rpc",
            }
        except Exception as e:
            logger.warning(f"[回填] 解析交易失败: {e}")
            return None

    async def _fill_placeholders(self, tx_list: List[Dict[str, Any]]):
        """
        填充占位记录（UPDATE source='rpc_fill' 的记录）
        不触碰 source='helius_ws' 的记录
        """
        if not tx_list:
            return

        from app.utils.database import SessionLocal
        db = SessionLocal()
        try:
            filled_count = 0
            for tx in tx_list:
                sig = tx.get("sig")
                if not sig:
                    continue

                # 只更新 source='rpc_fill' 的占位记录
                result = db.query(Transaction).filter(
                    Transaction.sig == sig,
                    Transaction.source == "rpc_fill"
                ).update(tx, synchronize_session=False)

                if result > 0:
                    filled_count += 1

            db.commit()
            logger.info(f"[回填] 填充占位记录 {filled_count} 条")
        except Exception as e:
            logger.error(f"[回填] 填充失败: {e}")
            db.rollback()
        finally:
            db.close()

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
