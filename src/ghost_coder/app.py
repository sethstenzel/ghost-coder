"""Ghost Coder UI process: NiceGUI native window that controls the typer and listener processes."""

import argparse
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Optional

import paho.mqtt.client as mqtt
from loguru import logger
from nicegui import app, ui

from ghost_coder import APP_TITLE, __version__, winutil
from ghost_coder.broker import broker_process
from ghost_coder.listener import listener_process
from ghost_coder.typer import DEFAULT_SETTINGS, typer_process
from ghost_coder.utils import (
    config_dir, get_random_available_port, log_file, read_json, rotate_log_if_large, setup_logging,
    unique_client_id, wait_for_port, write_json,
)

ICON_FILE = Path(__file__).parent / "assets" / "icon.ico"
LEGACY_HOTKEYS_FILE = Path(__file__).parent / "hotkeys.json"

# Tall enough that the left-hand controls fit without scrolling (they need ~950 px).
DEFAULT_WINDOW_SIZE = (1400, 1000)

SLOT_NAMES = {1: "Play / Pause / Resume", 2: "Stop", 3: "Advance to Newline", 4: "Advance Token"}

# File extension -> CodeMirror language name (highlighting runs in the browser, not the UI event loop).
LANGUAGES = {
    ".py": "Python", ".pyw": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TSX", ".jsx": "JSX", ".java": "Java", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++",
    ".hpp": "C++", ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".json": "JSON", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML", ".md": "Markdown", ".sh": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".kt": "Kotlin", ".swift": "Swift", ".lua": "Lua",
}
PREVIEW_THEMES = {False: "vscodeLight", True: "vscodeDark"}

SETTINGS: dict[str, Any] = {**DEFAULT_SETTINGS, "dark_mode": False, "last_file": ""}

STATE: dict[str, Any] = {
    "play_status": "stopped",  # mirrored from the typer
    "source_file_path": "",
    "hotkeys": {slot: None for slot in SLOT_NAMES},
    "inactive_hotkeys": set(),  # saved hotkeys whose device is currently unavailable
    "progress": (0, 0),
    "countdown": 0,
    "target_title": "",
}

UI: dict[str, Any] = {}

mqtt_queue: "Queue[tuple[str, dict]]" = Queue()
mqtt_client: Optional[mqtt.Client] = None
CHILD_PROCESSES: dict[str, mp.Process] = {}
DEAD_PROCESSES_REPORTED: set[str] = set()
SHUTTING_DOWN = False


# ---------------------------------------------------------------- persistence

def settings_file() -> Path:
    return config_dir() / "settings.json"


def hotkeys_file() -> Path:
    return config_dir() / "hotkeys.json"


def load_settings() -> None:
    saved = read_json(settings_file(), {})
    if isinstance(saved, dict):
        for key, value in saved.items():
            if key in SETTINGS and value is not None:
                SETTINGS[key] = value


def save_settings() -> None:
    try:
        write_json(settings_file(), SETTINGS)
    except OSError as e:
        logger.error(f"Could not save settings: {e}")


def load_hotkeys() -> None:
    path = hotkeys_file()
    if not path.exists() and LEGACY_HOTKEYS_FILE.exists():
        # Earlier versions stored hotkeys inside the package directory.
        try:
            shutil.copyfile(LEGACY_HOTKEYS_FILE, path)
        except OSError:
            path = LEGACY_HOTKEYS_FILE
    saved = read_json(path, {})
    if not isinstance(saved, dict):
        return
    for slot_str, hotkey in saved.items():
        try:
            slot = int(slot_str)
        except ValueError:
            continue
        if slot in STATE["hotkeys"] and isinstance(hotkey, dict):
            STATE["hotkeys"][slot] = hotkey


def save_hotkeys() -> None:
    try:
        write_json(hotkeys_file(), {str(s): hk for s, hk in STATE["hotkeys"].items() if hk})
    except OSError as e:
        logger.error(f"Could not save hotkeys: {e}")


# ----------------------------------------------------------------------- MQTT

