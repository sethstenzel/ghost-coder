"""
Typer process for automated text input simulation.
Listens for commands on MQTT TYPER topic and monitors STATE for configuration.
"""

import time
import json
import threading
from ghost_coder.data import SingleKey, MultiKeys, TimedPause, MouseScroll, TextData
from pynput.keyboard import Key
from pynput.keyboard import Controller as KbController
from pynput.mouse import Controller as MsController
import pygetwindow as gw
import paho.mqtt.client as Client
from loguru import logger
from typing import Optional, Dict, Any


class Typer:
    """
    Automated text input simulator with MQTT interface.

    Features:
    - Load text files and parse into tokens
    - Simulate keyboard and mouse actions
    - Window focus detection and pausing
    - Configurable speed and behavior
    - MQTT command interface on TYPER topic
    - State synchronization via STATE topic
    """

    def __init__(self, mqtt_port: int):
        """
        Initialize the Typer.

        Args:
            mqtt_port: Port for MQTT broker connection
        """
        # Text data
        self.text_tokens = None
        self.text_tokens_preview = []
        self.original_text_tokens = None  # Store original for reset
        self.current_file_path = None  # Store file path for reload

        # Playback state
        self.play = False
        self.paused = False
        self.advance_to_newline = 0
        self.advance_token = 0

        # Configuration (will be synced from STATE)
        self.speed = 100
        self.pause_on_new_line = True
        self.window_title = ""
        self.pause_on_window_not_focused = True
        self.refocus_window_on_resume = True
        self.start_playback_paused = False
        self.auto_home_on_newline = False
        self.control_on_newline = False
        self.replace_quad_spaces_with_tab = True

        # Window handle
        self.hwnd = None

        # Input controllers
        self.kb = KbController()
        self.ms = MsController()

        # MQTT setup
        self._mqtt_port = mqtt_port
        self._mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION1, "typer_client")
        self._mqtt_client.on_connect = self._on_mqtt_connect
        self._mqtt_client.on_message = self._on_mqtt_message
        self._mqtt_connected = False
        self._running = False

        # State sync thread
        self._state_sync_thread: Optional[threading.Thread] = None
        self._state_values = {}

        # Playback thread
        self._playback_thread: Optional[threading.Thread] = None

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            logger.info("Typer connected to MQTT broker")
            client.subscribe("TYPER", qos=1)
            client.subscribe("STATE", qos=1)
            client.subscribe("APP", qos=1)
            logger.info("Typer subscribed to TYPER, STATE, and APP topics")
            self._mqtt_connected = True
        else:
            logger.error(f"Typer failed to connect to MQTT broker, return code: {rc}")

    def _on_mqtt_message(self, client, userdata, message):
        """Handle incoming MQTT messages."""
        try:
            payload = message.payload.decode()
            logger.debug(f"Typer received message on {message.topic}: {payload}")

            # Handle CLOSE command on APP topic
            if message.topic == "APP":
                try:
                    cmd_data = json.loads(payload)
                    if cmd_data.get("cmd") == "CLOSE" or cmd_data.get("command") == "CLOSE":
                        logger.info("Typer received CLOSE command")
                        self.stop()
                        return
                except json.JSONDecodeError:
                    pass
                return

            # Handle STATE topic messages (state updates)
            if message.topic == "STATE":
                try:
                    state_data = json.loads(payload)
                    # Update local state cache
                    if "result" in state_data and isinstance(state_data["result"], dict):
                        self._state_values.update(state_data["result"])
                        self._sync_from_state()
                except json.JSONDecodeError:
                    pass
                return

            # Handle TYPER topic commands
            try:
                cmd_data = json.loads(payload)
                cmd = cmd_data.get("cmd")

                if cmd == "load_file":
                    self._handle_load_file(cmd_data)
                elif cmd == "data":
                    self._handle_data()
                elif cmd == "play":
                    self._handle_play()
                elif cmd == "stop":
                    self._handle_stop()
                elif cmd == "pause":
                    self._handle_pause()
                elif cmd == "advance_newline":
                    self._handle_advance_newline()
                elif cmd == "advance_token":
                    self._handle_advance_token()
                elif cmd == "help":
                    self._handle_help()
                else:
                    logger.warning(f"Unknown command: {cmd}")

            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message: {payload}")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def _handle_load_file(self, cmd_data: Dict[str, Any]):
        """Handle load_file command."""
        file_path = cmd_data.get("file")

        if not file_path:
            error_msg = {"error": "Missing 'file' parameter"}
            self.emit("UI", error_msg)
            logger.error("load_file command missing 'file' parameter")
            return

        try:
            self.initialize_text_data(file_path)
            result = {"result": "ok", "message": f"Loaded file: {file_path}"}
            self.emit("TYPER", result)
            logger.info(f"Successfully loaded file: {file_path}")
        except Exception as e:
            error_msg = {"error": f"Failed to load file: {str(e)}"}
            self.emit("UI", error_msg)
            logger.error(f"Error loading file {file_path}: {e}")

    def _handle_data(self):
        """Handle data command - return text tokens preview."""
        if self.text_tokens_preview:
            result = {"result": self.text_tokens_preview}
        else:
            result = {"result": [], "warning": "No data loaded"}

        self.emit("TYPER", result)
        logger.debug(f"Sent data with {len(self.text_tokens_preview)} tokens")

    def _handle_play(self):
        """Handle play command - start typing."""
        if not self.text_tokens:
            error_msg = {"error": "No data loaded. Use load_file first."}
            self.emit("TYPER", error_msg)
            logger.error("Cannot play: no data loaded")
            return

        if self.play:
            logger.warning("Playback already running")
            return

        self.play = True
        self.paused = False

        # Update play_status state to playing
        self._update_play_status("playing")

        # Start playback in a separate thread
        self._playback_thread = threading.Thread(target=self._play_with_delay, daemon=True)
        self._playback_thread.start()

        result = {"result": "ok", "message": "Playback will start in 5 seconds"}
        self.emit("TYPER", result)
        logger.info("Playback will start in 5 seconds")

    def _handle_stop(self):
        """Handle stop command."""
        self.play = False
        self.paused = False

        # Reset to beginning of file
        self._reset_to_beginning()

        # Update play_status state to stopped
        self._update_play_status("stopped")

        result = {"result": "ok", "message": "Playback stopped - reset to beginning"}
        self.emit("TYPER", result)
        logger.info("Stopped playback and reset to beginning")

    def _handle_pause(self):
        """Handle pause command."""
        self.paused = not self.paused
        state = "paused" if self.paused else "playing"

        # If resuming (unpausing) and refocus is enabled, focus the window first and wait
        if not self.paused and self.refocus_window_on_resume:  # Now playing
            self.focus_window()
            logger.info("Focused window before resuming playback, waiting 2 seconds...")
            time.sleep(2)
            logger.info("Resuming playback now")

        # Update play_status state
        self._update_play_status(state)

        result = {"result": "ok", "message": f"Playback {state}"}
        self.emit("TYPER", result)
        logger.info(f"Playback {state}")

    def _handle_advance_newline(self):
        """Handle advance_newline command - advance to next newline."""
        self.advance_to_newline += 1
        result = {"result": "ok", "message": "Advancing to next newline"}
        self.emit("TYPER", result)
        logger.info("Advance to newline triggered")

    def _handle_advance_token(self):
        """Handle advance_token command - advance by one token."""
        self.advance_token += 1
        result = {"result": "ok", "message": "Advancing by one token"}
        self.emit("TYPER", result)
        logger.info("Advance token triggered")

    def _handle_help(self):
        """Handle help command."""
        help_details = {
            "description": "Automated text input simulator with window focus detection",
            "features": [
                "Load text files and parse into tokens",
                "Simulate keyboard and mouse actions",
                "Window focus detection and auto-pause",
                "Configurable typing speed and behavior",
                "State synchronization via STATE topic"
            ],
            "commands": {
                "load_file": {
                    "description": "Load a text file for typing",
                    "parameters": {
                        "cmd": "'load_file' (required)",
                        "file": "File path to load (required)"
                    },
                    "example": {
                        "cmd": "load_file",
                        "file": "c:/path/to/file.txt"
                    }
                },
                "data": {
                    "description": "Get the loaded text tokens preview",
                    "parameters": {
                        "cmd": "'data' (required)"
                    },
                    "example": {
                        "cmd": "data"
                    }
                },
                "play": {
                    "description": "Start typing the loaded text",
                    "parameters": {
                        "cmd": "'play' (required)"
                    },
                    "example": {
                        "cmd": "play"
                    }
                },
                "stop": {
                    "description": "Stop typing",
                    "parameters": {
                        "cmd": "'stop' (required)"
                    },
                    "example": {
                        "cmd": "stop"
                    }
                },
                "pause": {
                    "description": "Toggle pause/resume",
                    "parameters": {
                        "cmd": "'pause' (required)"
                    },
                    "example": {
                        "cmd": "pause"
                    }
                },
                "advance_newline": {
                    "description": "Advance to next newline (when paused)",
                    "parameters": {
                        "cmd": "'advance_newline' (required)"
                    },
                    "example": {
                        "cmd": "advance_newline"
                    }
                },
                "advance_token": {
                    "description": "Advance by one token (when paused)",
                    "parameters": {
                        "cmd": "'advance_token' (required)"
                    },
                    "example": {
                        "cmd": "advance_token"
                    }
                },
                "help": {
                    "description": "Get help information",
                    "parameters": {
                        "cmd": "'help' (required)"
                    },
                    "example": {
                        "cmd": "help"
                    }
                }
            },
            "state_keys": [
                "speed (int): Typing speed in milliseconds",
                "pause_on_new_line (bool): Auto-pause on newline",
                "window_title (str): Target window title",
                "pause_on_window_not_focused (bool): Auto-pause when window loses focus",
                "refocus_window_on_resume (bool): Refocus target window when resuming from pause",
                "start_playback_paused (bool): Start in paused state",
                "auto_home_on_newline (bool): Press Home key after Enter",
                "control_on_newline (bool): Press Ctrl+Enter instead of Enter",
                "replace_quad_spaces_with_tab (bool): Convert 4 spaces to Tab"
            ],
            "workflow": [
                "1. Load a file: {\"cmd\": \"load_file\", \"file\": \"path/to/file.txt\"}",
                "2. Get data preview: {\"cmd\": \"data\"}",
                "3. Configure via STATE topic (optional)",
                "4. Start playback: {\"cmd\": \"play\"}",
                "5. Pause/resume: {\"cmd\": \"pause\"}",
                "6. Stop: {\"cmd\": \"stop\"}"
            ]
        }
        help_message = {"info": help_details}
        self.emit("TYPER", help_message)
        logger.info("Sent help message to TYPER topic")

    def _sync_from_state(self):
        """Sync configuration from STATE values."""
        if "speed" in self._state_values:
            self.speed = self._state_values["speed"]
        if "pause_on_new_line" in self._state_values:
            self.pause_on_new_line = self._state_values["pause_on_new_line"]
        if "window_title" in self._state_values:
            self.window_title = self._state_values["window_title"]
            self._update_window_handle()
        if "pause_on_window_not_focused" in self._state_values:
            self.pause_on_window_not_focused = self._state_values["pause_on_window_not_focused"]
        if "refocus_window_on_resume" in self._state_values:
            self.refocus_window_on_resume = self._state_values["refocus_window_on_resume"]
        if "start_playback_paused" in self._state_values:
            self.start_playback_paused = self._state_values["start_playback_paused"]
        if "auto_home_on_newline" in self._state_values:
            self.auto_home_on_newline = self._state_values["auto_home_on_newline"]
        if "control_on_newline" in self._state_values:
            self.control_on_newline = self._state_values["control_on_newline"]
        if "replace_quad_spaces_with_tab" in self._state_values:
            self.replace_quad_spaces_with_tab = self._state_values["replace_quad_spaces_with_tab"]

    def _update_window_handle(self):
        """Update the window handle based on window_title."""
        if self.window_title:
            try:
                windows = gw.getWindowsWithTitle(self.window_title)
                if windows:
                    self.hwnd = windows[0]._hWnd
                    logger.info(f"Updated window handle for: {self.window_title}")
            except Exception as e:
                logger.error(f"Error getting window handle: {e}")

    def _state_sync_loop(self):
        """Background thread that periodically requests state updates."""
        while self._running:
            if self._mqtt_connected:
                state_request = {"cmd": "get"}
                self.emit("STATE", state_request)
                time.sleep(0.1)

    def initialize_text_data(self, file_path: str):
        """Load and parse a text file into tokens."""
        with open(file_path, 'r', encoding='utf-8') as f:
            file_data = f.read()
            text_data = TextData(file_data, replace_quad_spaces_with_tab=self.replace_quad_spaces_with_tab)
            self.text_tokens = text_data.text_tokens.copy()
            self.original_text_tokens = text_data.text_tokens.copy()  # Save original
            self.text_tokens_preview = ['[ ' + str(x) + ' ]' for x in text_data.text_tokens]
            self.current_file_path = file_path  # Save file path

    def type_token(self, token):
        """Type a single token."""
        kb = self.kb
        ms = self.ms
        token_completed = False

        while not token_completed and self.play:
            if isinstance(token, MultiKeys):
                if self.check_window_focused(pause_if_not=True):
                    for key in token.keys:
                        self.focus_window()
                        if hasattr(Key, key):
                            kb.press(getattr(Key, key))
                        else:
                            kb.press(key)
                    time.sleep(self.speed/1000/2)
                    for key in token.keys:
                        self.focus_window()
                        if hasattr(Key, key):
                            kb.release(getattr(Key, key))
                        else:
                            kb.release(key)
                    token_completed = True
            elif isinstance(token, SingleKey):
                if self.check_window_focused(pause_if_not=True):
                    if token.key == "enter" and self.control_on_newline:
                        kb.press(Key.ctrl)
                        kb.press(getattr(Key, token.key))
                        time.sleep(self.speed/1000)
                        kb.release(getattr(Key, token.key))
                        kb.release(Key.ctrl)
                    elif token.key == "atpause":
                        self.paused = True
                        self._update_play_status("paused")
                    else:
                        kb.press(getattr(Key, token.key))
                        time.sleep(self.speed/1000)
                        kb.release(getattr(Key, token.key))

                    if token.key == "enter" and self.auto_home_on_newline:
                        kb.press(Key.home)
                        time.sleep(self.speed/1000)
                        kb.release(Key.home)

                    if (token.key == "enter" and self.pause_on_new_line) or (token.key == "enter" and self.advance_to_newline > 0):
                        if self.advance_to_newline > 0:
                            self.advance_to_newline -= 1
                        self.paused = True
                        self._update_play_status("paused")
                    token_completed = True

            elif isinstance(token, TimedPause):
                if self.check_window_focused(pause_if_not=True):
                    time.sleep(token.time)
                    token_completed = True
            elif isinstance(token, MouseScroll):
                if self.check_window_focused(pause_if_not=True):
                    for _ in range(token.scroll_count):
                        ms.scroll(0, token.scroll_direction)
                        time.sleep(self.speed/1000/2)
                    token_completed = True
            else:
                for char in token:
                    char_completed = False
                    while not char_completed:
                        if (
                            (self.check_window_focused(pause_if_not=True) and not self.paused) or
                            (self.play and self.paused and self.advance_to_newline > 0) or
                            (self.play and self.paused and self.advance_token > 0)
                        ):
                            self.focus_window()
                            kb.press(char)
                            time.sleep(self.speed/1000)
                            kb.release(char)
                            char_completed = True
                        time.sleep(0.01)
                token_completed = True
        time.sleep(0.01)
        return token_completed

    def _play_with_delay(self):
        """Wait 5 seconds then start typing."""
        logger.info("Waiting 5 seconds before starting playback...")
        time.sleep(5)

        # Capture the currently focused window
        self._capture_active_window()

        logger.info("Starting playback now")
        result = {"result": "ok", "message": "Playback started"}
        self.emit("TYPER", result)
        self.type_text_tokens()

    def _capture_active_window(self):
        """Capture the currently active window as the target."""
        try:
            active_window = gw.getActiveWindow()
            if active_window:
                self.hwnd = active_window._hWnd
                self.window_title = active_window.title
                logger.info(f"Captured active window: '{self.window_title}' (hwnd: {self.hwnd})")
            else:
                logger.warning("No active window found to capture")
                self.hwnd = None
        except Exception as e:
            logger.error(f"Error capturing active window: {e}")
            self.hwnd = None

    def type_text_tokens(self):
        """Type all loaded text tokens."""
        self.focus_window()
        if self.start_playback_paused:
            self.paused = True
            self._update_play_status("paused")

        for token in self.text_tokens:
            token_completed = False
            while not token_completed and self.play is True:
                if (
                    (self.play and not self.paused) or
                    (self.play and self.paused and self.advance_to_newline > 0) or
                    (self.play and self.paused and self.advance_token > 0)
                ):
                    if (not self.paused and self.check_window_focused(pause_if_not=True) or
                        (self.play and self.paused and self.advance_to_newline > 0) or
                        (self.play and self.paused and self.advance_token > 0)
                    ):
                        self.focus_window()
                        token_completed = self.type_token(token)
                        if token_completed and self.advance_token > 0:
                            self.advance_token -= 1
                if not self.play:
                    break
                if self.paused:
                    time.sleep(0.1)
            if not self.play:
                break
            if token_completed:
                # Update preview (remove first token)
                if self.text_tokens_preview:
                    self.text_tokens_preview.pop(0)

        # Playback finished
        self.play = False
        self.paused = False

        # Update play_status state to stopped
        self._update_play_status("stopped")

        result = {"result": "ok", "message": "Playback finished"}
        self.emit("TYPER", result)
        logger.info("Playback finished")

    def check_window_focused(self, pause_if_not=False):
        """Check if target window is focused."""
        if not self.hwnd or not self.pause_on_window_not_focused:
            return True

        active_window = gw.getActiveWindow()
        if not active_window or active_window._hWnd != self.hwnd:
            if pause_if_not:
                self.paused = True
                # Update play_status state to paused
                self._update_play_status("paused")
            return False
        return True

    def focus_window(self):
        """Focus the target window."""
        if self.hwnd:
            try:
                windows = [w for w in gw.getAllWindows() if w._hWnd == self.hwnd]
                if windows:
                    windows[0].activate()
            except Exception as e:
                logger.debug(f"Error focusing window: {e}")

    def emit(self, topic: str, data: Dict[str, Any]):
        """Emit a message via MQTT."""
        if self._mqtt_connected:
            message = json.dumps(data)
            self._mqtt_client.publish(topic, message, qos=1)

    def _update_play_status(self, status: str):
        """Update the play_status state."""
        state_msg = {"cmd": "add", "key": "play_status", "value": status, "type": "str"}
        self.emit("STATE", state_msg)
        logger.debug(f"Updated play_status to '{status}'")

    def _reset_to_beginning(self):
        """Reset tokens to beginning of file."""
        if self.original_text_tokens:
            self.text_tokens = self.original_text_tokens.copy()
            self.text_tokens_preview = ['[ ' + str(x) + ' ]' for x in self.original_text_tokens]
            logger.info("Reset to beginning of file")
        else:
            logger.warning("No original tokens to reset to")

    def start(self):
        """Start the Typer and connect to MQTT."""
        if self._running:
            logger.info("Typer already running")
            return

        self._running = True

        # Connect to MQTT broker
        try:
            self._mqtt_client.connect("127.0.0.1", self._mqtt_port, keepalive=60)
            self._mqtt_client.loop_start()
            logger.info(f"Typer connecting to MQTT broker on port {self._mqtt_port}")
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._running = False
            return

        # Start state sync thread
        self._state_sync_thread = threading.Thread(target=self._state_sync_loop, daemon=True)
        self._state_sync_thread.start()

    def stop(self):
        """Stop the Typer."""
        if not self._running:
            return

        self._running = False
        self.play = False

        # Stop MQTT client
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()

        logger.info("Typer stopped")

    def is_running(self) -> bool:
        """Check if Typer is running."""
        return self._running


def typer_process(port: int, enable_logging: bool = False):
    """Run the typer as a separate process."""
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

    logger.info("Starting Typer process")
    typer = Typer(mqtt_port=port)
    typer.start()

    try:
        # Keep the process running
        while typer.is_running():
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Typer interrupted")
    finally:
        typer.stop()
        logger.info("Typer stopped")
