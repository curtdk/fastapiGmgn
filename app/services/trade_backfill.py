"""历史交易回填服务 - 高效批量获取并入库"""
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
    """历史交易回填引擎"""

    def __init__(self, db: Session, mint: str):
        self.db = db
        self.mint = mint
        self.running = False
        self.total_fetched = 0
        self.start_slot: Optional[int] = None
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
            # 1. 记录当前 slot 作为分界点
            current_slot = await self._get_current_slot()
            self.start_slot = current_slot
            logger.info(f"[回填] 起始 slot: {current_slot}")
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "BACKFILLING",
                    "message": f"开始回填，分界 slot: {current_slot}",
                    "current_slot": current_slot,
                }
            })

            # 2. 分页获取历史签名（从新到旧）
            signatures = await self._fetch_all_signatures()
            logger.info(f"[回填] 共获取 {len(signatures)} 条签名")

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

            # 3. 批量解析交易详情
            await self._batch_parse_transactions(signatures)

            # 4. 回填完成，进入补漏阶段
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "CATCHING_UP",
                    "message": f"回填完成，共 {self.total_fetched} 条，正在补漏...",
                    "total_trades": self.total_fetched,
                    "progress": 100.0,
                }
            })

            # 5. 补漏：获取回填期间可能遗漏的数据
            await self._catch_up(current_slot)

            # 6. 切换到实时流
            await ws_manager.broadcast(self.mint, {
                "type": "status",
                "data": {
                    "mint": self.mint,
                    "status": "STREAMING",
                    "message": f"回填完成，共 {self.total_fetched} 条，开始实时监听",
                    "total_trades": self.total_fetched,
                    "progress": 100.0,
                }
            })

        except Exception as e:
            logger.error(f"[回填] 异常: {e}", exc_info=True)
            await ws_manager.broadcast(self.mint, {
                "type": "error",
                "data": {"mint": self.mint, "message": f"回填异常: {str(e)}"}
            })
        finally:
            self.running = False

    async def _get_current_slot(self) -> int:
        """获取当前 slot"""
        api_key = self._get_api_key()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{HELIUS_RPC_URL}/?api-key={api_key}",
                json={"jsonrpc": "2.0", "id": 1, "method": "getSlot"}
            )
            resp.raise_for_status()
            data = resp.json()
            return data["result"]

    async def _fetch_all_signatures(self) -> List[Dict[str, Any]]:
        """
        分页获取该 mint 的所有交易签名
        从新到旧排列
        """
        all_sigs = []
        before_sig = None
        batch_size = 1000  # getSignaturesForAddress 最大 1000

        while self.running:
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    self.mint,
                    {
                        "limit": batch_size,
                        "commitment": "confirmed",
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
                    "status": "BACKFILLING",
                    "message": f"已获取 {len(all_sigs)} 条签名...",
                    "total_trades": len(all_sigs),
                }
            })

            # 如果返回不足 batch_size，说明到头了
            if len(sigs) < batch_size:
                break

            before_sig = sigs[-1]["signature"]

        return all_sigs

    async def _batch_parse_transactions(self, signatures: List[Dict[str, Any]]):
        """
        批量解析交易详情
        每批 batch_size 个，并发 concurrent_requests 路
        """
        batch_size = get_int_setting(self.db, "batch_size", 100)
        concurrent = get_int_setting(self.db, "concurrent_requests", 3)
        interval = get_float_setting(self.db, "request_interval", 0.5)

        # 按 batch_size 分组
        batches = [signatures[i:i + batch_size] for i in range(0, len(signatures), batch_size)]
        total_batches = len(batches)

        semaphore = asyncio.Semaphore(concurrent)

        async def parse_batch(batch_idx: int, batch_sigs: List[Dict[str, Any]]):
            """解析一批交易"""
            async with semaphore:
                sig_list = [s["signature"] for s in batch_sigs]
                tx_details = await self._parse_transactions(sig_list)

                # 入库
                if tx_details:
                    await self._bulk_insert(tx_details)

                # 更新进度
                self.total_fetched += len(tx_details)
                progress = ((batch_idx + 1) / total_batches) * 100

                await ws_manager.broadcast(self.mint, {
                    "type": "status",
                    "data": {
                        "mint": self.mint,
                        "status": "BACKFILLING",
                        "message": f"回填进度 {progress:.1f}% ({self.total_fetched}/{len(signatures)})",
                        "progress": round(progress, 1),
                        "total_trades": self.total_fetched,
                    }
                })

                # 触发异步处理
                for detail in tx_details:
                    asyncio.create_task(
                        self._process_single_trade(detail)
                    )

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
                        # 附加 signature 方便后续使用
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

            # 检查是否已存在
            existing = self.db.query(Transaction).filter(Transaction.sig == sig).first()
            if existing:
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

            # 找到 signer 的 token 余额变化
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
                    tx_type = "TRANSFER"

            # 从 inner instructions 中提取 DEX 信息
            inner_instructions = meta.get("innerInstructions", [])
            for ix_group in inner_instructions:
                for ix in ix_group.get("instructions", []):
                    program_id = ix.get("programId", "")
                    # Pump.fun 相关
                    if "pump" in program_id.lower() or "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in program_id:
                        dex = "pump.fun"
                        tx_type = tx_type or "SWAP"
                    # Raydium
                    elif "raydium" in program_id.lower() or "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8" in program_id:
                        dex = "raydium"
                        tx_type = tx_type or "SWAP"
                    # Orca
                    elif "orca" in program_id.lower():
                        dex = "orca"
                        tx_type = tx_type or "SWAP"

            # 从 SOL 余额变化计算 net SOL flow
            pre_sol = meta.get("preBalances", [])
            post_sol = meta.get("postBalances", [])
            fee = meta.get("fee", 0)
            if pre_sol and post_sol and len(pre_sol) > 0 and len(post_sol) > 0:
                signer_sol_pre = pre_sol[0] / 1e9
                signer_sol_post = post_sol[0] / 1e9
                net_sol = signer_sol_post - signer_sol_pre + fee / 1e9
            else:
                net_sol = 0.0

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
                "raw_data": str(tx)[:5000],
                "source": "solana_rpc",
            }
        except Exception as e:
            logger.warning(f"[回填] 解析交易失败: {e}")
            return None

    async def _bulk_insert(self, tx_list: List[Dict[str, Any]]):
        """批量插入交易（去重）"""
        if not tx_list:
            return

        # 使用 INSERT OR IGNORE 去重
        for tx in tx_list:
            try:
                stmt = insert(Transaction).values(**tx)
                stmt = stmt.prefix_with("OR IGNORE")
                self.db.execute(stmt)
            except Exception as e:
                logger.warning(f"[回填] 插入失败 {tx.get('sig', '?')}: {e}")

        self.db.commit()
        logger.info(f"[回填] 批量入库 {len(tx_list)} 条")

    async def _catch_up(self, start_slot: int):
        """补漏：获取回填期间可能遗漏的数据"""
        # 获取从回填开始到现在的增量数据
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [self.mint, {"limit": 100, "commitment": "confirmed"}]
        }

        api_key = self._get_api_key()
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{HELIUS_RPC_URL}/?api-key={api_key}",
                json=body
            )
            resp.raise_for_status()
            data = resp.json()

        sigs = data.get("result", [])
        # 过滤出 slot >= start_slot 的新数据
        new_sigs = [s for s in sigs if s.get("slot", 0) >= start_slot]

        if new_sigs:
            new_sigs.reverse()  # 从旧到新
            await self._batch_parse_transactions(new_sigs)

    async def _process_single_trade(self, tx_detail: Dict[str, Any]):
        """异步处理单条交易（调用处理引擎）"""
        from app.services.trade_processor import process_trade
        await process_trade(self.db, tx_detail)

    def stop(self):
        """停止回填"""
        self.running = False
        logger.info("[回填] 收到停止信号")
