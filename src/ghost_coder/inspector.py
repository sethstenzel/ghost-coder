import paho.mqtt.client as Client
from loguru import logger
import time
import json


logger.configure(
    handlers=[
        {
            "sink": lambda msg: print(msg, end=""),
            "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}: {message}",
        }
    ]
)


MAX_CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAY = 5  # seconds


def on_connect(client, userdata, flags, rc):
    """Callback for when the client connects to the broker."""
    if rc == 0:
        logger.info(f"Inspector connected to broker on port {userdata['port']}")
        client.subscribe("#")  # Subscribe to all topics
        logger.info("Subscribed to all topics (#)")
    else:
        logger.error(f"Inspector failed to connect, return code: {rc}")


def on_message(client, userdata, message):
    """Callback for when a message is received."""
    payload_str = message.payload.decode()
    
    # Try to parse as JSON for better formatting
    try:
        payload_json = json.loads(payload_str)
        payload_display = json.dumps(payload_json, indent=2)
    except json.JSONDecodeError:
        payload_display = payload_str
    
    # Check for close command
    try:
        data = json.loads(payload_str)
        if message.topic == "APP" and data.get("command") == "CLOSE":
            logger.info("Received CLOSE command, shutting down inspector")
            client.disconnect()
            return
    except json.JSONDecodeError:
        pass
    
    logger.info(
        f"Topic: {message.topic} | QoS: {message.qos}\nPayload: {payload_display}"
    )


def inspector_process(port, enable_logging=False):
    """Run an MQTT inspector that logs all messages on all topics."""
    if enable_logging:
        logger.configure(
            handlers=[
                {
                    "sink": lambda msg: print(msg, end=""),
                    "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}: {message}",
                }
            ]
        )
    else:
        logger.disable("ghost_coder")

    logger.info(f"Starting inspector on port {port}")

    userdata = {"port": port, "reconnect_count": 0, "should_exit": False}
    client = Client.Client(Client.CallbackAPIVersion.VERSION1, "inspector")
    client.user_data_set(userdata)
    
    client.on_connect = on_connect
    client.on_message = on_message

    # Retry initial connection
    connected = False
    for attempt in range(1, MAX_CONNECT_ATTEMPTS + 1):
        try:
            logger.info(f"Inspector attempting to connect to 127.0.0.1:{port} (attempt {attempt}/{MAX_CONNECT_ATTEMPTS})")
            client.connect("127.0.0.1", port, keepalive=60)
            connected = True
            break
        except Exception as e:
            if attempt < MAX_CONNECT_ATTEMPTS:
                logger.warning(f"Inspector connection failed: {e}. Retrying in {CONNECT_RETRY_DELAY} seconds...")
                time.sleep(CONNECT_RETRY_DELAY)
            else:
                logger.error(f"Inspector failed to connect after {MAX_CONNECT_ATTEMPTS} attempts: {e}")
                return

    if connected:
        try:
            client.loop_forever()
        except KeyboardInterrupt:
            logger.info("Inspector KeyboardInterrupt, shutting down...")
        except Exception as e:
            logger.error(f"Inspector error: {e}")
        finally:
            client.disconnect()
            logger.info("Inspector stopped")
