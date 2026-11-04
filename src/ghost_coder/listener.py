"""
Global input listener: records and detects keyboard, mouse and gamepad hotkeys.

MQTT interface (topic LISTENER)
  in   {"cmd": "register", "slot": n, "input": "keyboard|mouse|gamepad"}
       {"cmd": "restore", "slot": n, "source": ..., "value": ..., "gamepad_name": ...}
       {"cmd": "unregister", "slot": n} | {"cmd": "cancel"}
  out  {"event": "listener_ready"}
       {"event": "hotkey_triggered", "slot": n, ...}
       {"event": "hotkey_registered" | "hotkey_registration_error" | "hotkey_registration_cancelled", ...}
       {"event": "hotkey_restoration_success" | "hotkey_restoration_error", ...}
  APP in {"command": "CLOSE"}

Synthetic (injected) keyboard/mouse events are ignored, so keystrokes produced by the typer
can never trigger a hotkey.
"""

import json
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import paho.mqtt.client as mqtt
from loguru import logger
from pynput import keyboard, mouse

from ghost_coder.gamepad import GAMEPAD_NAME, XInputPoller
from ghost_coder.utils import setup_logging, start_parent_watchdog, unique_client_id

SLOTS = range(1, 9)
RECORD_TIMEOUT = 15.0
GAMEPAD_POLL_INTERVAL = 0.01  # 100 Hz
REJECTED_MOUSE_BUTTONS = {"left"}  # a left-click hotkey would fire on every click


class InputSource(Enum):
    KEYBOARD = "keyboard"
    MOUSE = "mouse"
    GAMEPAD = "gamepad"


@dataclass
class Hotkey:
    slot: int
    source: InputSource
    value: str
    gamepad_name: Optional[str] = None


