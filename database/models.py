from sqlalchemy import Column, Integer, String, Float, DateTime, UniqueConstraint
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
    chat_id= Column(String, nullable=True)

    username= Column(String, nullable=True)
    first_name= Column(String, nullable=True)
    last_name=Column(String, nullable=True)
    language_code= Column(String, nullable=True)

    subscription_token = Column(String, unique=True, nullable=True)
    subscription_start = Column(DateTime, default= datetime.utcnow)
    subscription_end = Column(DateTime, nullable=False)

    last_seen_at= Column(DateTime, nullable=True)

    creat_at = Column(DateTime, default=datetime.utcnow)


class ConfigOperatorResult(Base):
    __tablename__ = "config_operator_results"

    id = Column(Integer, primary_key=True)

    config_id = Column(Integer, nullable=False, index=True)
    operator = Column(String, nullable=False, index=True)

    status = Column(String, default="not_tested")

    latency = Column(Float, default=0)

    ping_min_ms = Column(Float, nullable=True)
    ping_avg_ms = Column(Float, nullable=True)

    download_mbps = Column(Float, nullable=True)
    upload_mbps = Column(Float, nullable=True)

    quality_score = Column(Float, default=0)
    quality_status = Column(String, default="not_tested")

    success_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)

    last_checked = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)

    test_source = Column(String, default="local_operator_scan")

    creat_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "config_id",
            "operator",
            name="uq_config_operator_result"
        ),
    )

