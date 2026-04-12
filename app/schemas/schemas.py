from pydantic import BaseModel, EmailStr
from datetime import datetime
from typing import Optional

# 用户 Schema
class UserBase(BaseModel):
    username: str
    email: EmailStr

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None

class UserResponse(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

# Token Schema
class TokenResponse(BaseModel):
    access_token: str
    token_type: str

# Transaction Schema
class TransactionBase(BaseModel):
    sig: str
    slot: Optional[int] = None
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    amount: Optional[float] = None
    token_mint: Optional[str] = None
    token_symbol: Optional[str] = None
    transaction_type: Optional[str] = None
    dex: Optional[str] = None
    pool_address: Optional[str] = None

class TransactionCreate(TransactionBase):
    pass

class TransactionResponse(TransactionBase):
    id: int
    block_time: Optional[datetime] = None
    source: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True

# Token Data Schema
class TokenBase(BaseModel):
    mint_address: str
    symbol: Optional[str] = None
    name: Optional[str] = None
    decimals: Optional[int] = None
    price: Optional[float] = None
    market_cap: Optional[float] = None
    liquidity: Optional[float] = None

class TokenCreate(TokenBase):
    pass

class TokenResponse(TokenBase):
    id: int
    volume_24h: Optional[float] = None
    is_verified: bool
    created_at: datetime
    
    class Config:
        from_attributes = True
