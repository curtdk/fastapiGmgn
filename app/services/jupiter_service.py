"""
Jupiter Swap Service - 执行代币买卖
"""
import os
import time
import base58
import base64
import logging
from typing import Optional, Dict, Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 加载 .env 配置
load_dotenv()

JUPITER_API_KEY = os.getenv('JUPITER_API_KEY', '')
MY_PRIVATE_KEY = os.getenv('MY_PRIVATE_KEY', '')
RPC_URL = os.getenv('RPC_URL', 'https://api.mainnet-beta.solana.com')

# 设置代理
PROXY_HTTP = os.getenv('http_proxy', '')
PROXY_HTTPS = os.getenv('https_proxy', '')


class JupiterService:
    """Jupiter 交易服务"""
    
    # SOL Mint 地址
    SOL_MINT = "So11111111111111111111111111111111111111112"
    
    def __init__(self):
        self.payer = None
        self._initialized = False
        self._busy = False  # 交易中锁
        self._priority = self._load_priority()  # 从数据库读取
    
    def _load_priority(self):
        """从数据库加载 priority 设置"""
        try:
            from app.utils.database import get_db
            from app.services.settings_service import get_setting
            db = next(get_db())
            return get_setting(db, "jupiter_priority") or "Medium"
        except Exception:
            return "Medium"
    
    def _ensure_not_busy(self):
        """检查是否正在交易中"""
        if self._busy:
            raise ValueError("上一笔交易尚未完成，请稍候")
        self._busy = True
    
    def _release_busy(self):
        """释放交易锁"""
        self._busy = False
    
    def _ensure_initialized(self):
        """确保钱包已初始化"""
        if self._initialized:
            return True
        
        if not MY_PRIVATE_KEY or MY_PRIVATE_KEY == '这里替换成我的钱包私钥Base58明文字符串':
            raise ValueError("请在 .env 文件中配置私钥 (MY_PRIVATE_KEY)")
        
        try:
            from solders.keypair import Keypair
            private_key_bytes = base58.b58decode(MY_PRIVATE_KEY)
            self.payer = Keypair.from_bytes(private_key_bytes)
            self._initialized = True
            logger.info(f"钱包加载成功: {self.payer.pubkey()}")
            return True
        except Exception as e:
            logger.error(f"钱包加载失败: {e}")
            raise
    
    @property
    def wallet_address(self) -> str:
        """获取钱包地址"""
        self._ensure_initialized()
        return str(self.payer.pubkey())
    
    def _rpc_call(self, method: str, params: list) -> Dict[str, Any]:
        """直接调用 RPC"""
        import requests
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        
        proxies = {}
        if PROXY_HTTP:
            proxies['http'] = PROXY_HTTP
        if PROXY_HTTPS:
            proxies['https'] = PROXY_HTTPS
        
        response = requests.post(
            RPC_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            proxies=proxies if proxies else None,
            timeout=30
        )
        return response.json()
    
    def _get_token_balance_via_rpc(self, token_mint: str) -> tuple:
        """获取 SPL / Token2022 代币余额"""
        self._ensure_initialized()
        wallet_str = self.wallet_address
        
        # 查询代币余额
        result = self._rpc_call("getTokenAccountsByOwner", [
            wallet_str,
            {"programId": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"},
            {"encoding": "jsonParsed"}
        ])
        
        if 'result' in result:
            accounts = result['result']['value']
            for acc in accounts:
                try:
                    info = acc['account']['data']['parsed']['info']
                    mint = info['mint']
                    if mint == token_mint:
                        amount = info['tokenAmount']['amount']
                        decimals = info['tokenAmount'].get('decimals', 0)
                        return int(amount), acc['pubkey'], decimals
                except Exception:
                    continue
        
        return 0, None, 0
    
    def get_sol_balance(self) -> float:
        """获取 SOL 余额（返回 SOL 单位）"""
        self._ensure_initialized()
        try:
            result = self._rpc_call("getBalance", [self.wallet_address])
            if 'result' in result:
                return result['result']['value'] / 1e9  # lamports to SOL
        except Exception as e:
            logger.error(f"获取 SOL 余额失败: {e}")
        return 0.0
    
    def get_token_balance(self, mint: str) -> Dict[str, Any]:
        """获取代币余额信息"""
        balance, token_account, decimals = self._get_token_balance_via_rpc(mint)
        return {
            "balance": balance,
            "balance_sol": balance / (10 ** decimals) if decimals > 0 else balance,
            "token_account": token_account,
            "decimals": decimals
        }
    
    def _get_order(self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 500, priority: str = "Medium") -> Dict[str, Any]:
        """获取订单"""
        import requests
        
        url = "https://api.jup.ag/swap/v2/order"
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": amount,
            "taker": self.wallet_address,
            "slippageBps": slippage_bps,
            "priorityLevel": priority,
        }
        headers = {}
        if JUPITER_API_KEY:
            headers['x-api-key'] = JUPITER_API_KEY
        
        proxies = {}
        if PROXY_HTTP:
            proxies['http'] = PROXY_HTTP
        if PROXY_HTTPS:
            proxies['https'] = PROXY_HTTPS
        
        try:
            response = requests.get(
                url, 
                params=params, 
                headers=headers, 
                proxies=proxies if proxies else None,
                timeout=60
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text, "status": response.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _execute_swap(self, signed_transaction_base64: str, request_id: str) -> Dict[str, Any]:
        """通过 Jupiter 执行已签名的交易"""
        import requests
        
        url = "https://api.jup.ag/swap/v2/execute"
        payload = {
            "signedTransaction": signed_transaction_base64,
            "requestId": request_id
        }
        headers = {"Content-Type": "application/json"}
        if JUPITER_API_KEY:
            headers['x-api-key'] = JUPITER_API_KEY
        
        proxies = {}
        if PROXY_HTTP:
            proxies['http'] = PROXY_HTTP
        if PROXY_HTTPS:
            proxies['https'] = PROXY_HTTPS
        
        try:
            response = requests.post(
                url, 
                json=payload, 
                headers=headers,
                proxies=proxies if proxies else None,
                timeout=120
            )
            
            if response.status_code == 200:
                return {"success": True, "data": response.json()}
            else:
                return {"success": False, "error": response.text, "status": response.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _sign_and_execute(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """签名并执行交易"""
        from solders.transaction import VersionedTransaction
        
        try:
            # 解码交易
            tx_bytes = base64.b64decode(order_data['transaction'])
            tx = VersionedTransaction.from_bytes(tx_bytes)
            
            # 签名
            signed_tx = VersionedTransaction(tx.message, [self.payer])
            signed_tx_bytes = bytes(signed_tx)
            signed_tx_base64 = base64.b64encode(signed_tx_bytes).decode('utf-8')
            
            # 执行
            return self._execute_swap(signed_tx_base64, order_data.get('requestId', ''))
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def buy(self, mint: str, sol_amount: float, slippage_bps: int = 500) -> Dict[str, Any]:
        """
        买入代币
        
        Args:
            mint: 代币 Mint 地址
            sol_amount: 购买的 SOL 数量
            slippage_bps: 滑点（默认 5% = 500 bps）
        
        Returns:
            交易结果，包含 success, signature, error 等
        """
        self._ensure_not_busy()
        try:
            self._ensure_initialized()
            
            logger.info(f"开始买入操作: mint={mint}, sol_amount={sol_amount}")
            
            # 检查 SOL 余额
            sol_balance = self.get_sol_balance()
            logger.info(f"SOL 余额: {sol_balance}")
            
            if sol_balance < sol_amount:
                return {
                    "success": False,
                    "error": f"SOL 余额不足: 需要 {sol_amount} SOL, 当前余额 {sol_balance} SOL"
                }
            
            # 转换为 lamports (SOL 有 9 位小数)
            amount_lamports = int(sol_amount * 1e9)
            
            # 获取订单（使用缓存的 priority）
            order_result = self._get_order(
                input_mint=self.SOL_MINT,
                output_mint=mint,
                amount=amount_lamports,
                slippage_bps=slippage_bps,
                priority=self._priority
            )
            
            if not order_result["success"]:
                return {
                    "success": False,
                    "error": f"获取订单失败: {order_result.get('error', '未知错误')}"
                }
            
            data = order_result["data"]
            logger.info(f"订单获取成功: inAmount={data.get('inAmount')}, outAmount={data.get('outAmount')}, priority={self._priority}")
            
            if data.get('errorMessage'):
                logger.warning(f"订单警告: {data.get('errorMessage')}")
            
            if not data.get('transaction'):
                return {
                    "success": False,
                    "error": "没有获取到交易指令"
                }
            
            # 签名并执行
            execute_result = self._sign_and_execute(data)
            
            if execute_result["success"]:
                result_data = execute_result["data"]
                signature = result_data.get('signature', '')
                
                return {
                    "success": True,
                    "type": "BUY",
                    "signature": signature,
                    "status": result_data.get('status', 'unknown'),
                    "in_amount": data.get('inAmount'),
                    "out_amount": data.get('outAmount'),
                    "tx_url": f"https://solscan.io/tx/{signature}" if signature else None
                }
            else:
                return {
                    "success": False,
                    "error": f"执行失败: {execute_result.get('error', '未知错误')}"
                }
        finally:
            self._release_busy()
    
    def sell(self, mint: str, percent: int = 100, slippage_bps: int = 500) -> Dict[str, Any]:
        """
        卖出代币
        
        Args:
            mint: 代币 Mint 地址
            percent: 卖出百分比 (1-100)，默认 100%
            slippage_bps: 滑点（默认 5% = 500 bps）
        
        Returns:
            交易结果，包含 success, signature, error 等
        """
        self._ensure_not_busy()
        try:
            self._ensure_initialized()
            
            logger.info(f"开始卖出操作: mint={mint}, percent={percent}%")
            
            # 获取代币余额
            token_info = self.get_token_balance(mint)
            total_balance = token_info["balance"]
            
            if total_balance == 0:
                return {
                    "success": False,
                    "error": "代币余额为 0，无法卖出"
                }
            
            logger.info(f"代币余额: {token_info['balance_sol']}")
            
            # 计算卖出数量
            sell_amount = int(total_balance * percent / 100)
            
            if sell_amount == 0:
                return {
                    "success": False,
                    "error": "计算卖出数量为 0，请检查余额"
                }
            
            # 获取订单 (卖出代币换 SOL，使用缓存的 priority)
            order_result = self._get_order(
                input_mint=mint,
                output_mint=self.SOL_MINT,
                amount=sell_amount,
                slippage_bps=slippage_bps,
                priority=self._priority
            )
            
            if not order_result["success"]:
                return {
                    "success": False,
                    "error": f"获取订单失败: {order_result.get('error', '未知错误')}"
                }
            
            data = order_result["data"]
            out_amount = int(data.get('outAmount', 0))
            logger.info(f"订单获取成功: inAmount={data.get('inAmount')}, outAmount={data.get('outAmount')} ({out_amount/1e9:.6f} SOL), priority={self._priority}")
            
            if data.get('errorMessage'):
                logger.warning(f"订单警告: {data.get('errorMessage')}")
            
            if not data.get('transaction'):
                return {
                    "success": False,
                    "error": "没有获取到交易指令"
                }
            
            # 签名并执行
            execute_result = self._sign_and_execute(data)
            
            if execute_result["success"]:
                result_data = execute_result["data"]
                signature = result_data.get('signature', '')
                
                return {
                    "success": True,
                    "type": "SELL",
                    "signature": signature,
                    "status": result_data.get('status', 'unknown'),
                    "in_amount": data.get('inAmount'),
                    "out_amount": data.get('outAmount'),
                    "out_amount_sol": out_amount / 1e9,
                    "tx_url": f"https://solscan.io/tx/{signature}" if signature else None
                }
            else:
                return {
                    "success": False,
                    "error": f"执行失败: {execute_result.get('error', '未知错误')}"
                }
        finally:
            self._release_busy()


# 单例实例
_jupiter_service: Optional[JupiterService] = None


def get_jupiter_service() -> JupiterService:
    """获取 Jupiter 服务单例"""
    global _jupiter_service
    if _jupiter_service is None:
        _jupiter_service = JupiterService()
    return _jupiter_service
