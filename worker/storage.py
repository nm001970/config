from sqlalchemy.exc import IntegrityError

from database.db import SessionLocal
from database.models import Config

def save_configs(configs):
    session= SessionLocal()
    inserted=0 
    for item in configs:
        row= Config(
            config_hash= item["config_hash"],
            protocol= item["protocol"],
            config_text= item["config"]
        )

        try:
            session.add(row)
            session.commit()

            inserted +=1
        except IntegrityError:
            session.rollback()
    session.close()

    return inserted