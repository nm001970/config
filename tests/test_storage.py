from worker.collector import download_configs
from worker.parser import parser_confifgs
from worker.deduplicator import remove_duplicates
from worker.storage import save_configs

raw_text= download_configs()
configs = parser_confifgs(raw_text)
configs= remove_duplicates(configs)
count= save_configs(configs)

print(f"Inserted: {count}")
