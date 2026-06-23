import socket

def get_free_port():
    sock= socket.socket()

    sock.bind(("127.0.0.1", 0))
    port= sock.getsockname()[1]
    sock.close()
    return port