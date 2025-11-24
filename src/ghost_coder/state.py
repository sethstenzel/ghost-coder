"""
State management process with MQTT interface.
Maintains a key-value store accessible via MQTT commands.
"""

import paho.mqtt.client as Client
import json
import time
from loguru import logger
from typing import Dict, Any, Optional, Literal


class StateManager:
    """
    State management system with MQTT interface.

    Features:
    - Key-value store with type support (int, float, str, bool)
    - Get entire state or specific keys
    - Add/update state values
    - Delete state values
    - MQTT command interface on STATE topic
    """

    def __init__(self, mqtt_port: int):
        """
        Initialize the StateManager.

        Args:
            mqtt_port: Port for MQTT broker connection
        """
        # State storage
        self._state: Dict[str, Any] = {}

        # MQTT setup
        self._mqtt_port = mqtt_port
        self._mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION1, "state_client")
        self._mqtt_client.on_connect = self._on_mqtt_connect
        self._mqtt_client.on_message = self._on_mqtt_message
        self._mqtt_connected = False
        self._running = False

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            logger.info("StateManager connected to MQTT broker")
            client.subscribe("STATE", qos=1)
            client.subscribe("APP", qos=1)
            logger.info("StateManager subscribed to STATE and APP topics")
            self._mqtt_connected = True
        else:
            logger.error(f"StateManager failed to connect to MQTT broker, return code: {rc}")

    def _on_mqtt_message(self, client, userdata, message):
        """Handle incoming MQTT messages."""
        try:
            payload = message.payload.decode()
            logger.debug(f"StateManager received message on {message.topic}: {payload}")

            # Handle CLOSE command on APP topic
            if message.topic == "APP":
                try:
                    cmd_data = json.loads(payload)
                    if cmd_data.get("cmd") == "CLOSE":
                        logger.info("StateManager received CLOSE command")
                        self.stop()
                        return
                except json.JSONDecodeError:
                    pass
                return

            # Handle STATE topic commands
            try:
                cmd_data = json.loads(payload)
                cmd = cmd_data.get("cmd")

                if cmd == "get":
                    self._handle_get(cmd_data)
                elif cmd == "add":
                    self._handle_add(cmd_data)
                elif cmd == "del":
                    self._handle_delete(cmd_data)
                elif cmd == "help":
                    self._handle_help()
                else:
                    logger.warning(f"Unknown command: {cmd}")

            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {payload}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_get(self, cmd_data: Dict[str, Any]):
        """Handle get command."""
        key = cmd_data.get("key")

        if key is None:
            # Return entire state
            result = {"result": self._state}
        else:
            # Return specific key
            if key in self._state:
                result = {"result": self._state[key]}
            else:
                result = {"result": None, "error": f"Key '{key}' not found"}

        self.emit("STATE", result)
        logger.debug(f"Get command - key: {key}, result: {result}")

    def _handle_add(self, cmd_data: Dict[str, Any]):
        """Handle add command."""
        key = cmd_data.get("key")
        value = cmd_data.get("value")
        value_type = cmd_data.get("type", "str")

        if key is None:
            result = {"result": "error", "error": "Missing 'key' parameter"}
            self.emit("STATE", result)
            logger.error("Add command missing 'key' parameter")
            return

        if value is None:
            result = {"result": "error", "error": "Missing 'value' parameter"}
            self.emit("STATE", result)
            logger.error("Add command missing 'value' parameter")
            return

        # Convert value to specified type
        try:
            if value_type == "int":
                converted_value = int(value)
            elif value_type == "float":
                converted_value = float(value)
            elif value_type == "bool":
                if isinstance(value, bool):
                    converted_value = value
                elif isinstance(value, str):
                    converted_value = value.lower() in ("true", "1", "yes")
                else:
                    converted_value = bool(value)
            else:  # Default to string
                converted_value = str(value)

            self._state[key] = converted_value
            result = {"result": "ok"}
            self.emit("STATE", result)
            logger.info(f"Added state - key: {key}, value: {converted_value}, type: {value_type}")

        except (ValueError, TypeError) as e:
            result = {"result": "error", "error": f"Failed to convert value to {value_type}: {e}"}
            self.emit("STATE", result)
            logger.error(f"Type conversion error: {e}")

    def _handle_delete(self, cmd_data: Dict[str, Any]):
        """Handle delete command."""
        key = cmd_data.get("key")

        if key is None:
            result = {"result": "error", "error": "Missing 'key' parameter"}
            self.emit("STATE", result)
            logger.error("Delete command missing 'key' parameter")
            return

        if key in self._state:
            del self._state[key]
            result = {"result": "ok"}
            logger.info(f"Deleted state key: {key}")
        else:
            result = {"result": "ok", "warning": f"Key '{key}' not found"}
            logger.debug(f"Delete command - key '{key}' not found")

        self.emit("STATE", result)

    def _handle_help(self):
        """Handle help command."""
        help_details = {
            "description": "State management system with key-value storage",
            "features": [
                "Key-value storage with type support",
                "Supported types: int, float, str, bool",
                "Get entire state or specific keys",
                "Add/update state values with type conversion",
                "Delete state values"
            ],
            "commands": {
                "get": {
                    "description": "Get the entire state or a specific key value",
                    "parameters": {
                        "cmd": "'get' (required)",
                        "key": "Key name (optional - if omitted, returns entire state)"
                    },
                    "examples": [
                        {
                            "description": "Get entire state",
                            "command": {"cmd": "get"},
                            "response": {"result": {"key1": "value1", "key2": 123}}
                        },
                        {
                            "description": "Get specific key",
                            "command": {"cmd": "get", "key": "name"},
                            "response": {"result": "value"}
                        }
                    ]
                },
                "add": {
                    "description": "Add or update a state value with type conversion",
                    "parameters": {
                        "cmd": "'add' (required)",
                        "key": "Key name (required)",
                        "value": "Value to store (required)",
                        "type": "Value type: 'int', 'float', 'str', or 'bool' (optional, default: 'str')"
                    },
                    "examples": [
                        {
                            "description": "Add string value",
                            "command": {"cmd": "add", "key": "name", "value": "John"},
                            "response": {"result": "ok"}
                        },
                        {
                            "description": "Add integer value",
                            "command": {"cmd": "add", "key": "age", "value": "25", "type": "int"},
                            "response": {"result": "ok"}
                        },
                        {
                            "description": "Add boolean value",
                            "command": {"cmd": "add", "key": "active", "value": "true", "type": "bool"},
                            "response": {"result": "ok"}
                        }
                    ]
                },
                "del": {
                    "description": "Delete a key from the state",
                    "parameters": {
                        "cmd": "'del' (required)",
                        "key": "Key name to delete (required)"
                    },
                    "examples": [
                        {
                            "description": "Delete a key",
                            "command": {"cmd": "del", "key": "name"},
                            "response": {"result": "ok"}
                        }
                    ]
                },
                "help": {
                    "description": "Get help information about the state manager",
                    "parameters": {
                        "cmd": "'help' (required)"
                    },
                    "examples": [
                        {
                            "description": "Get help",
                            "command": {"cmd": "help"},
                            "response": {"info": "..."}
                        }
                    ]
                }
            },
            "workflow": [
                "1. Add values to state: {\"cmd\": \"add\", \"key\": \"counter\", \"value\": \"0\", \"type\": \"int\"}",
                "2. Get specific value: {\"cmd\": \"get\", \"key\": \"counter\"}",
                "3. Get entire state: {\"cmd\": \"get\"}",
                "4. Update value: {\"cmd\": \"add\", \"key\": \"counter\", \"value\": \"1\", \"type\": \"int\"}",
                "5. Delete value: {\"cmd\": \"del\", \"key\": \"counter\"}",
                "6. To get help: {\"cmd\": \"help\"}"
            ],
            "notes": [
                "All responses are sent to the STATE topic",
                "Type conversion errors will return an error response",
                "Deleting non-existent keys returns 'ok' with a warning",
                "Getting non-existent keys returns null with an error message"
            ]
        }
        help_message = {"info": help_details}
        self.emit("STATE", help_message)
        logger.info("Sent help message to STATE topic")

    def emit(self, topic: str, data: Dict[str, Any]):
        """Emit a message via MQTT."""
        if self._mqtt_connected:
            message = json.dumps(data)
            self._mqtt_client.publish(topic, message, qos=1)

    def start(self):
        """Start the StateManager and connect to MQTT."""
        if self._running:
            logger.info("StateManager already running")
            return

        self._running = True

        # Connect to MQTT broker
        try:
            self._mqtt_client.connect("127.0.0.1", self._mqtt_port, keepalive=60)
            self._mqtt_client.loop_start()
            logger.info(f"StateManager connecting to MQTT broker on port {self._mqtt_port}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._running = False
            return

    def stop(self):
        """Stop the StateManager."""
        if not self._running:
            return

        self._running = False

        # Stop MQTT client
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()

        logger.info("StateManager stopped")

    def is_running(self) -> bool:
        """Check if StateManager is running."""
        return self._running


def state_process(port: int, enable_logging: bool = False):
    """Run the state manager as a separate process."""
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

    logger.info("Starting StateManager process")
    state_manager = StateManager(mqtt_port=port)
    state_manager.start()

    try:
        # Keep the process running
        while state_manager.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("StateManager interrupted")
    finally:
        state_manager.stop()
        logger.info("StateManager stopped")
