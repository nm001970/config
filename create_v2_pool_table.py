from database.db import engine
from database.models import ConfigPool


def main():
    ConfigPool.__table__.create(
        bind=engine,
        checkfirst=True,
    )

    print("V2 CONFIG POOL TABLE READY")


if __name__ == "__main__":
    main()