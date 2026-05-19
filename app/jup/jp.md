# 核心依赖
requests>=2.34.2
python-dotenv>=1.2.2
base58>=2.1.1

# Solana 区块链交互
solders>=0.27.1
solana>=0.36.12
spl-token>=0.5.0

# SOCKS 代理支持 (重要!)
socksio>=1.0.0
pysocks>=1.7.1

# ========== 环境变量配置 (.env) ==========
# JUPITER_API_KEY=你的Jupiter API密钥
# MY_PRIVATE_KEY=钱包私钥 (Base58)
# RPC_URL=RPC节点地址
# http_proxy=socks5h://127.0.0.1:7897 (可选)
# https_proxy=socks5h://127.0.0.1:7897 (可选)