from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
import logging

logger = logging.getLogger(__name__)

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./gmgn.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def migrate_transactions_table():
    """自动添加 transactions 表缺失的列"""
    try:
        db = SessionLocal()
        try:
            # 检查当前表结构
            columns = db.execute(text("PRAGMA table_info(transactions)")).fetchall()
            existing_cols = {col[1] for col in columns}

            new_columns = [
                ("sol_spent", "FLOAT"),
                ("fee", "FLOAT"),
            ]

            for col_name, col_type in new_columns:
                if col_name not in existing_cols:
                    db.execute(text(f"ALTER TABLE transactions ADD COLUMN {col_name} {col_type}"))
                    db.commit()
                    logger.info(f"[迁移] 已添加 transactions.{col_name} 列")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[迁移] transactions 表迁移失败: {e}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