class Listener:
    def __init__(self, mqtt_host: str, mqtt_port: int):
        self._lock = threading.Lock()
        self._hotkeys: dict[int, Optional[Hotkey]] = {i: None for i in SLOTS}
        self._running = threading.Event()

        # Recording state
        self._recording_slot: Optional[int] = None
        self._recording_source: Optional[InputSource] = None
        self._recording_deadline = 0.0

        # Inputs currently held down; triggers fire on the press edge only.
        self._pressed: set[tuple[InputSource, str]] = set()
        # The input just used for registration must be released before it can trigger.
        self._just_registered: Optional[tuple[InputSource, str]] = None

        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, unique_client_id("listener"))
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None
        self._gamepads = XInputPoller()

    # ------------------------------------------------------------------ MQTT

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            logger.error(f"Listener failed to connect to MQTT broker: {reason_code}")
            return
        client.subscribe([("LISTENER", 1), ("APP", 1)])
        logger.info("Listener connected and subscribed to LISTENER, APP")
        self.emit({"event": "listener_ready"})

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        try:
            if message.topic == "APP":
                if "CLOSE" in (data.get("cmd"), data.get("command")):
                    self.stop()
                return
            cmd = data.get("cmd")
            if cmd == "register":
                self._start_recording(data.get("slot"), data.get("input"))
            elif cmd == "restore":
                self._restore(data)
            elif cmd == "unregister":
                self._clear(data.get("slot"))
            elif cmd == "cancel":
                self._cancel_recording(notify=True)
            # Messages with "event" are our own (or other clients') broadcasts: ignore.
        except Exception:
            logger.exception("Listener error handling message")

    def emit(self, data: dict[str, Any]) -> None:
        if self._client.is_connected():
            self._client.publish("LISTENER", json.dumps(data), qos=1)

    # -------------------------------------------------------- registration

    def _start_recording(self, slot: Any, source: Any) -> None:
        try:
            source_enum = InputSource(source)
        except ValueError:
            self.emit({"event": "hotkey_registration_error", "slot": slot, "error": f"Unknown input type '{source}'"})
            return
        if slot not in SLOTS:
            self.emit({"event": "hotkey_registration_error", "slot": slot, "error": f"Invalid slot {slot}"})
            return
        if source_enum == InputSource.GAMEPAD and not self._gamepads.connected():
            self.emit({"event": "hotkey_registration_error", "slot": slot, "source": source, "error": "No gamepad connected"})
            return
        with self._lock:
            self._recording_slot = slot
            self._recording_source = source_enum
            self._recording_deadline = time.monotonic() + RECORD_TIMEOUT
        logger.info(f"Recording {source} hotkey for slot {slot}")

    def _cancel_recording(self, notify: bool, reason: str = "cancelled") -> None:
        with self._lock:
            slot = self._recording_slot
            self._recording_slot = None
            self._recording_source = None
        if slot is not None and notify:
            self.emit({"event": "hotkey_registration_cancelled", "slot": slot, "reason": reason})
            logger.info(f"Hotkey recording for slot {slot} {reason}")

    def _check_recording_timeout(self) -> None:
        with self._lock:
            expired = self._recording_slot is not None and time.monotonic() > self._recording_deadline
        if expired:
            self._cancel_recording(notify=True, reason="timed out")

    def _clear(self, slot: Any) -> None:
        if slot in SLOTS:
            with self._lock:
                self._hotkeys[slot] = None
            logger.info(f"Cleared hotkey slot {slot}")

    def _restore(self, data: dict[str, Any]) -> None:
        slot, source, value = data.get("slot"), data.get("source"), data.get("value")
        gamepad_name = data.get("gamepad_name")
        error = None
        try:
            source_enum = InputSource(source)
        except ValueError:
            source_enum, error = None, f"Invalid input source '{source}'"
        if error is None and (slot not in SLOTS or not value):
            error = "Invalid saved hotkey"
        if error is None and source_enum == InputSource.GAMEPAD:
            pads = self._gamepads.connected()
            if gamepad_name and gamepad_name not in pads:
                error = f"Gamepad '{gamepad_name}' not connected"
            elif not pads:
                error = "No gamepad connected"
        if error:
            logger.warning(f"Cannot restore hotkey slot {slot}: {error}")
            self.emit({"event": "hotkey_restoration_error", "slot": slot, "source": source, "error": error})
            return
        with self._lock:
            self._hotkeys[slot] = Hotkey(slot, source_enum, str(value), gamepad_name)
        logger.info(f"Restored hotkey slot {slot}: {source} {value}")
        self.emit({"event": "hotkey_restoration_success", "slot": slot, "source": source, "value": value})

    # ------------------------------------------------------------ dispatch

    def _on_input(self, source: InputSource, value: str, pressed: bool, gamepad_name: Optional[str] = None,
                  momentary: bool = False) -> None:
        """Common handling for all inputs. momentary=True for events without a release (scroll)."""
        ident = (source, value)
        triggered: list[Hotkey] = []
        registered: Optional[dict[str, Any]] = None
        cancelled_slot = None
        rejected: Optional[dict[str, Any]] = None

        with self._lock:
            if not pressed:
                self._pressed.discard(ident)
                if self._just_registered == ident:
                    self._just_registered = None
                return

            if self._recording_slot is not None and source == InputSource.KEYBOARD and value == "esc":
                # ESC cancels any pending registration (keyboard, mouse or gamepad).
                cancelled_slot = self._recording_slot
                self._recording_slot = None
                self._recording_source = None
                self._pressed.add(ident)
            elif self._recording_slot is not None and self._recording_source == source:
                slot = self._recording_slot
                if source == InputSource.MOUSE and value in REJECTED_MOUSE_BUTTONS:
                    rejected = {"event": "hotkey_registration_error", "slot": slot, "source": source.value,
                                "error": "The left mouse button can't be used as a hotkey"}
                else:
                    self._hotkeys[slot] = Hotkey(slot, source, value, gamepad_name)
                    registered = {"event": "hotkey_registered", "slot": slot, "source": source.value, "value": value}
                    if gamepad_name:
                        registered["gamepad_name"] = gamepad_name
                    # Each input may only be bound to one slot.
                    for other, hk in self._hotkeys.items():
                        if other != slot and hk and hk.source == source and hk.value == value:
                            self._hotkeys[other] = None
                    self._just_registered = None if momentary else ident
                if rejected is None:
                    self._recording_slot = None
                    self._recording_source = None
                if not momentary:
                    self._pressed.add(ident)
            else:
                if ident in self._pressed or self._just_registered == ident:
                    return  # key auto-repeat or still holding the registration press
                if not momentary:
                    self._pressed.add(ident)
                for hk in self._hotkeys.values():
                    if hk and hk.source == source and hk.value == value and (
                            source != InputSource.GAMEPAD or not hk.gamepad_name or hk.gamepad_name == gamepad_name):
                        triggered.append(hk)

        if cancelled_slot is not None:
            self.emit({"event": "hotkey_registration_cancelled", "slot": cancelled_slot, "reason": "cancelled"})
        if rejected:
            self.emit(rejected)
        if registered:
            logger.info(f"Registered hotkey slot {registered['slot']}: {source.value} {value}")
            self.emit(registered)
        for hk in triggered:
            logger.info(f"Hotkey {hk.slot} triggered: {source.value} {value}")
            event = {"event": "hotkey_triggered", "slot": hk.slot, "source": source.value, "value": value}
            if hk.gamepad_name:
                event["gamepad_name"] = hk.gamepad_name
            self.emit(event)

    # ------------------------------------------------------------ keyboard

    @staticmethod
    def _format_key(key) -> str:
        char = getattr(key, "char", None)
        if char and char.isprintable():
            return char.lower()
        vk = getattr(key, "vk", None)
        if isinstance(key, keyboard.KeyCode) and vk is not None:
            # Keys pressed with Ctrl report control characters; fall back to the virtual key.
            if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
                return chr(vk).lower()
            return f"vk{vk}"
        return str(key).replace("Key.", "")

    def _on_key_press(self, key, injected=False):
        if not injected:
            self._on_input(InputSource.KEYBOARD, self._format_key(key), True)

    def _on_key_release(self, key, injected=False):
        if not injected:
            self._on_input(InputSource.KEYBOARD, self._format_key(key), False)

    # --------------------------------------------------------------- mouse

    def _on_click(self, _x, _y, button, pressed, injected=False):
        if not injected:
            self._on_input(InputSource.MOUSE, str(button).replace("Button.", ""), pressed)

    def _on_scroll(self, _x, _y, dx, dy, injected=False):
        if injected:
            return
        if dy:
            value = "scroll_up" if dy > 0 else "scroll_down"
        else:
            value = "scroll_right" if dx > 0 else "scroll_left"
        self._on_input(InputSource.MOUSE, value, True, momentary=True)

    # ------------------------------------------------------------- gamepad

    def _gamepad_loop(self) -> None:
        if not self._gamepads.available:
            logger.warning("XInput not available; gamepad hotkeys disabled")
            return
        while self._running.is_set():
            try:
                for value, pressed in self._gamepads.poll():
                    self._on_input(InputSource.GAMEPAD, value, pressed, GAMEPAD_NAME)
            except Exception:
                logger.exception("Gamepad polling error")
                time.sleep(1)
            time.sleep(GAMEPAD_POLL_INTERVAL)

    # ----------------------------------------------------------- lifecycle

    def start(self) -> bool:
        try:
            self._client.connect(self._mqtt_host, self._mqtt_port, keepalive=60)
        except Exception as e:
            logger.error(f"Listener failed to connect to MQTT broker: {e}")
            return False
        self._client.loop_start()
        self._running.set()

        self._keyboard_listener = keyboard.Listener(on_press=self._on_key_press, on_release=self._on_key_release)
        self._keyboard_listener.start()
        self._mouse_listener = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._mouse_listener.start()
        threading.Thread(target=self._gamepad_loop, name="gamepad", daemon=True).start()
        logger.info("Listener started")
        return True

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener:
                listener.stop()
        self._client.loop_stop()
        self._client.disconnect()
        logger.info("Listener stopped")

    def wait(self) -> None:
        while self._running.is_set():
            time.sleep(0.5)
            self._check_recording_timeout()


def listener_process(host: str, port: int, enable_logging: bool = False) -> None:
    """Entry point for the listener child process."""
    setup_logging(enable_logging)
    logger.info("Starting Listener process")
    listener = Listener(mqtt_host=host, mqtt_port=port)
    if not listener.start():
        return
    start_parent_watchdog(listener.stop)
    try:
        listener.wait()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
