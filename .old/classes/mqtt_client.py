from queue import Queue
import paho.mqtt.client as Client
from loguru import logger

class MQTT_Client():
    def __init__(self, port, subs_and_qos):
        self.queue = Queue()
        self.client = None
        self.port = port or 44444
        self.subs_and_qos = subs_and_qos
        self._setup_client()

    def _setup_client(self):
        """Initialize and connect the MQTT client."""
        self.client = Client.Client(Client.CallbackAPIVersion.VERSION2, "ui_client")
        self.client.user_data_set({"port": self.port})
        self.client.on_connect = self._on_mqtt_connect
        self.client.on_message = self._on_mqtt_message

        try:
            self.client.connect("127.0.0.1", self.port, keepalive=60)
            self.client.loop_start()
            logger.debug(f"UI MQTT client connecting to 127.0.0.1:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect UI MQTT client: {e}")

    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        """Callback when MQTT client connects to broker."""
        if rc == 0:
            logger.debug(f"UI connected to MQTT broker on port {userdata['port']}")
            client.subscribe(self.subs_and_qos)
            logger.debug(f"UI subscribed to topics: {self.subs_and_qos}")
        else:
            logger.error(f"UI failed to connect to MQTT broker, return code: {rc}")

    def _on_mqtt_message(self, client, userdata, message):
        """Callback when MQTT message is received."""
        if self.queue:
            self.queue.put((message.topic, message.payload.decode()))
            logger.debug(f"UI received message - Topic: {message.topic}, Payload: {message.payload.decode()}")