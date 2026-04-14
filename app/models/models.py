from sqlalchemy import Column, Integer, String, DateTime, Text, Float, Boolean
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
    raw_data = Column(Text)
    source = Column(String)  # helius_ws, solana_rpc, rpc_fill
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
