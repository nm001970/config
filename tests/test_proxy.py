from worker.xray_tester import start_xray
from worker.proxy_checker import test_proxy
from worker.port_manager import get_free_port

socks_port= get_free_port()

process, alive= start_xray(
    "temp_config.json"
)

print(
    "XRAY",
    alive
)

if alive:
    result= test_proxy(socks_port)


    print(result)
    if process.poll() is None:
        process.terminate