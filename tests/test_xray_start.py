from worker.xray_tester import start_xray

process, alive= start_xray("temp_config.json")

print("ALIVE",alive)
if not alive:
    stdout, stderr= process.communicate()
    print("STDOUT:")
    print(stdout)
    print("STDERR:")
    print(stderr)

else:
    process.terminate