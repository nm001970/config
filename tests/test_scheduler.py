from worker.scheduler import (get_configs_for_testing)

rows = get_configs_for_testing()
print(f"Selected: {len(rows)}")
for row in rows[:5]:
    print(row.id,
          row.protocol,
          row.status)