def setup_mqtt_client(host: str, port: int) -> None:
    global mqtt_client
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, unique_client_id("ui"))
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=5)
    try:
        mqtt_client.connect(host, port, keepalive=60)
    except Exception as e:
        logger.error(f"UI failed to connect to MQTT broker at {host}:{port}: {e}")
    mqtt_client.loop_start()


def on_mqtt_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code.is_failure:
        logger.error(f"UI failed to connect to MQTT broker: {reason_code}")
        return
    client.subscribe([("UI", 1), ("APP", 1), ("STATE", 1), ("LISTENER", 1)])
    logger.info("UI connected to MQTT broker")
    mqtt_queue.put(("_internal", {"event": "connected"}))


def on_mqtt_message(client, userdata, message):
    try:
        data = json.loads(message.payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning(f"UI ignoring non-JSON message on {message.topic}")
        return
    if isinstance(data, dict):
        mqtt_queue.put((message.topic, data))


def publish(topic: str, data: dict[str, Any]) -> bool:
    if mqtt_client and mqtt_client.is_connected():
        mqtt_client.publish(topic, json.dumps(data), qos=1)
        return True
    logger.warning(f"MQTT not connected; dropped message to {topic}: {data}")
    return False


def send_typer(cmd: str, **kwargs) -> bool:
    return publish("TYPER", {"cmd": cmd, **kwargs})


def publish_settings() -> None:
    publish("STATE", {"state-data": {k: SETTINGS[k] for k in DEFAULT_SETTINGS}})


def send_hotkeys_to_listener() -> None:
    for slot, hotkey in STATE["hotkeys"].items():
        if hotkey:
            publish("LISTENER", {"cmd": "restore", "slot": slot, "source": hotkey.get("source"),
                                 "value": hotkey.get("value"), "gamepad_name": hotkey.get("gamepad_name")})


# ------------------------------------------------------------- message pump

def check_mqtt_messages() -> None:
    while True:
        try:
            topic, data = mqtt_queue.get_nowait()
        except Empty:
            return
        try:
            handle_message(topic, data)
        except Exception:
            logger.exception(f"Error handling {topic} message: {data}")


def handle_message(topic: str, data: dict[str, Any]) -> None:
    global SHUTTING_DOWN
    if topic == "_internal":
        if data.get("event") == "connected":
            publish_settings()
            send_hotkeys_to_listener()
            if STATE["source_file_path"]:
                send_typer("load_file", file=STATE["source_file_path"])

    elif topic == "APP":
        if "CLOSE" in (data.get("cmd"), data.get("command")) and not SHUTTING_DOWN:
            logger.info("UI received CLOSE")
            SHUTTING_DOWN = True
            # Close the native window; NiceGUI then shuts the server down cleanly.
            app.native.main_window.destroy()

    elif topic == "UI":
        event = data.get("event")
        if "notify" in data:
            ui.notify(str(data["notify"]), type=data.get("type", "info"))
        elif event == "typer_ready":
            publish_settings()
            if STATE["source_file_path"]:
                send_typer("load_file", file=STATE["source_file_path"])
        elif event == "progress":
            STATE["progress"] = (int(data.get("done", 0)), int(data.get("total", 0)))
            update_status()
        elif event == "countdown":
            STATE["countdown"] = int(data.get("remaining", 0))
            update_status()
        elif event == "target":
            STATE["target_title"] = str(data.get("title", ""))
            update_status()

    elif topic == "STATE":
        if data.get("cmd") == "update_state" and data.get("key") == "play_status":
            STATE["play_status"] = data.get("value", "stopped")
            if STATE["play_status"] != "playing":
                STATE["countdown"] = 0
            update_status()

    elif topic == "LISTENER":
        handle_listener_event(data)


def handle_listener_event(data: dict[str, Any]) -> None:
    event = data.get("event")
    slot = data.get("slot")

    if event == "listener_ready":
        send_hotkeys_to_listener()
    elif event == "hotkey_triggered":
        handle_hotkey_trigger(slot)
    elif event == "hotkey_registered" and slot in STATE["hotkeys"]:
        source, value = data.get("source"), data.get("value")
        for other, hotkey in STATE["hotkeys"].items():
            if other != slot and hotkey and hotkey.get("source") == source and hotkey.get("value") == value:
                STATE["hotkeys"][other] = None
                ui.notify(f"Hotkey moved from '{SLOT_NAMES[other]}' to '{SLOT_NAMES[slot]}'")
        hotkey = {"source": source, "value": value}
        if data.get("gamepad_name"):
            hotkey["gamepad_name"] = data["gamepad_name"]
        STATE["hotkeys"][slot] = hotkey
        STATE["inactive_hotkeys"].discard(slot)
        save_hotkeys()
        close_recording_dialog()
        update_hotkey_labels()
        ui.notify(f"Hotkey set: {SLOT_NAMES[slot]} = {source} {value}", type="positive")
    elif event == "hotkey_registration_error":
        ui.notify(f"Hotkey not set: {data.get('error')}", type="negative")
        if "No gamepad" in str(data.get("error")):
            close_recording_dialog()
    elif event == "hotkey_registration_cancelled":
        close_recording_dialog()
        ui.notify(f"Hotkey registration {data.get('reason', 'cancelled')}")
    elif event == "hotkey_restoration_success" and slot in STATE["hotkeys"]:
        STATE["inactive_hotkeys"].discard(slot)
        update_hotkey_labels()
    elif event == "hotkey_restoration_error" and slot in STATE["hotkeys"]:
        # Keep the saved hotkey (e.g. gamepad unplugged) but show it as inactive.
        STATE["inactive_hotkeys"].add(slot)
        update_hotkey_labels()
        ui.notify(f"Hotkey '{SLOT_NAMES[slot]}' inactive: {data.get('error')}", type="warning")


# ----------------------------------------------------------------- playback

def require_file() -> bool:
    if not STATE["source_file_path"]:
        ui.notify("No source file is loaded", type="warning")
        return False
    return True


def toggle_playback() -> None:
    status = STATE["play_status"]
    if status == "playing":
        if send_typer("pause"):
            STATE["play_status"] = "paused"
    elif status == "paused":
        if send_typer("resume"):
            STATE["play_status"] = "playing"
    else:
        if not require_file():
            return
        refresh_preview()
        publish_settings()
        if send_typer("play"):
            STATE["play_status"] = "playing"
            delay = int(SETTINGS["start_delay"])
            if delay:
                ui.notify(f"Starting in {delay}s: click into the window you want to type into")
    update_status()


def stop_playback() -> None:
    if STATE["play_status"] == "stopped":
        ui.notify("Nothing is playing", type="warning")
        return
    if send_typer("stop"):
        STATE["play_status"] = "stopped"
        update_status()


def advance_newline() -> None:
    if require_file():
        send_typer("advance_newline")


def advance_token() -> None:
    if require_file():
        send_typer("advance_token")


def handle_hotkey_trigger(slot: Any) -> None:
    actions = {1: toggle_playback, 2: stop_playback, 3: advance_newline, 4: advance_token}
    action = actions.get(slot)
    if action:
        action()


# ------------------------------------------------------------------ files

def read_source(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        ui.notify("File is not UTF-8 text", type="negative")
    except OSError as e:
        ui.notify(f"Error reading file: {e}", type="negative")
    return None


def load_source_file(path: str, announce: bool = True) -> None:
    content = read_source(path)
    if content is None:
        return
    if STATE["play_status"] != "stopped":
        send_typer("stop")
    STATE["source_file_path"] = path
    STATE["progress"] = (0, 0)
    SETTINGS["last_file"] = path
    save_settings()
    if UI.get("file_input"):
        UI["file_input"].value = path
    show_preview(path, content)
    send_typer("load_file", file=path)
    update_status()
    if announce:
        ui.notify(f"Loaded {Path(path).name}")


def file_signature(path: str) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return os.path.abspath(path), st.st_mtime_ns, st.st_size


def show_preview(path: str, content: str) -> None:
    editor = UI.get("preview")
    if editor is None:
        return
    editor.set_language(LANGUAGES.get(Path(path).suffix.lower()))
    editor.set_value(content)
    editor.update()
    UI["preview_signature"] = file_signature(path)


def refresh_preview() -> None:
    """Re-read the source into the preview, unless the file is unchanged since it was last shown."""
    path = STATE["source_file_path"]
    if not path or (UI.get("preview") is not None and UI.get("preview_signature") == file_signature(path)):
        return
    content = read_source(path)
    if content is not None:
        show_preview(path, content)


def set_dark_mode(dark, value: bool) -> None:
    dark.set_value(value)
    set_setting("dark_mode", value)
    editor = UI.get("preview")
    if editor is not None:
        editor.set_theme(PREVIEW_THEMES[value])
        editor.update()


async def choose_file() -> None:
    start_dir = str(Path(STATE["source_file_path"]).parent) if STATE["source_file_path"] else ""
    try:
        result = await app.native.main_window.create_file_dialog(directory=start_dir, allow_multiple=False)
    except Exception as e:
        ui.notify(f"Could not open file dialog: {e}", type="negative")
        return
    if result:
        load_source_file(result[0])


def open_source_folder() -> None:
    if not require_file():
        return
    path = os.path.normpath(STATE["source_file_path"])
    try:
        subprocess.Popen(f'explorer /select,"{path}"')
    except OSError as e:
        ui.notify(f"Error opening folder: {e}", type="negative")


def open_in_editor() -> None:
    if not require_file():
        return
    path = STATE["source_file_path"]
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    code = shutil.which("code")
    try:
        if code:
            subprocess.Popen([code, path], creationflags=no_window)
            ui.notify("Opened in VS Code")
        else:
            subprocess.Popen(["notepad.exe", path])
            ui.notify("Opened in Notepad (VS Code not found)")
    except OSError as e:
        ui.notify(f"Error opening editor: {e}", type="negative")


# --------------------------------------------------------------- settings UI

def set_setting(key: str, value: Any) -> None:
    SETTINGS[key] = value
    save_settings()
    if key in DEFAULT_SETTINGS:
        publish_settings()


def setting_checkbox(label: str, key: str, tooltip: str) -> None:
    ui.checkbox(label, value=bool(SETTINGS[key]),
                on_change=lambda e: set_setting(key, bool(e.value))).tooltip(tooltip)


# ---------------------------------------------------------------- hotkeys UI

def show_hotkey_dialog(slot: int) -> None:
    with ui.dialog() as dialog, ui.card().classes("min-w-[320px]"):
        ui.label(f"Hotkey for {SLOT_NAMES[slot]}").classes("text-lg font-bold")
        ui.separator()
        with ui.column().classes("w-full gap-2"):
            for source, icon in (("keyboard", "keyboard"), ("mouse", "mouse"), ("gamepad", "sports_esports")):
                ui.button(f"{source.capitalize()} hotkey", icon=icon,
                          on_click=lambda s=source: (dialog.close(), start_recording(slot, s))).classes("w-full")
            ui.button("Clear hotkey", icon="clear", color="negative",
                      on_click=lambda: (dialog.close(), clear_hotkey(slot))).classes("w-full")
            ui.button("Cancel", icon="close", on_click=dialog.close).props("flat").classes("w-full")
    dialog.open()


def start_recording(slot: int, source: str) -> None:
    if not publish("LISTENER", {"cmd": "register", "slot": slot, "input": source}):
        ui.notify("Internal message broker not connected", type="negative")
        return
    prompts = {"keyboard": "Press a key", "mouse": "Click a mouse button (not left) or scroll",
               "gamepad": "Press a gamepad button, D-pad direction or trigger"}
    with ui.dialog().props("persistent") as dialog, ui.card().classes("items-center"):
        ui.spinner(size="lg")
        ui.label(f"{prompts[source]} for '{SLOT_NAMES[slot]}'").classes("text-lg")
        ui.label("Press ESC to cancel (times out after 15 s)").classes("text-sm opacity-70")
        ui.button("Cancel", on_click=lambda: publish("LISTENER", {"cmd": "cancel"})).props("flat")
    UI["recording_dialog"] = dialog
    dialog.open()


def close_recording_dialog() -> None:
    dialog = UI.pop("recording_dialog", None)
    if dialog:
        dialog.close()
        dialog.delete()


def clear_hotkey(slot: int) -> None:
    publish("LISTENER", {"cmd": "unregister", "slot": slot})
    STATE["hotkeys"][slot] = None
    STATE["inactive_hotkeys"].discard(slot)
    save_hotkeys()
    update_hotkey_labels()
    ui.notify(f"Hotkey for '{SLOT_NAMES[slot]}' cleared")


def describe_hotkey(hotkey: dict[str, Any]) -> str:
    source = str(hotkey.get("source", "")).capitalize()
    return f"{source}: {hotkey.get('value', '')}"


def update_hotkey_labels() -> None:
    for slot, name in SLOT_NAMES.items():
        label = UI.get(f"hotkey_label_{slot}")
        button = UI.get(f"button_{slot}")
        hotkey = STATE["hotkeys"].get(slot)
        inactive = slot in STATE["inactive_hotkeys"]
        if label:
            if hotkey:
                suffix = " (device not connected)" if inactive else ""
                label.set_text(f"{name}: {describe_hotkey(hotkey)}{suffix}")
            else:
                label.set_text(f"{name}: not set")
        if button:
            color = "orange" if inactive else "#21ba45"
            button.style(f"outline: 2px solid {color}; outline-offset: 2px" if hotkey else "outline: none")


# ----------------------------------------------------------------- status UI

def update_status() -> None:
    status = STATE["play_status"]
    play_button = UI.get("button_1")
    if play_button:
        if status == "playing":
            play_button.props("icon=pause")
            play_button.text = "PAUSE"
        else:
            play_button.props("icon=play_arrow")
            play_button.text = "RESUME" if status == "paused" else "PLAY"

    label = UI.get("status_label")
    if label:
        if status == "playing" and STATE["countdown"] > 0:
            text = f"Starting in {STATE['countdown']}s… click into your target window"
        elif status == "stopped":
            text = "Stopped"
        else:
            target = STATE["target_title"]
            text = status.capitalize() + (f" → {target}" if target else "")
        label.set_text(text)

    done, total = STATE["progress"]
    if UI.get("progress_bar"):
        UI["progress_bar"].set_value(done / total if total else 0)
    if UI.get("progress_label"):
        UI["progress_label"].set_text(f"{done} / {total} tokens" if total else "")


# ------------------------------------------------------------------- layout

def build_ui() -> None:
    """Root page; NiceGUI calls this for every client connection."""
    dark = ui.dark_mode(value=bool(SETTINGS["dark_mode"]))
    ui.timer(0.05, check_mqtt_messages)
    ui.timer(1.0, monitor_processes)

    with ui.row().classes("w-full no-wrap gap-0"):
        with ui.column().classes("p-2 gap-1").style("width: 45%; min-width: 520px"):
            ui.label("How to use").classes("font-bold text-xl")
            ui.label("1. Select the source file to play back")
            ui.label("2. Adjust playback speed and options")
            ui.label("3. Right-click a control button to assign a global hotkey")
            ui.label("4. Press Play, then click into your editor before the countdown ends")
            ui.separator().classes("my-2")

            ui.label("Source file").classes("font-bold")
            UI["file_input"] = ui.input(value=STATE["source_file_path"]).props("readonly dense").classes("w-full")
            with ui.row().classes("gap-2"):
                ui.button("Select File", icon="file_open", on_click=choose_file)
                ui.button("Open Folder", icon="folder_open", on_click=open_source_folder).props("outline")
                ui.button("Open in Editor", icon="edit", on_click=open_in_editor).props("outline")
                ui.button("Reload", icon="refresh", on_click=lambda: STATE["source_file_path"] and load_source_file(
                    STATE["source_file_path"])).props("outline").tooltip("Re-read the file from disk")
            ui.separator().classes("my-2")

            speed_label = ui.label().classes("font-bold")

            def on_speed(e):
                speed_label.set_text(f"Typing speed: {int(e.value)} ms per keystroke")
                set_setting("typing_speed_value", int(e.value))

            ui.slider(min=10, max=500, step=10, value=int(SETTINGS["typing_speed_value"]), on_change=on_speed)
            speed_label.set_text(f"Typing speed: {int(SETTINGS['typing_speed_value'])} ms per keystroke")

            delay_label = ui.label().classes("font-bold")

            def on_delay(e):
                delay_label.set_text(f"Start delay: {int(e.value)} s")
                set_setting("start_delay", int(e.value))

            ui.slider(min=0, max=10, step=1, value=int(SETTINGS["start_delay"]), on_change=on_delay)
            delay_label.set_text(f"Start delay: {int(SETTINGS['start_delay'])} s")

            with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-0"):
                setting_checkbox("Varied typing speed", "varied_coding_speed",
                                 "Add random jitter (-50 to +150 ms) to each keystroke")
                setting_checkbox("Start playback paused", "start_playback_paused",
                                 "Capture the target window, then wait for Resume / Advance")
                setting_checkbox("Auto pause on new line", "pause_on_new_line",
                                 "Pause after every line break")
                setting_checkbox("Replace leading 4 spaces with Tab", "replace_quad_spaces_with_tab",
                                 "Type indentation as Tab keys (lets the editor apply its own indent style)")
                setting_checkbox("Auto Home on new line", "auto_home_on_newline",
                                 "Press Home after each line break to skip editor auto-indent")
                setting_checkbox("Ctrl+Enter for new lines", "control_on_newline",
                                 "Use Ctrl+Enter (insert line below) instead of Enter; avoids auto-closed brackets")
                setting_checkbox("Pause when target loses focus", "pause_on_window_not_focused",
                                 "Pause if another window becomes active; otherwise focus is taken back")
                setting_checkbox("Refocus target on resume", "refocus_window_on_resume",
                                 "Bring the target window to the front when resuming")
            ui.separator().classes("my-2")

            with ui.row().classes("gap-2"):
                buttons = {
                    1: ui.button("PLAY", icon="play_arrow", on_click=toggle_playback),
                    2: ui.button("STOP", icon="stop", on_click=stop_playback, color="negative"),
                    3: ui.button("ADV. NEWLINE", icon="keyboard_return", on_click=advance_newline),
                    4: ui.button("ADV. TOKEN", icon="fast_forward", on_click=advance_token),
                }
                for slot, button in buttons.items():
                    button.on("contextmenu.prevent", lambda _, s=slot: show_hotkey_dialog(s))
                    button.tooltip("Right-click to assign a hotkey")
                    UI[f"button_{slot}"] = button

            with ui.row().classes("w-full items-center gap-2 mt-2"):
                ui.label("Status:").classes("font-bold")
                UI["status_label"] = ui.label()
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                UI["progress_bar"] = ui.linear_progress(value=0, show_value=False).classes("grow")
                UI["progress_label"] = ui.label().classes("text-sm whitespace-nowrap")

            ui.separator().classes("my-2")
            ui.label("Hotkeys (right-click a button to set)").classes("font-bold")
            with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-0"):
                for slot in SLOT_NAMES:
                    UI[f"hotkey_label_{slot}"] = ui.label().classes("text-sm")

            ui.separator().classes("my-2")
            with ui.row().classes("items-center gap-4"):
                ui.switch("Dark mode", value=bool(SETTINGS["dark_mode"]),
                          on_change=lambda e: set_dark_mode(dark, bool(e.value)))
                ui.label(f"v{__version__}").classes("text-xs opacity-60")

        with ui.column().classes("p-2 pl-4 gap-2").style(
                "width: 55%; height: calc(100vh - 2rem); border-left: 2px solid #8884"):
            ui.label("Source preview").classes("font-bold text-xl")
            UI["preview"] = ui.codemirror(theme=PREVIEW_THEMES[bool(SETTINGS["dark_mode"])]).classes(
                "w-full grow").style("height: auto; min-height: 0; font-size: 12px")
            UI["preview"].disable()  # read-only
            UI["preview_signature"] = None

    update_hotkey_labels()
    update_status()
    if STATE["source_file_path"]:
        refresh_preview()


# --------------------------------------------------------------- processes

def monitor_processes() -> None:
    if SHUTTING_DOWN:
        return
    for name, proc in CHILD_PROCESSES.items():
        if not proc.is_alive() and name not in DEAD_PROCESSES_REPORTED:
            DEAD_PROCESSES_REPORTED.add(name)
            logger.error(f"{name} process exited (code {proc.exitcode})")
            ui.notify(f"Internal {name} process stopped unexpectedly (exit code {proc.exitcode}). "
                      f"Please restart Ghost Coder.", type="negative", timeout=0, close_button=True)


def on_shutdown() -> None:
    global SHUTTING_DOWN
    SHUTTING_DOWN = True
    if mqtt_client:
        if mqtt_client.is_connected():
            info = mqtt_client.publish("APP", json.dumps({"command": "CLOSE"}), qos=1)
            try:
                info.wait_for_publish(timeout=1.0)
            except Exception:
                pass
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    for name, proc in CHILD_PROCESSES.items():
        proc.join(timeout=2)
        if proc.is_alive():
            logger.info(f"Terminating {name} process")
            proc.terminate()
            proc.join(timeout=2)


def initial_window_geometry() -> tuple[int, int, Optional[int], Optional[int]]:
    """Default (width, height, x, y): shrunk to fit and centered in the primary monitor's work area."""
    width, height = DEFAULT_WINDOW_SIZE
    area = winutil.get_work_area()
    if not area:
        return width, height, None, None
    left, top, area_w, area_h = area
    width, height = min(width, area_w), min(height, area_h)
    return width, height, left + (area_w - width) // 2, top + (area_h - height) // 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ghost-coder", description="Ghost Coder - automated code playback")
    parser.add_argument("--port", type=int, help="port for the internal MQTT broker (default: random free port)")
    parser.add_argument("--logging", action="store_true", help=f"write debug logs to {log_file()} and the console")
    parser.add_argument("--extbroker", "--extmqtt", dest="extbroker", metavar="HOST:PORT",
                        help="use an external MQTT broker instead of the internal one")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args, _unknown = parser.parse_known_args()
    return args


def main() -> None:
    mp.freeze_support()
    args = parse_args()

    if args.logging:
        rotate_log_if_large()
    setup_logging(args.logging)
    logger.info(f"Starting {APP_TITLE}")

    if args.extbroker:
        host, _, port_str = args.extbroker.rpartition(":")
        if not host or not port_str.isdigit():
            print("Error: --extbroker must be HOST:PORT (e.g. 127.0.0.1:1883)", file=sys.stderr)
            sys.exit(2)
        broker_host, broker_port = host, int(port_str)
    else:
        broker_host = "127.0.0.1"
        broker_port = args.port or get_random_available_port()

    if not args.extbroker:
        CHILD_PROCESSES["broker"] = mp.Process(target=broker_process, args=(broker_host, broker_port, args.logging),
                                               name="broker", daemon=True)
        CHILD_PROCESSES["broker"].start()

    broker = CHILD_PROCESSES.get("broker")
    if not wait_for_port(broker_host, broker_port, timeout=15, alive=lambda: broker is None or broker.is_alive()):
        logger.error(f"MQTT broker at {broker_host}:{broker_port} is not reachable")
        print(f"Error: MQTT broker at {broker_host}:{broker_port} is not reachable", file=sys.stderr)
        for proc in CHILD_PROCESSES.values():
            proc.terminate()
        sys.exit(1)

    for name, target in (("listener", listener_process), ("typer", typer_process)):
        CHILD_PROCESSES[name] = mp.Process(target=target, args=(broker_host, broker_port, args.logging),
                                           name=name, daemon=True)
        CHILD_PROCESSES[name].start()

    load_settings()
    load_hotkeys()
    last_file = SETTINGS.get("last_file")
    if last_file and Path(last_file).is_file():
        STATE["source_file_path"] = last_file

    setup_mqtt_client(broker_host, broker_port)
    app.on_shutdown(on_shutdown)

    width, height, x, y = initial_window_geometry()
    if x is not None:
        # window_args don't reach NiceGUI's window process, so position it once it exists.
        app.on_startup(lambda: app.native.main_window.move(x, y))

    ui.run(
        build_ui,
        title=APP_TITLE,
        native=True,
        window_size=(width, height),
        reload=False,
        show_welcome_message=False,
        favicon=ICON_FILE if ICON_FILE.exists() else None,
    )


if __name__ == "__main__":
    main()
