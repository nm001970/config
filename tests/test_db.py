from worker.db_queries import get_next_config

row= get_next_config()

print("ID:", row.id)
print("PROTOCOL:", row.protocol)
print("STATUS:", row.status)