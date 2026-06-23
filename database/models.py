from sqlalchemy import Column, Integer, String, Float, DateTime
from datetime import datetime

from database.db import Base


class Config(Base):
    __tablename__ = "configs"

    id = Column(Integer, primary_key=True)

    config_hash = Column(
        String,
        unique=True,
        nullable=False
    )

    protocol = Column(String)
    config_text = Column(String)

    latency = Column(
        Float,
        default=0
    )

    score = Column(
        Float,
        default=0
    )

    status = Column(
        String,
        default="new"
    )

    exit_ip = Column(
        String,
        nullable=True
    )

    last_error = Column(
        String,
        nullable=True
    )

    success_count = Column(
        Integer,
        default=0
    )

    fail_count = Column(
        Integer,
        default=0
    )

    last_checked = Column(
        DateTime,
        nullable=True
    )

    # Quality test fields
    quality_status = Column(
        String,
        default="not_tested"
    )

    ping_min_ms = Column(
        Float,
        default=0
    )

    ping_avg_ms = Column(
        Float,
        default=0
    )

    download_mbps = Column(
        Float,
        default=0
    )

    upload_mbps = Column(
        Float,
        default=0
    )

    quality_score = Column(
        Float,
        default=0
    )

    last_quality_checked = Column(
        DateTime,
        nullable=True
    )

    quality_error = Column(
        String,
        nullable=True
    )

    creat_at = Column(
        DateTime,
        default=datetime.utcnow
    )


class BotUser(Base):
    __tablename__ = "bot_users"

    id = Column(Integer, primary_key=True)

    telegram_id= Column(String, unique=True, nullable=False)
    username= Column(String, nullable=True)
    first_name= Column(String, nullable=True)

    subscription_token = Column(String, unique=True, nullable=True)
    subscription_start = Column(DateTime, default= datetime.utcnow)
    subscription_end = Column(DateTime, nullable=False)

    creat_at = Column(DateTime, default=datetime.utcnow)
