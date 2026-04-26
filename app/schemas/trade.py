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
    sol_spent: Optional[float] = None  # 消耗 SOL
    fee: Optional[float] = None  # 交易费
    source: Optional[str] = None  # helius_ws, solana_rpc, rpc_fill
    # 分析结果
    net_sol_flow: float = 0.0
    net_token_flow: float = 0.0
    price_per_token: float = 0.0
    wallet_tag: Optional[str] = None
    # 风险相关
    risk_score: Optional[int] = 0
    risk_verdict: Optional[str] = None
    risk_indicators: Optional[str] = None  # JSON 字符串
    # Compute Unit
    priority_fee: Optional[float] = None
    cu_consumed: Optional[int] = 0
    cu_limit: Optional[int] = 200000
    cu_price: Optional[int] = 0
    # 指令统计
    instructions_count: Optional[int] = 0
    inner_instructions_count: Optional[int] = 0
    total_instruction_count: Optional[int] = 0
    account_keys_count: Optional[int] = 0
    uses_lookup_table: Optional[bool] = False
    signers_count: Optional[int] = 0
    # 指令详情（JSON 字符串）
    main_instructions: Optional[str] = None
    inner_instructions: Optional[str] = None
    program_ids: Optional[str] = None


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
