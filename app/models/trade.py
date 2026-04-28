"""交易相关数据库模型"""
from sqlalchemy import Column, Integer, String, DateTime, Text, Float, Boolean
from app.utils.database import Base
from datetime import datetime


class Setting(Base):
    """系统设置表"""
    __tablename__ = "settings"

    key = Column(String, primary_key=True, index=True)
    value = Column(Text, nullable=False)
    description = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# class TradeAnalysis(Base):
#     """交易分析结果表"""
#     __tablename__ = "trade_analysis"

#     id = Column(Integer, primary_key=True, index=True)
#     sig = Column(String, unique=True, index=True, nullable=False)
#     net_sol_flow = Column(Float, default=0.0)       # SOL净流入
#     net_token_flow = Column(Float, default=0.0)      # Token净流入
#     price_per_token = Column(Float, default=0.0)     # 单价(SOL)
#     wallet_tag = Column(String)                       # 钱包标记
#     processed_at = Column(DateTime, default=datetime.utcnow)
