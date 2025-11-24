"""
Global input listener for capturing keyboard, mouse, and gamepad events.
Supports hotkey registration (1-8 slots) and MQTT event emission.
"""

from pynput import keyboard, mouse
from inputs import get_gamepad, devices
import threading
import time
import paho.mqtt.client as Client
from typing import Optional, Literal, Dict, Any, List
from dataclasses import dataclass
from enum import Enum
from loguru import logger
import json


# Force refresh of device list before enumerating
def refresh_gamepad_devices():
    """Refresh the gamepad device list by reimporting."""
    global devices
    import importlib
    import inputs
    importlib.reload(inputs)
    devices = inputs.devices
    return devices


class InputSource(Enum):
    """Types of input sources that can be monitored."""
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    GAMEPAD = "gamepad"


@dataclass
class HotkeyEvent:
    """Represents a hotkey configuration."""
    slot: int  # 1-8
    source: InputSource
    value: Any  # Key name, button, gamepad code, etc.
    gamepad_name: Optional[str] = None  # Specific gamepad device name (for gamepad source)
    message: Optional[str] = None  # Custom message to emit when hotkey is triggered
    suppress: bool = False  # Whether to suppress/capture the event (prevent OS from seeing it)


class Listener:
    """
    Global input listener supporting keyboard, mouse, and gamepad inputs.

    Features:
    - Global event capture for keyboard, mouse, and gamepad
    - 8 hotkey slots for registration
    - MQTT event emission
    - Configurable input source for hotkey registration
    """

    def __init__(self, mqtt_port: int, gamepad_name: Optional[str] = None):
        """
        Initialize the Listener.

        Args:
            mqtt_port: Port for MQTT broker connection
            gamepad_name: Specific gamepad to use (None = first available)
        """
        # State management
        self._lock = threading.Lock()
        self._hotkeys: Dict[int, Optional[HotkeyEvent]] = {i: None for i in range(1, 9)}
        self._running = False

        # Recording state
        self._recording_slot: Optional[int] = None
        self._recording_source: Optional[InputSource] = None
        self._recording_gamepad_name: Optional[str] = None
        self._recording_message: Optional[str] = None
        self._recording_suppress: bool = False

        # MQTT setup
        self._mqtt_port = mqtt_port
        self._mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION1, "listener_client")
        self._mqtt_client.on_connect = self._on_mqtt_connect
        self._mqtt_client.on_message = self._on_mqtt_message
        self._mqtt_connected = False

        # Select gamepad
        self._selected_gamepad = gamepad_name
        if self._selected_gamepad is None:
            gamepads = self.get_gamepads()
            if gamepads:
                self._selected_gamepad = gamepads[0]['name']
                logger.info(f"Auto-selected first gamepad: {self._selected_gamepad}")

        # Listeners
        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None
        self._gamepad_thread: Optional[threading.Thread] = None

        # Pressed state tracking (for detecting hotkey triggers)
        self._pressed_keys = set()
        self._pressed_buttons = set()
        self._pressed_gamepad = set()

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            logger.info("Listener connected to MQTT broker")
            client.subscribe("LISTENER", qos=1)
            logger.info("Listener subscribed to LISTENER topic")
            self._mqtt_connected = True
        else:
            logger.error(f"Listener failed to connect to MQTT broker, return code: {rc}")

    def _on_mqtt_message(self, client, userdata, message):
        """Handle incoming MQTT messages on LISTENER topic."""
        try:
            payload = message.payload.decode()
            logger.debug(f"Listener received message: {payload}")
            
            # Try to parse as JSON
            try:
                cmd_data = json.loads(payload)
                
                # Ignore messages that are events (not commands)
                if "event" in cmd_data:
                    logger.debug(f"Ignoring event message: {cmd_data.get('event')}")
                    return
                
                # Handle cmd-based format
                cmd = cmd_data.get("cmd")
                
                if cmd == "register":
                    # Register hotkey
                    slot = cmd_data.get("slot")
                    source = cmd_data.get("input")
                    suppress = cmd_data.get("suppress", False)

                    try:
                        self.register_hotkey(slot, source, gamepad_name=self._selected_gamepad, message=None, suppress=suppress)
                    except Exception as e:
                        logger.error(f"Error registering hotkey: {e}")

                elif cmd == "unregister":
                    # Unregister hotkey
                    slot = cmd_data.get("slot")
                    self.clear_hotkey(slot)

                elif cmd == "help":
                    # Handle help command
                    self._handle_help()

                else:
                    # Handle other commands (get_gamepads, etc.)
                    self._handle_command(cmd_data)
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {payload}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def start(self):
        """Start all input listeners and connect to MQTT."""
        with self._lock:
            if self._running:
                logger.info("Listener already running")
                return

            self._running = True

        # Connect to MQTT broker
        try:
            self._mqtt_client.connect("127.0.0.1", self._mqtt_port, keepalive=60)
            self._mqtt_client.loop_start()
            logger.info(f"Listener connecting to MQTT broker on port {self._mqtt_port}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return

        # Start keyboard listener
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_keyboard_press,
            on_release=self._on_keyboard_release,
            suppress=False
        )
        self._keyboard_listener.start()

        # Start mouse listener
        self._mouse_listener = mouse.Listener(
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
            suppress=False
        )
        self._mouse_listener.start()

        # Start gamepad thread
        self._gamepad_thread = threading.Thread(target=self._gamepad_loop, daemon=True)
        self._gamepad_thread.start()

        logger.info("Listener started all input listeners")

    def stop(self):
        """Stop all input listeners."""
        with self._lock:
            if not self._running:
                return

            self._running = False

        # Stop keyboard listener
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

        # Stop mouse listener
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None

        # Stop MQTT client
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()

        logger.info("Listener stopped all input listeners")

    def get_gamepads(self) -> List[Dict[str, Any]]:
        """Get a list of available gamepad devices."""
        # Refresh device list to detect newly connected gamepads
        current_devices = refresh_gamepad_devices()
        
        gamepad_list = []
        for i, dev in enumerate(current_devices.gamepads):
            name = getattr(dev, 'name', f'Gamepad {i+1}')
            gamepad_list.append({
                'index': i,
                'name': name,
                'device': dev
            })
        return gamepad_list

    def emit(self, topic: str, data: Dict[str, Any]):
        """Emit an event via MQTT."""
        if self._mqtt_connected:
            message = json.dumps(data)
            self._mqtt_client.publish(topic, message, qos=1)

    def _handle_help(self):
        """Handle the help command."""
        help_details = {
            "description": "Global input listener supporting keyboard, mouse, and gamepad hotkeys",
            "features": [
                "8 hotkey slots (1-8)",
                "Multiple input sources (keyboard, mouse, gamepad)",
                "Auto-select first available gamepad",
                "Custom message attachment",
                "Event suppression (prevent OS from seeing event)"
            ],
            "commands": {
                "register": {
                    "description": "Register a hotkey for a specific slot",
                    "parameters": {
                        "cmd": "'register' (required)",
                        "slot": "Hotkey slot number (1-8, required)",
                        "input": "Input source: 'keyboard', 'mouse', or 'gamepad' (required)",
                        "suppress": "Whether to suppress the event (boolean, required)"
                    },
                    "example": {
                        "cmd": "register",
                        "slot": 1,
                        "input": "keyboard",
                        "suppress": False
                    }
                },
                "unregister": {
                    "description": "Unregister/clear a hotkey slot",
                    "parameters": {
                        "cmd": "'unregister' (required)",
                        "slot": "Hotkey slot number (1-8, required)"
                    },
                    "example": {
                        "cmd": "unregister",
                        "slot": 1
                    }
                },
                "help": {
                    "description": "Get help information about the listener",
                    "parameters": {
                        "cmd": "'help' (required)"
                    },
                    "example": {
                        "cmd": "help"
                    }
                }
            },
            "events": {
                "hotkey_triggered": {
                    "description": "Emitted when a registered hotkey is triggered",
                    "topic": "LISTENER",
                    "fields": {
                        "event": "'hotkey_triggered'",
                        "slot": "Hotkey slot number (1-8)",
                        "source": "Input source ('keyboard', 'mouse', or 'gamepad')",
                        "value": "The key/button/code that was pressed",
                        "gamepad_name": "Gamepad device name (if source is gamepad)",
                        "message": "Custom message (if configured)"
                    },
                    "example": {
                        "event": "hotkey_triggered",
                        "slot": 1,
                        "source": "keyboard",
                        "value": "a",
                        "message": "My custom action"
                    }
                }
            },
            "workflow": [
                "1. Send registration to LISTENER topic: {\"cmd\": \"register\", \"slot\": 1, \"input\": \"keyboard\", \"suppress\": false}",
                "2. Press the key/button/gamepad input you want to register",
                "3. Listener will confirm registration via log",
                "4. When you press that input again, a 'hotkey_triggered' event is emitted to LISTENER topic",
                "5. To unregister: send {\"cmd\": \"unregister\", \"slot\": 1}",
                "6. To get help: send {\"cmd\": \"help\"}"
            ]
        }
        help_message = {"info": help_details}
        self.emit("LISTENER", help_message)
        logger.info("Sent help message to LISTENER topic")

    def _handle_command(self, cmd_data: Dict[str, Any]):
        """Handle a command from MQTT LISTENER topic."""
        cmd_type = cmd_data.get("command")

        # Handle get_gamepads command
        if cmd_type == "get_gamepads":
            gamepads = self.get_gamepads()
            self.emit("LISTENER_RESPONSE", {
                "command": "get_gamepads",
                "gamepads": [{"index": gp["index"], "name": gp["name"]} for gp in gamepads]
            })
        else:
            logger.warning(f"Unknown command or invalid format: {cmd_data}")

    def register_hotkey(self, slot: int, source: Literal["keyboard", "mouse", "gamepad"], gamepad_name: Optional[str] = None, message: Optional[str] = None, suppress: bool = False):
        """Start recording a hotkey for the specified slot."""
        if slot < 1 or slot > 8:
            raise ValueError(f"Slot must be between 1 and 8, got {slot}")

        source_enum = InputSource(source)

        # If gamepad source, validate or use selected gamepad
        if source_enum == InputSource.GAMEPAD:
            # Refresh gamepad list to detect newly connected gamepads
            refresh_gamepad_devices()

            if gamepad_name is None:
                gamepad_name = self._selected_gamepad

            if gamepad_name is None:
                raise ValueError("No gamepad available")

        with self._lock:
            self._recording_slot = slot
            self._recording_source = source_enum
            self._recording_gamepad_name = gamepad_name
            self._recording_message = message
            self._recording_suppress = suppress

        suppress_str = " (will suppress)" if suppress else ""
        logger.info(f"Recording hotkey for slot {slot} from {source}{suppress_str}... Press/click now.")

    def clear_hotkey(self, slot: int):
        """Clear the hotkey configuration for a specific slot."""
        with self._lock:
            self._hotkeys[slot] = None

        logger.info(f"Cleared hotkey slot {slot}")

    # ==================== Keyboard Handlers ====================

    def _on_keyboard_press(self, key):
        """Handle keyboard key press events."""
        key_str = self._format_key(key)
        should_suppress = False

        with self._lock:
            # Check if we're recording
            if self._recording_slot and self._recording_source == InputSource.KEYBOARD:
                hotkey = HotkeyEvent(
                    slot=self._recording_slot,
                    source=InputSource.KEYBOARD,
                    value=key_str,
                    message=self._recording_message,
                    suppress=self._recording_suppress
                )
                self._hotkeys[self._recording_slot] = hotkey
                msg_info = f" with message '{self._recording_message}'" if self._recording_message else ""
                suppress_info = " (suppressed)" if self._recording_suppress else ""
                logger.info(f"Registered hotkey {self._recording_slot}: Keyboard '{key_str}'{msg_info}{suppress_info}")
                self._recording_slot = None
                self._recording_source = None
                self._recording_message = None
                self._recording_suppress = False
                return

            # Track pressed state
            self._pressed_keys.add(key_str)

            # Check if this triggers any hotkey
            for slot, hotkey in self._hotkeys.items():
                if hotkey and hotkey.source == InputSource.KEYBOARD and hotkey.value == key_str:
                    self._trigger_hotkey(slot, hotkey)
                    if hotkey.suppress:
                        should_suppress = True

        if should_suppress:
            return False

    def _on_keyboard_release(self, key):
        """Handle keyboard key release events."""
        key_str = self._format_key(key)

        with self._lock:
            self._pressed_keys.discard(key_str)

    def _format_key(self, key) -> str:
        """Format a pynput key object to a string."""
        try:
            if hasattr(key, 'char') and key.char:
                return key.char
            return str(key).replace('Key.', '')
        except:
            return str(key)

    # ==================== Mouse Handlers ====================

    def _on_mouse_click(self, _x, _y, button, pressed):
        """Handle mouse click events."""
        button_str = self._format_button(button)
        
        if not pressed:
            with self._lock:
                self._pressed_buttons.discard(button_str)
            return

        should_suppress = False

        with self._lock:
            # Check if we're recording
            if self._recording_slot and self._recording_source == InputSource.MOUSE:
                hotkey = HotkeyEvent(
                    slot=self._recording_slot,
                    source=InputSource.MOUSE,
                    value=button_str,
                    message=self._recording_message,
                    suppress=self._recording_suppress
                )
                self._hotkeys[self._recording_slot] = hotkey
                msg_info = f" with message '{self._recording_message}'" if self._recording_message else ""
                suppress_info = " (suppressed)" if self._recording_suppress else ""
                logger.info(f"Registered hotkey {self._recording_slot}: Mouse '{button_str}'{msg_info}{suppress_info}")
                self._recording_slot = None
                self._recording_source = None
                self._recording_message = None
                self._recording_suppress = False
                return

            # Track pressed state
            self._pressed_buttons.add(button_str)

            # Check if this triggers any hotkey
            for slot, hotkey in self._hotkeys.items():
                if hotkey and hotkey.source == InputSource.MOUSE and hotkey.value == button_str:
                    self._trigger_hotkey(slot, hotkey)
                    if hotkey.suppress:
                        should_suppress = True

        if should_suppress:
            return False

    def _on_mouse_scroll(self, _x, _y, dx, dy):
        """Handle mouse scroll events."""
        scroll_event = f"scroll_{'up' if dy > 0 else 'down'}" if dy != 0 else f"scroll_{'right' if dx > 0 else 'left'}"
        should_suppress = False

        with self._lock:
            # Check if we're recording
            if self._recording_slot and self._recording_source == InputSource.MOUSE:
                hotkey = HotkeyEvent(
                    slot=self._recording_slot,
                    source=InputSource.MOUSE,
                    value=scroll_event,
                    message=self._recording_message,
                    suppress=self._recording_suppress
                )
                self._hotkeys[self._recording_slot] = hotkey
    def _on_mouse_scroll(self, _x, _y, dx, dy):
        """Handle mouse scroll events."""
        scroll_event = f"scroll_{'up' if dy > 0 else 'down'}" if dy != 0 else f"scroll_{'right' if dx > 0 else 'left'}"
        should_suppress = False

        with self._lock:
            # Check if we're recording
            if self._recording_slot and self._recording_source == InputSource.MOUSE:
                hotkey = HotkeyEvent(
                    slot=self._recording_slot,
                    source=InputSource.MOUSE,
                    value=scroll_event,
                    message=self._recording_message,
                    suppress=self._recording_suppress
                )
                self._hotkeys[self._recording_slot] = hotkey
                msg_info = f" with message '{self._recording_message}'" if self._recording_message else ""
                suppress_info = " (suppressed)" if self._recording_suppress else ""
                logger.info(f"Registered hotkey {self._recording_slot}: Mouse '{scroll_event}'{msg_info}{suppress_info}")
                self._recording_slot = None
                self._recording_source = None
                self._recording_message = None
                self._recording_suppress = False
                return

            # Check if this triggers any hotkey
            for slot, hotkey in self._hotkeys.items():
                if hotkey and hotkey.source == InputSource.MOUSE and hotkey.value == scroll_event:
                    self._trigger_hotkey(slot, hotkey)
                    if hotkey.suppress:
                        should_suppress = True

        if should_suppress:
            return False

    def _format_button(self, button) -> str:
        """Format a pynput button object to a string."""
        return str(button).replace('Button.', '')

    # ==================== Gamepad Handlers ====================

    def _gamepad_loop(self):
        """Polling loop for gamepad events."""
        while True:
            with self._lock:
                if not self._running:
                    break

            try:
                events = get_gamepad()
                for event in events:
                    if event.ev_type not in ('Key', 'Absolute'):
                        continue

                    if event.state == 0:
                        # Handle release
                        with self._lock:
                            self._pressed_gamepad.discard(event.code)
                        continue

                    gamepad_code = event.code
                    gamepad_device_name = getattr(event.device, 'name', None)

                    # Only process if this is the selected gamepad
                    if self._selected_gamepad and gamepad_device_name != self._selected_gamepad:
                        continue

                    with self._lock:
                        # Check if we're recording
                        if self._recording_slot and self._recording_source == InputSource.GAMEPAD:
                            hotkey = HotkeyEvent(
                                slot=self._recording_slot,
                                source=InputSource.GAMEPAD,
                                value=gamepad_code,
                                gamepad_name=gamepad_device_name,
                                message=self._recording_message,
                                suppress=self._recording_suppress
                            )
                            self._hotkeys[self._recording_slot] = hotkey
                            msg_info = f" with message '{self._recording_message}'" if self._recording_message else ""
                            suppress_info = " (suppressed)" if self._recording_suppress else ""
                            logger.info(f"Registered hotkey {self._recording_slot}: Gamepad '{gamepad_device_name}' button '{gamepad_code}'{msg_info}{suppress_info}")
                            self._recording_slot = None
                            self._recording_source = None
                            self._recording_gamepad_name = None
                            self._recording_message = None
                            self._recording_suppress = False
                            continue

                        # Track pressed state
                        if gamepad_code not in self._pressed_gamepad:
                            self._pressed_gamepad.add(gamepad_code)

                            # Check if this triggers any hotkey
                            for slot, hotkey in self._hotkeys.items():
                                if (hotkey and
                                    hotkey.source == InputSource.GAMEPAD and
                                    hotkey.value == gamepad_code and
                                    (hotkey.gamepad_name is None or hotkey.gamepad_name == gamepad_device_name)):
                                    self._trigger_hotkey(slot, hotkey)

            except Exception as e:
                # No gamepad connected or other error
                time.sleep(0.5)
                continue

            time.sleep(0.01)

    # ==================== Hotkey Triggering ====================

    def _trigger_hotkey(self, slot: int, hotkey: HotkeyEvent):
        """Trigger a hotkey event."""
        logger.info(f"Hotkey {slot} pressed: {hotkey.source.value} '{hotkey.value}'")

        # Emit MQTT event to LISTENER topic
        event_data = {
            "event": "hotkey_triggered",
            "slot": slot,
            "source": hotkey.source.value,
            "value": hotkey.value
        }
        if hotkey.gamepad_name:
            event_data["gamepad_name"] = hotkey.gamepad_name
        if hotkey.message:
            event_data["message"] = hotkey.message

        self.emit("LISTENER", event_data)


def listener_process(port: int):
    """Run the listener as a separate process."""
    logger.configure(
        handlers=[
            {
                "sink": lambda msg: print(msg, end=""),
                "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}: {message}",
            }
        ]
    )
    
    logger.info("Starting Listener process")
    listener = Listener(mqtt_port=port)
    listener.start()

    try:
        # Keep the process running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Listener interrupted")
    finally:
        listener.stop()
        logger.info("Listener stopped")

