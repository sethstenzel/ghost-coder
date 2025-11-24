import socket

def get_random_available_port() -> int:
    """
    Creates a temporary socket, binds it to port 0 to let the OS
    assign a free port, and returns the assigned port number.
    For most OS, binding to 0 will return an available port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))  # Bind to an available port (OS assigns 0)
        port = int(s.getsockname()[1])
        s.close()
        return port
