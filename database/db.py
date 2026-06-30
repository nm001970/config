import  os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL= os.getenv(
    "CONFIG_DATABASE_URL",
    "sqlite:///configs.db"
)

engine= create_engine(
    DATABASE_URL, 
    echo=False
)

SessionLocal= sessionmaker(
    autocommit= False,
    autoflush= False,
    bind= engine
)

Base = declarative_base()
