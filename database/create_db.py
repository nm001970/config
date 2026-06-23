from database.db import Base, engine
from database import models
print(Base.metadata.tables.keys())
Base.metadata.create_all(bind= engine)
print("Database  Created ")
