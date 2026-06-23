import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base

USER_DATABASE_URL= os.getenv(
    "USER_DATABASE_URL",
    "sqlite:///users.db"
)

def normalize_database_url(url):
    if url.startswith("postgres://"):
        return url.replace("postgres://","postgresql://", 1)
    
    return url

USER_DATABASE_URL= normalize_database_url(USER_DATABASE_URL)
    

connect_args= {}

if USER_DATABASE_URL.startswith("sqlite"):
    connect_args= {
        "check_same_thread": False
    }

user_engine= create_engine(
    USER_DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True
)

UserSessionLocal= sessionmaker(
    autocommit= False,
    autoflush=False,
    bind=user_engine
)

def create_user_tables():
    from database.models import BotUser

    BotUser.__table__.create(
        bind=user_engine,
        checkfirst=True
    )