from worker.query import get_configs

configs= get_configs()

print(f"Found: {len(configs)}")

for config in configs[:3]:
    print(config.id,
          config.protocol,
          config.status
          )