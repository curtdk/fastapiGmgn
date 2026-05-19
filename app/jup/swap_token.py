#!/usr/bin/env python3
"""
Jupiter Swap - 执行交易
使用方法: python swap_token.py

功能:
- 从钱包加载私钥
- 获取兑换报价
- 签名交易
- 发送交易上链

⚠️ 注意: 此脚本会实际发送交易，请确保余额充足!
"""
import os

# 设置代理 - socks5h
os.environ["http_proxy"] = "socks5h://127.0.0.1:7897"
os.environ["https_proxy"] = "socks5h://127.0.0.1:7897"

import base58
import base64
import requests
from dotenv import load_dotenv

# 加载 .env 配置
load_dotenv()

JUPITER_API_KEY = os.getenv('JUPITER_API_KEY', '')
MY_PRIVATE_KEY = os.getenv('MY_PRIVATE_KEY', '')
RPC_URL = os.getenv('RPC_URL', 'https://api.mainnet-beta.solana.com')

print(f"代理设置: socks5h://127.0.0.1:7897")
print(f"http_proxy: {os.environ.get('http_proxy', 'N/A')}")
print(f"https_proxy: {os.environ.get('https_proxy', 'N/A')}")

def load_wallet():
    """加载钱包"""
    from solders.keypair import Keypair
    
    if not MY_PRIVATE_KEY or MY_PRIVATE_KEY == '这里替换成我的钱包私钥Base58明文字符串':
        raise ValueError("请在 .env 文件中配置私钥 (MY_PRIVATE_KEY)")
    
    private_key_bytes = base58.b58decode(MY_PRIVATE_KEY)
    payer = Keypair.from_bytes(private_key_bytes)
    return payer

def get_order(input_mint, output_mint, amount, taker):
    """获取订单 (包含交易指令)"""
    url = "https://api.jup.ag/swap/v2/order"
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "taker": taker,
        "slippageBps": 200,          # 滑点 2%
        
        # 💡 控费核心参数（三选一，千万不要同时乱填）：
        
        # 选项 A：设置优先费级别 (字符串)
        # 可选值: "Min" (极低), "Low" (低), "Medium" (中,默认), "High" (高), "VeryHigh" (极高)
        # 如果你想省钱，可以直接用 "Low" 或者 "Min"
        "priorityLevel": "Low", 
        
        # 选项 B：直接锁死最高愿意支付的微 lamports 单价 (整数)
        # 比如强制锁死每计算单元只给 10,000 microLamports
        # "computeUnitPriceMicroLamports": 10000,
        
        # 选项 C：直接锁死这笔交易总共最多给多少优先费 (单位为 Lamports 整数)
        # 比如强制这笔交易总共只给 0.0005 SOL 的优先费：0.0005 * 10^9 = 500000
        # "maxPriorityFeeLamports": 500000
    }
    headers = {}
    if JUPITER_API_KEY:
        headers['x-api-key'] = JUPITER_API_KEY
    
    print(f"请求 URL: {url}")
    print(f"参数: {params}")
    
    response = requests.get(url, params=params, headers=headers, timeout=30)
    
    print(f"响应状态码: {response.status_code}")
    
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    else:
        return {"success": False, "error": response.text}

def execute_swap(signed_transaction_base64, request_id):
    """通过 Jupiter 执行已签名的交易"""
    url = "https://api.jup.ag/swap/v2/execute"
    payload = {
        "signedTransaction": signed_transaction_base64,
        "requestId": request_id
    }
    headers = {"Content-Type": "application/json"}
    if JUPITER_API_KEY:
        headers['x-api-key'] = JUPITER_API_KEY
    
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    else:
        return {"success": False, "error": response.text}

def check_balance(wallet_address):
    """检查钱包余额"""
    from solana.rpc.api import Client
    
    client = Client(RPC_URL)
    try:
        balance = client.get_balance(wallet_address)
        return balance.value
    except Exception as e:
        print(f"检查余额失败: {e}")
        return 0

