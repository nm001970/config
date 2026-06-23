from database.db import SessionLocal
from database.models import Config
def get_config_by_id(config_id):
    session= SessionLocal()
    
    row= (
        session.query(Config)
        .filter(Config.id== config_id)
        .first()
    )

    session.close()
    return row