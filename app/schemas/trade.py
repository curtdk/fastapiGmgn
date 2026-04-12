"""交易相关 Pydantic Schema"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class SettingBase(BaseModel):
    key: str
    value: str
    description: Optional[str] = None


class SettingResponse(SettingBase):
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SettingUpdate(BaseModel):
    value: str


class TradeAnalysisResponse(BaseModel):
    sig: str
    net_sol_flow: float = 0.0
    net_token_flow: float = 0.0
    price_per_token: float = 0.0
    wallet_tag: Optional[str] = None
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TradeData(BaseModel):
    """单条交易数据（含分析结果）"""
    sig: str
    slot: Optional[int] = None
    block_time: Optional[datetime] = None
    from_address: Optional[str] = None
    to_address: Optional[str] = None
    amount: Optional[float] = None
    token_mint: Optional[str] = None
    token_symbol: Optional[str] = None
    transaction_type: Optional[str] = None
    dex: Optional[str] = None
    pool_address: Optional[str] = None
    # 分析结果
    net_sol_flow: float = 0.0
    net_token_flow: float = 0.0
    price_per_token: float = 0.0
    wallet_tag: Optional[str] = None


class TradeListResponse(BaseModel):
    """交易列表响应"""
    trades: List[TradeData]
    total: int
    has_more: bool


class MonitorStatus(BaseModel):
    """监听状态"""
    mint: str
    status: str  # IDLE, BACKFILLING, CATCHING_UP, STREAMING, STOPPED
    progress: float = 0.0  # 回填进度 0-100
    total_trades: int = 0
    current_slot: Optional[int] = None
    message: str = ""


class FourMetrics(BaseModel):
    """四个核心指标"""
    current_bet: float = 0.0       # 本轮下注（当前净投入 SOL）
    current_cost: float = 0.0      # 本轮成本（平均成本）
    realized_profit: float = 0.0   # 已落袋（已实现利润 SOL）
    trade_count: int = 0           # 总交易笔数


class WsMessage(BaseModel):
    """WebSocket 推送消息"""
    type: str  # "status", "trade", "metrics", "error"
    data: dict
