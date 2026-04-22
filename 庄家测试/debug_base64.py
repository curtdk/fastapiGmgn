"""调试脚本 - 使用 base64 encoding 获取完整数据"""
import asyncio
import json
import base64
import struct
import httpx

HELIUS_RPC_URL = "https://mainnet.helius-rpc.com"
API_KEY = "f43f1a35-863c-4c55-9a13-d00092f0ff2d"

DEALER2_SELL_SIG = "3aTX5VRKqnviFe8quEmj1LYpxdynwX7ZLfMVTvRDVdRPST3cDVvKP1oCr1dc1rtFhGWMcfFtEmPVrnLWvrb9vr6z"


def decode_base64_transaction(base64_tx: str) -> dict:
    """解码 base64 交易数据"""
    try:
        data = base64.b64decode(base64_tx)
        # 这里只做简单检查，不完全解析
        return {"length": len(data), "data_preview": data[:50].hex()}
    except Exception as e:
        return {"error": str(e)}


async def get_tx_base64(sig: str):
    """用 base64 encoding 获取交易"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            sig,
            {
                "encoding": "base64",
                "maxSupportedTransactionVersion": 0,
                "commitment": "finalized"
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            print(f"API Error: {data['error']}")
            return None
        
        return data.get("result")


async def get_tx_json(sig: str):
    """用 json 获取交易"""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            sig,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "commitment": "finalized"
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            print(f"API Error: {data['error']}")
            return None
        
        return data.get("result")


async def debug_both_encodings():
    """对比两种 encoding"""
    print("=" * 80)
    print(f"对比 base64 vs json encoding: {DEALER2_SELL_SIG[:40]}...")
    print("=" * 80)
    
    # 获取 jsonParsed 版本
    print("\n📋 获取 jsonParsed 版本...")
    tx_json = await get_tx_json(DEALER2_SELL_SIG)
    if tx_json:
        meta = tx_json.get("meta", {})
        cu = meta.get("computeUnitsConsumed", 0)
        logs = meta.get("logs", [])
        inner_ixs = meta.get("innerInstructions", [])
        print(f"   CU: {cu:,}")
        print(f"   Logs: {len(logs)} 条")
        print(f"   InnerInstructions: {len(inner_ixs)} 个组")
        
        # 检查 closeAccount
        has_close_logs = any("CloseAccount" in log for log in logs)
        has_close_ixs = any(
            ix.get("parsed", {}).get("type", "").lower() == "closeaccount"
            for ix_group in inner_ixs
            for ix in ix_group.get("instructions", [])
        )
        print(f"   closeAccount in logs: {has_close_logs}")
        print(f"   closeAccount in innerInstructions: {has_close_ixs}")
    
    # 获取 base64 版本
    print("\n📋 获取 base64 版本...")
    tx_base64 = await get_tx_base64(DEALER2_SELL_SIG)
    if tx_base64:
        meta = tx_base64.get("meta", {})
        cu = meta.get("computeUnitsConsumed", 0)
        logs = meta.get("logs", [])
        print(f"   CU: {cu:,}")
        print(f"   Logs: {len(logs)} 条")
        
        # base64 版本应该有 logs
        if logs:
            print(f"\n   关键 Logs:")
            for log in logs:
                if "Instruction" in log or "consumed" in log.lower():
                    print(f"      {log}")
        
        # 尝试检查 closeAccount
        has_close = any("CloseAccount" in log for log in logs)
        print(f"   closeAccount in logs: {has_close}")
        
        # 检查 transaction 内容
        tx_data = tx_base64.get("transaction", {})
        message = tx_data.get("message", {})
        instructions = message.get("instructions", [])
        print(f"\n   顶层指令数: {len(instructions)}")
        
        # 检查日志中是否有 Pump/Sell
        has_pump = any("Pump" in log or "pump" in log.lower() for log in logs)
        has_sell = any("Sell" in log or "sell" in log.lower() for log in logs)
        print(f"   Pump 相关: {has_pump}")
        print(f"   Sell 相关: {has_sell}")


async def use_helius_enhanced_api():
    """使用 Helius 增强 API"""
    print("\n" + "=" * 80)
    print("尝试 Helius 增强 API...")
    print("=" * 80)
    
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            DEALER2_SELL_SIG,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0,
                "commitment": "finalized",
                "fetchDetails": True  # Helius 特有参数
            }
        ]
    }
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{HELIUS_RPC_URL}/?api-key={API_KEY}", json=body)
        data = resp.json()
        
        if "error" in data:
            print(f"API Error: {data['error']}")
            return
        
        tx = data.get("result")
        if tx:
            meta = tx.get("meta", {})
            logs = meta.get("logs", [])
            inner_ixs = meta.get("innerInstructions", [])
            cu = meta.get("computeUnitsConsumed", 0)
            
            print(f"CU: {cu:,}")
            print(f"Logs: {len(logs)} 条")
            print(f"InnerInstructions: {len(inner_ixs)} 个组")
            
            if logs:
                print(f"\n关键 Logs:")
                for log in logs:
                    if "Instruction" in log or "consumed" in log.lower():
                        print(f"   {log}")


async def main():
    await debug_both_encodings()
    await use_helius_enhanced_api()


if __name__ == "__main__":
    asyncio.run(main())
