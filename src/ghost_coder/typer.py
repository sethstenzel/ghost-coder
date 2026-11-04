"""
Typer process: replays a parsed source file as simulated keystrokes into a target window.

MQTT interface
  TYPER (in)   {"cmd": "load_file", "file": path} | play | pause | resume | stop |
               advance_newline | advance_token
  STATE (in)   {"state-data": {...settings...}}
  STATE (out)  {"cmd": "update_state", "key": "play_status", "value": "playing|paused|stopped"}
  UI    (out)  {"notify": str, "type": str} | {"event": "progress"|"countdown"|"target"|"typer_ready", ...}
  APP   (in)   {"command": "CLOSE"}

The typer is the single source of truth for playback status; the UI sends commands and mirrors
the status it reports back.
"""

import json
import random
import threading
import time
from typing import Any, Optional

import paho.mqtt.client as mqtt
from loguru import logger
from pynput.keyboard import Controller as KbController
from pynput.keyboard import Key
from pynput.mouse import Controller as MsController

from ghost_coder import APP_TITLE, winutil
from ghost_coder.data import (
    KeyCombo, KeyPress, MouseScroll, Newline, PausePoint, Text, TimedPause, Token, describe, parse,
)
from ghost_coder.utils import setup_logging, start_parent_watchdog, unique_client_id

DEFAULT_SETTINGS: dict[str, Any] = {
    "typing_speed_value": 50,
    "varied_coding_speed": False,
    "start_delay": 5,
    "pause_on_new_line": False,
    "start_playback_paused": False,
    "auto_home_on_newline": True,
    "control_on_newline": True,
    "replace_quad_spaces_with_tab": True,
    "pause_on_window_not_focused": True,
    "refocus_window_on_resume": True,
}

PROGRESS_INTERVAL = 0.2


class Cancelled(Exception):
    """Raised inside the playback thread when its session was stopped or replaced."""


