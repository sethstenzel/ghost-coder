import zmq
import multiprocessing
import time
import socket


def find_free_port():
    """Find a random available port on the system."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))  # Bind to port 0 = OS assigns a free port
        s.listen(1)
        port = s.getsockname()[1]  # Get the assigned port number
    return port


def server_process(port):
    """Server process that receives requests and sends replies."""
    context = zmq.Context()
    sock = context.socket(zmq.REP)  # Reply socket
    sock.bind(f"tcp://127.0.0.1:{port}")

    print(f"[Server] Started and listening on tcp://127.0.0.1:{port}")

    message_count = 0
    while message_count < 5:  # Process 5 messages then exit
        # Wait for request from client
        message = sock.recv_string()
        print(f"[Server] Received: {message}")

        # Do some work (simulate processing)
        time.sleep(0.5)

        # Send reply back to client
        reply = f"Server processed: {message} (count: {message_count})"
        sock.send_string(reply)
        print(f"[Server] Sent: {reply}")

        message_count += 1

    print("[Server] Shutting down")
    sock.close()
    context.term()


def client_process(port):
    """Client process that sends requests and receives replies."""
    context = zmq.Context()
    sock = context.socket(zmq.REQ)  # Request socket

    # Give server time to start
    time.sleep(1)

    sock.connect(f"tcp://127.0.0.1:{port}")
    print(f"[Client] Connected to tcp://127.0.0.1:{port}")

    for i in range(5):
        # Send request to server
        message = f"Hello from client #{i}"
        print(f"[Client] Sending: {message}")
        sock.send_string(message)

        # Wait for reply from server
        reply = sock.recv_string()
        print(f"[Client] Received: {reply}")

        time.sleep(0.2)

    print("[Client] Shutting down")
    sock.close()
    context.term()


def main():
    """Main function to start both processes."""
    print("Starting ZeroMQ multiprocessing example...")
    print("=" * 60)

    # Find a free port
    port = find_free_port()
    print(f"Using port: {port}")
    print("=" * 60)

    # Create processes with the shared port
    server = multiprocessing.Process(target=server_process, args=(port,), name="Server")
    client = multiprocessing.Process(target=client_process, args=(port,), name="Client")

    # Start processes
    server.start()
    client.start()

    # Wait for both to complete
    client.join()
    server.join()

    print("=" * 60)
    print("Both processes completed!")


if __name__ == "__main__":
    main()
