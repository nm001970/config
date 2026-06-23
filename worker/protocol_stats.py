from collections import Counter

from database.db import SessionLocal
from database.models import Config


session = SessionLocal()

rows = session.query(Config).all()

counter = Counter()

for row in rows:
    counter[row.protocol] += 1

session.close()

total = len(rows)

print(f"TOTAL = {total}")

for protocol, count in sorted(
    counter.items()
):

    print(
        protocol,
        count,
        round(
            count / total * 100,
            2
        )
    )