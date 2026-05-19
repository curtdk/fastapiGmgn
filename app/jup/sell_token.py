#!/usr/bin/env python3
"""
Jupiter Swap - 卖出代币 (100% 全部卖出)
使用方法: python sell_token.py

功能:
- 获取代币余额 (支持 Token2022)
- 卖出全部代币换成 SOL
- 签名并发送交易
"""
import os

# 设置代理 - socks5h
os.environ["http_proxy"] = "socks5h://127.0.0.1:7897"
os.environ["https_proxy"] = "socks5h://127.0.0.1:7897"

import base58
import base64
import time
import requests
from dotenv import load_dotenv

# 加载 .env 配置
load_dotenv()

JUPITER_API_KEY = os.getenv('JUPITER_API_KEY', '')
MY_PRIVATE_KEY = os.getenv('MY_PRIVATE_KEY', '')
RPC_URL = os.getenv('RPC_URL', 'https://api.mainnet-beta.solana.com')

print(f"代理设置: socks5h://127.0.0.1:7897")

# ========== 配置区域 - 直接输入 Mint 地址 ==========
SELL_TOKEN_MINT = "ZhxebfqGgPBj6vsLrz4KkTt41KxjMeJPzkiZVnWpump"  # 要卖出的代币 Mint
OUTPUT_MINT = "So11111111111111111111111111111111111111112"  # 换成的代币 (SOL)
# ===================================================

def load_wallet():
    """加载钱包"""
    from solders.keypair import Keypair
    
    if not MY_PRIVATE_KEY or MY_PRIVATE_KEY == '这里替换成我的钱包私钥Base58明文字符串':
        raise ValueError("请在 .env 文件中配置私钥 (MY_PRIVATE_KEY)")
    
    private_key_bytes = base58.b58decode(MY_PRIVATE_KEY)
    payer = Keypair.from_bytes(private_key_bytes)
    return payer

def rpc_call(method, params):
    """直接调用 RPC"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params
    }
    response = requests.post(
        RPC_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    return response.json()

def get_token_balance_via_rpc(wallet_pubkey, token_mint):
    """获取 SPL / Token2022 代币余额 - 使用直接 RPC 调用"""
    wallet_str = str(wallet_pubkey)
    
    # 使用直接 RPC 调用查询代币余额
    result = rpc_call("getTokenAccountsByOwner", [
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
                    print(f"   ✅ 找到代币余额: {amount}")
                    return int(amount), acc['pubkey']
            except Exception:
                continue
    
    return 0, None

def get_order(input_mint, output_mint, amount, taker):
    """获取订单"""
    url = "https://api.jup.ag/swap/v2/order"
    params = {
        "inputMint": input_mint,
        "outputMint": output_mint,
        "amount": amount,
        "taker": taker
    }
    headers = {}
    if JUPITER_API_KEY:
        headers['x-api-key'] = JUPITER_API_KEY
    
    response = requests.get(url, params=params, headers=headers, timeout=60)
    
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
    
    response = requests.post(url, json=payload, headers=headers, timeout=120)
    
    if response.status_code == 200:
        return {"success": True, "data": response.json()}
    else:
        return {"success": False, "error": response.text}

def main():
    print("=" * 60)
    print("Jupiter Swap - 卖出代币 (100%)")
    print("=" * 60)
    
    print(f"\n📊 交易配置:")
    print(f"  卖出代币 Mint: {SELL_TOKEN_MINT}")
    print(f"  换成 Mint: {OUTPUT_MINT}")
    
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
    
    # Step 2: 获取代币余额 (带重试机制)
    print("\n" + "-" * 40)
    print("Step 2: 获取代币余额 (通过 RPC)")
    print("-" * 40)
    
    balance = 0
    token_account = None
    
    # 💡 连续查询 6 次，最多等待 3 秒，对抗链上节点同步延迟
    for attempt in range(1, 7):
        balance, token_account = get_token_balance_via_rpc(payer.pubkey(), SELL_TOKEN_MINT)
        if balance > 0:
            break
        print(f"   ⏳ [第 {attempt} 次尝试] 余额暂为 0，正在等待链上区块同步...")
        time.sleep(0.5)
    
    print(f"   🔥 最终确认代币可卖余额: {balance}")
    if token_account:
        print(f"   代币账户: {token_account}")
    
    if balance == 0:
        print(f"\n❌ [风控拦截] 代币余额确认不存在，无法执行卖出!")
        return
    
    # Step 3: 获取订单
    print("\n" + "-" * 40)
    print("Step 3: 获取订单")
    print("-" * 40)
    
    result = get_order(SELL_TOKEN_MINT, OUTPUT_MINT, balance, str(payer.pubkey()))
    
    if not result["success"]:
        print(f"❌ 获取订单失败: {result['error']}")
        return
    
    data = result["data"]
    print(f"✅ 获取订单成功!")
    print(f"   输入数量: {data.get('inAmount', 'N/A')} (代币)")
    out_amount = int(data.get('outAmount', 0))
    print(f"   输出数量: {data.get('outAmount', 'N/A')} (SOL, {out_amount/1e9:.6f} SOL)")
    print(f"   最低输出: {data.get('otherAmountThreshold', 'N/A')}")
    print(f"   路由: {data.get('router', 'N/A')}")
    print(f"   费用: {data.get('feeBps', 'N/A')} bps")
    
    if data.get('errorMessage'):
        print(f"\n⚠️ 警告: {data.get('errorMessage')}")
    
    # 检查是否有 transaction
    if not data.get('transaction'):
        print(f"\n❌ 没有获取到交易指令")
        return
    
    # Step 4: 签名交易 (使用追加签名方式)
    print("\n" + "-" * 40)
    print("Step 4: 签名交易")
    print("-" * 40)
    
    from solders.transaction import VersionedTransaction
    
    try:
        tx_bytes = base64.b64decode(data['transaction'])
        tx = VersionedTransaction.from_bytes(tx_bytes)
        print(f"✅ 交易解码成功")
        
        # 💡 签名 VersionedTransaction
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
    
    # Step 5: 执行交易
    print("\n" + "-" * 40)
    print("Step 5: 执行交易")
    print("-" * 40)
    
    print("⚠️ 即将卖出全部代币!")
    
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