class Typer:
    def __init__(self, mqtt_host: str, mqtt_port: int):
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)

        self.settings: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.file_path: Optional[str] = None

        # Playback state (guarded by _lock)
        self.status = "stopped"
        self._session = 0
        self._advance_newline = False
        self._advance_tokens = 0
        self._refocus_pending = False
        self.hwnd = 0
        self._last_progress = 0.0

        self.kb = KbController()
        self.ms = MsController()

        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, unique_client_id("typer"))
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._running = threading.Event()

    # ------------------------------------------------------------------ MQTT

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            logger.error(f"Typer failed to connect to MQTT broker: {reason_code}")
            return
        client.subscribe([("TYPER", 1), ("STATE", 1), ("APP", 1)])
        logger.info("Typer connected and subscribed to TYPER, STATE, APP")
        self.publish("UI", {"event": "typer_ready"})

    def _on_message(self, client, userdata, message):
        try:
            data = json.loads(message.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning(f"Typer ignoring non-JSON message on {message.topic}")
            return
        if not isinstance(data, dict):
            return
        try:
            if message.topic == "APP":
                if "CLOSE" in (data.get("cmd"), data.get("command")):
                    logger.info("Typer received CLOSE")
                    self.stop()
            elif message.topic == "STATE":
                if isinstance(data.get("state-data"), dict):
                    self._apply_settings(data["state-data"])
            elif message.topic == "TYPER":
                self._handle_command(data)
        except Exception:
            logger.exception("Typer error handling message")

    def publish(self, topic: str, data: dict[str, Any]) -> None:
        if self._client.is_connected():
            self._client.publish(topic, json.dumps(data), qos=1)

    def notify(self, message: str, kind: str = "info") -> None:
        logger.info(f"Notify UI: {message}")
        self.publish("UI", {"notify": message, "type": kind})

    # -------------------------------------------------------------- settings

    def _apply_settings(self, state: dict[str, Any]) -> None:
        with self._lock:
            for key in DEFAULT_SETTINGS:
                if key in state and state[key] is not None:
                    self.settings[key] = state[key]
        logger.debug(f"Typer settings: {self.settings}")

    def _speed_ms(self) -> float:
        base = max(10, int(self.settings["typing_speed_value"]))
        if self.settings["varied_coding_speed"]:
            return max(10, base + random.randint(-50, 150))
        return base

    # -------------------------------------------------------------- commands

    def _handle_command(self, data: dict[str, Any]) -> None:
        cmd = data.get("cmd")
        if cmd == "load_file":
            self._load_file(data.get("file"))
        elif cmd == "play":
            self._play()
        elif cmd == "pause":
            self._pause()
        elif cmd == "resume":
            self._resume()
        elif cmd == "stop":
            self._stop_playback()
        elif cmd == "advance_newline":
            self._advance(newline=True)
        elif cmd == "advance_token":
            self._advance(newline=False)
        else:
            logger.warning(f"Typer unknown command: {cmd}")

    def _read_tokens(self, path: str) -> list[Token]:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        return parse(text, replace_quad_spaces_with_tab=bool(self.settings["replace_quad_spaces_with_tab"]))

    def _load_file(self, path: Optional[str]) -> None:
        if not path:
            self.notify("Load failed: no file given", "negative")
            return
        try:
            tokens = self._read_tokens(path)
        except Exception as e:
            self.notify(f"Failed to load file: {e}", "negative")
            return
        self._stop_playback(quiet=True)
        with self._lock:
            self.file_path = path
        self.publish("UI", {"event": "progress", "done": 0, "total": len(tokens)})
        logger.info(f"Loaded {path}: {len(tokens)} tokens")

    def _set_status(self, status: str, session: Optional[int] = None) -> bool:
        """Change status and report it. If session is given, only apply while it is current."""
        with self._lock:
            if session is not None and session != self._session:
                return False
            changed = self.status != status
            self.status = status
            if status != "paused":
                self._advance_newline = False
                self._advance_tokens = 0
            self._wake.notify_all()
        if changed:
            self.publish("STATE", {"cmd": "update_state", "key": "play_status", "value": status})
            logger.info(f"Playback status -> {status}")
        return True

    def _play(self) -> None:
        with self._lock:
            if self.status == "paused":
                self._resume()
                return
            if self.status == "playing":
                return
            if not self.file_path:
                self.notify("No source file is loaded", "warning")
                self.publish("STATE", {"cmd": "update_state", "key": "play_status", "value": "stopped"})
                return
            self._session += 1
            session = self._session
            self._advance_newline = False
            self._advance_tokens = 0
        self._set_status("playing")
        threading.Thread(target=self._run_session, args=(session,), name=f"playback-{session}", daemon=True).start()

    def _pause(self) -> None:
        with self._lock:
            if self.status == "playing":
                self._set_status("paused")

    def _resume(self) -> None:
        with self._lock:
            if self.status != "paused":
                return
            self._refocus_pending = bool(self.settings["refocus_window_on_resume"])
            self._set_status("playing")

    def _stop_playback(self, quiet: bool = False) -> None:
        with self._lock:
            was_running = self.status != "stopped"
            self._session += 1  # invalidates any running playback thread
            self._set_status("stopped")
        if was_running and not quiet:
            self.notify("Playback stopped")

    def _advance(self, newline: bool) -> None:
        with self._lock:
            if self.status == "stopped":
                self.notify("Playback has not started", "warning")
                return
            if newline:
                # While playing this means "pause at the end of the current line".
                self._advance_newline = True
            elif self.status == "paused":
                self._advance_tokens += 1
            self._wake.notify_all()

    # -------------------------------------------------------------- playback

    def _check(self, session: int) -> None:
        if session != self._session:
            raise Cancelled()

    def _sleep(self, session: int, seconds: float) -> None:
        """Interruptible sleep."""
        end = time.monotonic() + seconds
        while True:
            self._check(session)
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def _gate(self, session: int) -> None:
        """Block until the next keystroke may be sent to the target window."""
        warned_focus = False
        while True:
            with self._lock:
                self._check(session)
                paused = self.status == "paused"
                advancing = self._advance_newline or self._advance_tokens > 0
                refocus = self._refocus_pending
                self._refocus_pending = False
                if paused and not advancing:
                    self._wake.wait(0.1)
                    continue
                pause_on_blur = bool(self.settings["pause_on_window_not_focused"])

            if not winutil.is_window(self.hwnd):
                self.notify("Target window was closed; playback stopped", "warning")
                self._stop_playback(quiet=True)
                raise Cancelled()

            if winutil.get_foreground_window() == self.hwnd:
                return

            # Target is not focused.
            if refocus or paused or not pause_on_blur:
                # Resuming / stepping / focus-pausing disabled: bring the target back.
                if winutil.focus_window(self.hwnd):
                    time.sleep(0.15)  # let the window settle before typing
                    continue
                if not warned_focus:
                    self.notify("Could not focus the target window; click into it to continue", "warning")
                    warned_focus = True
                if paused:
                    with self._lock:
                        self._advance_newline = False
                        self._advance_tokens = 0
                time.sleep(0.2)
                continue

            if self._set_status("paused", session):
                self.notify("Paused: target window lost focus", "warning")

    def _tap(self, key, hold: float = 0.02) -> None:
        self.kb.press(key)
        try:
            time.sleep(hold)
        finally:
            self.kb.release(key)

    def _type_char(self, session: int, char: str) -> None:
        self._gate(session)
        key = Key.tab if char == "\t" else char
        speed = self._speed_ms() / 1000
        self._tap(key, hold=min(speed / 3, 0.03))
        self._sleep(session, speed * 2 / 3)

    def _press_named(self, session: int, name: str, count: int = 1) -> None:
        key = getattr(Key, name)
        for _ in range(count):
            self._gate(session)
            speed = self._speed_ms() / 1000
            self._tap(key, hold=min(speed / 3, 0.03))
            self._sleep(session, speed * 2 / 3)

    def _combo(self, session: int, keys: tuple[str, ...]) -> None:
        self._gate(session)
        resolved = [getattr(Key, k) if len(k) > 1 else k for k in keys]
        pressed = []
        try:
            for k in resolved:
                self.kb.press(k)
                pressed.append(k)
            time.sleep(0.03)
        finally:
            for k in reversed(pressed):
                self.kb.release(k)
        self._sleep(session, self._speed_ms() / 1000)

    def _newline(self, session: int) -> None:
        self._gate(session)
        speed = self._speed_ms() / 1000
        if self.settings["control_on_newline"]:
            self._combo(session, ("ctrl", "enter"))
        else:
            self._tap(Key.enter)
            self._sleep(session, speed)
        if self.settings["auto_home_on_newline"]:
            self._tap(Key.home)
            self._sleep(session, speed / 2)
        with self._lock:
            self._check(session)
            if self._advance_newline:
                self._advance_newline = False
                self._set_status("paused")
            elif self.settings["pause_on_new_line"] and self.status == "playing":
                self._set_status("paused")

    def _execute(self, session: int, token: Token) -> None:
        if isinstance(token, Text):
            for char in token.text:
                self._type_char(session, char)
        elif isinstance(token, Newline):
            self._newline(session)
        elif isinstance(token, KeyPress):
            self._press_named(session, token.key, token.count)
        elif isinstance(token, KeyCombo):
            self._combo(session, token.keys)
        elif isinstance(token, MouseScroll):
            for _ in range(token.count):
                self._gate(session)
                self.ms.scroll(0, token.direction)
                self._sleep(session, max(0.05, self._speed_ms() / 2000))
        elif isinstance(token, TimedPause):
            self._gate(session)
            self._sleep(session, token.seconds)
        elif isinstance(token, PausePoint):
            with self._lock:
                self._check(session)
                self._advance_newline = False
                self._advance_tokens = 0
                self._set_status("paused")
            self.notify("Paused at <<PAUSE>> marker")

    def _report_progress(self, done: int, total: int, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_progress >= PROGRESS_INTERVAL:
            self._last_progress = now
            self.publish("UI", {"event": "progress", "done": done, "total": total})

    def _countdown(self, session: int) -> None:
        delay = max(0, int(self.settings.get("start_delay", 5)))
        for remaining in range(delay, 0, -1):
            self.publish("UI", {"event": "countdown", "remaining": remaining})
            self._sleep(session, 1)
        self.publish("UI", {"event": "countdown", "remaining": 0})

    def _capture_target(self) -> bool:
        hwnd = winutil.get_foreground_window()
        title = winutil.get_window_title(hwnd)
        if not hwnd or title == APP_TITLE:
            self.notify("No target window: click into your editor before playback starts", "warning")
            return False
        self.hwnd = hwnd
        logger.info(f"Target window: '{title}' (hwnd {hwnd})")
        self.publish("UI", {"event": "target", "title": title})
        return True

    def _run_session(self, session: int) -> None:
        try:
            tokens = self._read_tokens(self.file_path)
        except Exception as e:
            self.notify(f"Failed to read file: {e}", "negative")
            self._set_status("stopped", session)
            return

        total = len(tokens)
        try:
            self._report_progress(0, total, force=True)
            self._countdown(session)
            self._check(session)
            if not self._capture_target():
                self._set_status("stopped", session)
                return
            if self.settings["start_playback_paused"]:
                self._set_status("paused", session)

            for index, token in enumerate(tokens):
                self._execute(session, token)
                with self._lock:
                    self._check(session)
                    if self.status == "paused" and self._advance_tokens > 0:
                        self._advance_tokens -= 1
                logger.debug(f"Typed token {index + 1}/{total}: {describe(token)}")
                self._report_progress(index + 1, total)

            self._report_progress(total, total, force=True)
            if self._set_status("stopped", session):
                self.notify("Playback finished", "positive")
        except Cancelled:
            logger.info(f"Playback session {session} cancelled")
        except Exception as e:
            logger.exception("Playback error")
            self.notify(f"Playback error: {e}", "negative")
            self._set_status("stopped", session)

    # ------------------------------------------------------------- lifecycle

    def start(self) -> bool:
        try:
            self._client.connect(self._mqtt_host, self._mqtt_port, keepalive=60)
        except Exception as e:
            logger.error(f"Typer failed to connect to MQTT broker: {e}")
            return False
        self._client.loop_start()
        self._running.set()
        return True

    def stop(self) -> None:
        with self._lock:
            self._session += 1
            self._wake.notify_all()
        if self._running.is_set():
            self._running.clear()
            self._client.loop_stop()
            self._client.disconnect()
            logger.info("Typer stopped")

    def wait(self) -> None:
        while self._running.is_set():
            time.sleep(0.5)


def typer_process(host: str, port: int, enable_logging: bool = False) -> None:
    """Entry point for the typer child process."""
    setup_logging(enable_logging)
    logger.info("Starting Typer process")
    typer = Typer(mqtt_host=host, mqtt_port=port)
    if not typer.start():
        return
    start_parent_watchdog(typer.stop)
    try:
        typer.wait()
    except KeyboardInterrupt:
        pass
    finally:
        typer.stop()
