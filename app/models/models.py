from sqlalchemy import Column, Integer, String, DateTime, Text, Float, Boolean, ForeignKey
from app.utils.database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    sig = Column(String, unique=True, index=True, nullable=False)
    slot = Column(Integer, index=True)
    block_time = Column(DateTime, index=True)
    from_address = Column(String, index=True)
    to_address = Column(String, index=True)
    amount = Column(Float)
    token_mint = Column(String, index=True)
    token_symbol = Column(String)
    transaction_type = Column(String)  # SWAP, TRANSFER, MINT, etc
    dex = Column(String)  # raydium, orca, etc
    pool_address = Column(String, index=True)
    sol_spent = Column(Float)  # 消耗 SOL
    fee = Column(Float)  # 交易费
    jito_tip = Column(Float, default=0.0)  # Jito 小费
    raw_data = Column(Text)
    source = Column(String)  # helius_ws, solana_rpc, rpc_fill
    # 新增字段
    priority_fee = Column(Float, default=0.0)  # Priority Fee (SOL)
    cu_consumed = Column(Integer, default=0)  # 消耗的 Compute Units
    cu_limit = Column(Integer, default=200000)  # CU Limit
    cu_price = Column(Integer, default=0)  # CU Price (microlamports)
    instructions_count = Column(Integer, default=0)  # 主指令数
    inner_instructions_count = Column(Integer, default=0)  # 内部指令数
    total_instruction_count = Column(Integer, default=0)  # 总指令数
    account_keys_count = Column(Integer, default=0)  # 账户数
    uses_lookup_table = Column(Boolean, default=False)  # 是否使用 ALT
    signers_count = Column(Integer, default=0)  # 签名者数
    main_instructions = Column(Text)  # JSON: 主指令详情
    inner_instructions = Column(Text)  # JSON: 内部指令详情
    program_ids = Column(Text)  # JSON: 程序 ID 列表
    risk_score = Column(Integer, default=0)  # 风险评分
    risk_verdict = Column(String)  # 风险判定
    risk_indicators = Column(Text)  # JSON: 触发指标
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Token(Base):
    __tablename__ = "tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    mint_address = Column(String, unique=True, index=True, nullable=False)
    symbol = Column(String, index=True)
    name = Column(String)
    decimals = Column(Integer)
    price = Column(Float)
    market_cap = Column(Float)
    liquidity = Column(Float)
    volume_24h = Column(Float)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TradingUser(Base):
    """交易用户表"""
    __tablename__ = "trading_users"
    
    user_address = Column(String, primary_key=True, index=True)  # 用户地址（主键）
    user_status = Column(String, index=True)  # 用户状态（普通用户/庄家/不确定）
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UserCondition(Base):
    """交易用户满足条件表"""
    __tablename__ = "user_conditions"
    
    id = Column(Integer, primary_key=True, index=True)  # 自增主键
    user_address = Column(String, ForeignKey("trading_users.user_address"), index=True)  # 用户地址（外键）
    condition_id = Column(String, index=True)  # 条件ID
    condition_score = Column(Float)  # 条件分值
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
