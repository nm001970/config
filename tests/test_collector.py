from worker.collector import download_configs
from worker.parser import parser_confifgs
from worker.deduplicator import remove_duplicates

raw_text= download_configs()

configs= parser_confifgs(raw_text)
print(f"Before  Sesuplicate: {len(configs)}")
unique_configs= remove_duplicates(configs)
print(f"After  Sesuplicate: {len(unique_configs)}")
print(unique_configs[0])