def main():
    print("=" * 60)
    print("Jupiter Swap - 执行交易 (SOCKS5H 代理)")
    print("=" * 60)
    
    # ========== 配置区域 - 直接输入 Mint 地址 ==========
    INPUT_MINT = "So11111111111111111111111111111111111111112"  # 输入代币 Mint (SOL)
    OUTPUT_MINT = "ZhxebfqGgPBj6vsLrz4KkTt41KxjMeJPzkiZVnWpump"  # 输出代币 Mint (USDC)
    AMOUNT = 10000000  # 数量 (lamports), 注意: 根据输入代币的小数位
    # ===================================================
    
    print(f"\n📊 交易配置:")
    print(f"  输入 Mint: {INPUT_MINT}")
    print(f"  输出 Mint: {OUTPUT_MINT}")
    print(f"  数量: {AMOUNT} (lamports)")
    
    # Step 1: 加载钱包
    print("\n" + "-" * 40)
    print("Step 1: 加载钱包")
    print("-" * 40)
    
    try:
        payer = load_wallet()
        print(f"✅ 钱包加载成功!")
        print(f"   地址: {payer.pubkey()}")
    except Exception as e:
        print(f"❌ 钱包加载失败: {e}")
        return
    
    # 检查余额
    balance = check_balance(payer.pubkey())
    print(f"   余额: {balance} lamports ({balance/1e9:.6f} SOL)")
    
    if balance < AMOUNT:
        print(f"\n⚠️ 余额不足! 需要 {AMOUNT} lamports, 但只有 {balance}")
    
    # Step 2: 获取订单
    print("\n" + "-" * 40)
    print("Step 2: 获取订单")
    print("-" * 40)
    
    result = get_order(INPUT_MINT, OUTPUT_MINT, AMOUNT, str(payer.pubkey()))
    
    if not result["success"]:
        print(f"❌ 获取订单失败: {result['error']}")
        return
    
    data = result["data"]
    print(f"✅ 获取订单成功!")
    print(f"   输入数量: {data.get('inAmount', 'N/A')}")
    print(f"   输出数量: {data.get('outAmount', 'N/A')}")
    print(f"   最低输出: {data.get('otherAmountThreshold', 'N/A')}")
    print(f"   路由: {data.get('router', 'N/A')}")
    print(f"   费用: {data.get('feeBps', 'N/A')} bps")
    
    if data.get('errorMessage'):
        print(f"\n⚠️ 警告: {data.get('errorMessage')}")
    
    # 检查是否有 transaction
    if not data.get('transaction'):
        print(f"\n❌ 没有获取到交易指令")
        return
    
    # Step 3: 签名交易
    print("\n" + "-" * 40)
    print("Step 3: 签名交易")
    print("-" * 40)
    
    from solders.transaction import VersionedTransaction
    
    try:
        # 解码交易 - 使用 from_bytes 而不是 deserialize
        tx_bytes = base64.b64decode(data['transaction'])
        tx = VersionedTransaction.from_bytes(tx_bytes)
        print(f"✅ 交易解码成功")
        
        # 签名
        signed_tx = VersionedTransaction(tx.message, [payer])
        signed_tx_bytes = bytes(signed_tx)
        signed_tx_base64 = base64.b64encode(signed_tx_bytes).decode('utf-8')
        
        print(f"✅ 交易签名成功")
        print(f"   签名后大小: {len(signed_tx_base64)} 字符")
        
    except Exception as e:
        print(f"❌ 签名失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Step 4: 执行交易
    print("\n" + "-" * 40)
    print("Step 4: 执行交易")
    print("-" * 40)
    
    print("⚠️ 即将发送交易到区块链!")
    
    result = execute_swap(signed_tx_base64, data.get('requestId', ''))
    
    if result["success"]:
        result_data = result["data"]
        
        print(f"\n✅ 交易执行完成!")
        print(f"   状态: {result_data.get('status', 'N/A')}")
        print(f"   签名: {result_data.get('signature', 'N/A')}")
        
        if result_data.get('signature'):
            print(f"\n🔗 查看交易: https://solscan.io/tx/{result_data['signature']}")
    else:
        print(f"\n❌ 执行失败: {result['error']}")

if __name__ == "__main__":
    main()