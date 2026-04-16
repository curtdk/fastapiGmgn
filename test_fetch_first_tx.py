"""测试 _fetch_first_transaction 方法"""
import asyncio
import httpx
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"

# 测试用地址（你可以换成有交易记录的地址）
TEST_ADDRESS = "42BdU4C3FMymU1GN6fSDyho6EpWNyQGF2M78KNfYcxR8"
TEST_API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"


async def test_fetch_first_transaction():
    """测试获取钱包最旧一笔交易"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransactionsForAddress",
        "params": [
            TEST_ADDRESS,
            {
                "transactionDetails": "full",
                "sortOrder": "asc",
                "limit": 1,
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "filters": {"status": "succeeded"},
            },
        ],
    }
    
    logger.info(f"请求地址: {TEST_ADDRESS}")
    logger.info(f"API URL: {HELIUS_RPC_URL}/?api-key={TEST_API_KEY[:10]}...")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.info("发送请求...")
            resp = await client.post(
                f"{HELIUS_RPC_URL}/?api-key={TEST_API_KEY}",
                json=body,
            )
            logger.info(f"响应状态码: {resp.status_code}")
            logger.info(f"响应内容: {resp.text[:500]}")
            resp.raise_for_status()
            data = resp.json()
            
        logger.info(f"响应数据: {data}")
        
        if "error" in data:
            logger.error(f"Helius 错误: {data['error']}")
            return None
            
        txs = data.get("result", {}).get("data", [])
        logger.info(f"获取到交易数: {len(txs)}")
        
        if txs:
            tx = txs[0]
            logger.info(f"首笔交易 sig: {tx.get('transaction', {}).get('signatures', ['N/A'])}")
            return tx
        return None
        
    except httpx.TimeoutException as e:
        logger.error(f"请求超时: {e}")
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP 错误: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        logger.error(f"请求失败: {type(e).__name__}: {e}", exc_info=True)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_fetch_first_transaction())
