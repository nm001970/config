from worker.config_writer import save_temp_config, delete_temp_config

sample={
    "test" : True
}

path = save_temp_config(sample,56034)
print(path)

delete_temp_config(path)
print("DONE")