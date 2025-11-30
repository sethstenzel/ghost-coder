import json
import multiprocessing as mp
import argparse
import time
import os
import subprocess
from pathlib import Path
from loguru import logger
from nicegui import ui, app
from queue import Queue
import paho.mqtt.client as Client
from ghost_coder.utils import get_random_available_port
from ghost_coder.broker import broker_process
from ghost_coder.listener import listener_process
from ghost_coder.typer import typer_process


APP_VERSION = "0.1.0"

# Path to hotkeys configuration file
HOTKEYS_FILE = Path(__file__).parent / "hotkeys.json"

UI_ELEMENTS = {
    'source_file_path_field': None,
    'select_source_file_button': None,
    'open_folder_button': None,
    'open_in_editor_button': None,
    'play_button': None,
    'stop_button': None,
    'advance_to_next_newline_button': None,
    'advance_to_next_token_button': None,
    'typing_speed_value': 50,
    'typing_speed_label': None,
    'pause_on_new_line_chkbx': None,
    'start_playback_paused': None,
    'auto_home_on_newline': None,
    'control_on_newline': None,
    'replace_quad_spaces_with_tab': None,
    'pause_on_window_not_focused': None,
    'refocus_window_on_resume': None,
    'source_code_display': None,
    'hotkey_labels': {
        1: None,  # play_button label
        2: None,  # stop_button label
        3: None,  # advance_to_next_newline_button label
        4: None   # advance_to_next_token_button label
    }
}

APP_STATE = {
    'play_status': 'stopped', # playing, paused, stopped
    'typing_speed_value': 50, # 50 - 500
    'pause_on_new_line': False,
    'start_playback_paused': False,
    'auto_home_on_newline': True,
    'control_on_newline': True,
    'replace_quad_spaces_with_tab': True,
    'pause_on_window_not_focused': True,
    'refocus_window_on_resume': True,
    'varied_coding_speed': False,
    'source_file_path': '',
    'loaded_file_data': '',
    'loaded_file_parsed_data': '',
    'hotkeys': {
        1: None,  # play_button
        2: None,  # stop_button
        3: None,  # advance_to_next_newline_button
        4: None   # advance_to_next_token_button
    }
}

# MQTT setup
mqtt_queue = Queue()
mqtt_client = None

def setup_mqtt_client(host, port):
    """Initialize and connect the MQTT client."""
    global mqtt_client

    mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION2, "ui_client")
    mqtt_client.user_data_set({"host": host, "port": port})
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect(host, port, keepalive=60)
        mqtt_client.loop_start()
        logger.info(f"UI MQTT client connecting to {host}:{port}")
    except Exception as e:
        logger.error(f"Failed to connect UI MQTT client: {e}")

def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    """Callback when MQTT client connects to broker."""
    if rc == 0:
        logger.info(f"UI connected to MQTT broker at {userdata['host']}:{userdata['port']}")
        client.subscribe([("UI", 1), ("APP", 1), ("STATE", 1), ("LISTENER", 1)])
        logger.info("UI subscribed to topics: UI, APP, STATE, LISTENER")
    else:
        logger.error(f"UI failed to connect to MQTT broker, return code: {rc}")

def on_mqtt_message(client, userdata, message):
    """Callback when MQTT message is received."""
    mqtt_queue.put((message.topic, message.payload.decode()))
    logger.debug(f"UI received message - Topic: {message.topic}, Payload: {message.payload.decode()}")

def save_hotkeys():
    """Save current hotkeys to hotkeys.json file."""
    try:
        # Filter out None values before saving
        hotkeys_to_save = {
            str(slot): hotkey_data
            for slot, hotkey_data in APP_STATE['hotkeys'].items()
            if hotkey_data is not None
        }

        with open(HOTKEYS_FILE, 'w') as f:
            json.dump(hotkeys_to_save, f, indent=2)

        logger.info(f"Hotkeys saved to {HOTKEYS_FILE}")
    except Exception as e:
        logger.error(f"Error saving hotkeys: {e}")

def load_hotkeys():
    """Load hotkeys from hotkeys.json file."""
    try:
        if HOTKEYS_FILE.exists():
            with open(HOTKEYS_FILE, 'r') as f:
                saved_hotkeys = json.load(f)

            # Convert string keys back to integers and update APP_STATE
            for slot_str, hotkey_data in saved_hotkeys.items():
                slot = int(slot_str)
                if slot in APP_STATE['hotkeys']:
                    APP_STATE['hotkeys'][slot] = hotkey_data

            logger.info(f"Hotkeys loaded from {HOTKEYS_FILE}: {saved_hotkeys}")
            return True
        else:
            logger.info(f"No hotkeys file found at {HOTKEYS_FILE}")
            return False
    except Exception as e:
        logger.error(f"Error loading hotkeys: {e}")
        return False

def send_hotkeys_to_listener():
    """Send loaded hotkeys to the listener process to re-register them."""
    if not mqtt_client or not mqtt_client.is_connected():
        logger.warning("Cannot send hotkeys to listener - MQTT not connected")
        return

    hotkeys_to_restore = 0

    for slot, hotkey_data in APP_STATE['hotkeys'].items():
        if hotkey_data:
            # Send a restore request to the listener
            # The listener will validate device availability and respond with success/error
            restore_msg = json.dumps({
                "event": "hotkey_registered",
                "slot": slot,
                "source": hotkey_data.get('source'),
                "value": hotkey_data.get('value'),
                "gamepad_name": hotkey_data.get('gamepad_name'),
                "restore": True  # Flag to indicate this is a restore operation
            })
            mqtt_client.publish("LISTENER", restore_msg, qos=1)
            hotkeys_to_restore += 1
            logger.info(f"Requesting restoration of hotkey - Slot {slot}: {hotkey_data}")

    if hotkeys_to_restore > 0:
        logger.info(f"Requested restoration of {hotkeys_to_restore} saved hotkey(s)")
        ui.notify(f"Restoring {hotkeys_to_restore} saved hotkey(s)...")

def check_mqtt_messages():
    """Process MQTT messages from the queue."""
    while not mqtt_queue.empty():
        topic, payload = mqtt_queue.get()

        # Parse JSON payload
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning(f"Received non-JSON message on {topic}: {payload}")
            continue

        # Handle APP topic messages
        if topic == "APP":
            if data.get("cmd") == "CLOSE":
                logger.info("UI received CLOSE command, shutting down")
                app.shutdown()

        # Handle UI topic messages
        elif topic == "UI":
            if data.get("notify"):
                msg = data.get("notify")
                logger.info(f"UI message: {msg}")
                ui.notify(f"{msg}")

        # Handle STATE topic messages
        elif topic == "STATE":
            if data.get("cmd") == "update_state":
                key = data.get("key")
                value = data.get("value")
                logger.info(f"STATE update message: {key} : {value}")
                if key in APP_STATE.keys():
                    APP_STATE[key] = value
                    state_changed()

        # Handle LISTENER topic messages
        elif topic == "LISTENER":
            event = data.get("event")
            if event == "hotkey_triggered":
                slot = data.get("slot")
                logger.info(f"Hotkey {slot} triggered")
                handle_hotkey_trigger(slot)
            elif event == "hotkey_restoration_success":
                # Handle successful hotkey restoration
                slot = data.get("slot")
                source = data.get("source")
                value = data.get("value")
                logger.info(f"Hotkey restored successfully for slot {slot}: {source} - {value}")
                # UI already has the hotkey in APP_STATE from loading, just update labels
                update_hotkey_labels()
            elif event == "hotkey_restoration_error":
                # Handle hotkey restoration error (device not available)
                slot = data.get("slot")
                source = data.get("source")
                error = data.get("error")
                logger.warning(f"Hotkey restoration failed for slot {slot}: {error}")
                # Clear the failed hotkey from APP_STATE
                if slot in APP_STATE['hotkeys']:
                    APP_STATE['hotkeys'][slot] = None
                    update_hotkey_labels()
                    save_hotkeys()  # Save updated state without the failed hotkey
                ui.notify(f"Hotkey {slot} not restored: {error}", type='warning')
            elif event == "hotkey_registered":
                # Handle hotkey registration confirmation
                slot = data.get("slot")
                source = data.get("source")
                value = data.get("value")
                gamepad_name = data.get("gamepad_name")

                if slot and source and value:
                    # Check if this hotkey is already assigned to another slot
                    for existing_slot, existing_hotkey in APP_STATE['hotkeys'].items():
                        if existing_slot != slot and existing_hotkey:
                            # Check if it's the same hotkey (same source, value, and gamepad if applicable)
                            if (existing_hotkey.get('source') == source and
                                existing_hotkey.get('value') == value):
                                # For gamepad hotkeys, also check gamepad_name
                                if source == 'gamepad':
                                    existing_gamepad = existing_hotkey.get('gamepad_name')
                                    if existing_gamepad == gamepad_name:
                                        # Same gamepad hotkey, clear the old assignment
                                        logger.info(f"Hotkey already assigned to slot {existing_slot}, clearing old assignment")
                                        APP_STATE['hotkeys'][existing_slot] = None
                                        # Send unregister command to listener for the old slot
                                        if mqtt_client and mqtt_client.is_connected():
                                            unregister_msg = json.dumps({
                                                "cmd": "unregister",
                                                "slot": existing_slot
                                            })
                                            mqtt_client.publish("LISTENER", unregister_msg, qos=1)
                                        ui.notify(f"Hotkey moved from slot {existing_slot} to slot {slot}", type='info')
                                        break
                                else:
                                    # Non-gamepad hotkey (keyboard/mouse), clear the old assignment
                                    logger.info(f"Hotkey already assigned to slot {existing_slot}, clearing old assignment")
                                    APP_STATE['hotkeys'][existing_slot] = None
                                    # Send unregister command to listener for the old slot
                                    if mqtt_client and mqtt_client.is_connected():
                                        unregister_msg = json.dumps({
                                            "cmd": "unregister",
                                            "slot": existing_slot
                                        })
                                        mqtt_client.publish("LISTENER", unregister_msg, qos=1)
                                    ui.notify(f"Hotkey moved from slot {existing_slot} to slot {slot}", type='info')
                                    break

                    # Assign the new hotkey
                    APP_STATE['hotkeys'][slot] = {
                        'source': source,
                        'value': value
                    }
                    if gamepad_name:
                        APP_STATE['hotkeys'][slot]['gamepad_name'] = gamepad_name
                    update_hotkey_labels()
                    save_hotkeys()  # Auto-save when hotkey is registered
                    logger.info(f"Hotkey registered for slot {slot}: {source} - {value}")
                    ui.notify(f"Hotkey registered: {source.capitalize()} - {value}")
            elif event == "hotkey_registration_error":
                # Handle hotkey registration error
                slot = data.get("slot")
                source = data.get("source")
                error = data.get("error")
                logger.warning(f"Hotkey registration failed for slot {slot}: {error}")
                ui.notify(f"Hotkey registration failed: {error}", type='negative')
            elif event == "hotkey_registration_cancelled":
                # Handle hotkey registration cancellation
                slot = data.get("slot")
                source = data.get("source")
                logger.info(f"Hotkey registration cancelled for slot {slot}")
                ui.notify("Hotkey registration cancelled", type='info')
            elif "info" not in data:  # Not a help message
                # Hotkey registration confirmation (no explicit event) - legacy support
                # Update local state when we receive slot/source/value
                slot = data.get("slot")
                source = data.get("source")
                value = data.get("value")
                if slot and source and value:
                    APP_STATE['hotkeys'][slot] = {
                        'source': source,
                        'value': value
                    }
                    update_hotkey_labels()
                    logger.info(f"Updated hotkey {slot}: {source} - {value}")



def publish_app_state():
    if mqtt_client and mqtt_client.is_connected():
        state = json.dumps({"state-data": APP_STATE})
        mqtt_client.publish("STATE", state, qos=1)
        logger.debug("Published APP_STATE to STATE Topic")

def state_changed():
    publish_app_state()
    update_ui_buttons()

def update_ui_buttons():
    """Update the play button UI based on play_status from APP_STATE.

    This function only updates UI elements, not state. State should be
    updated via APP_STATE and synced through the STATE process.
    """
    play_status = APP_STATE["play_status"]

    if play_status == "playing":
        # Update button to show PAUSE
        if UI_ELEMENTS['play_button']:
            UI_ELEMENTS['play_button'].props('icon=pause')
            UI_ELEMENTS['play_button'].text = "PAUSE"
    elif play_status == "paused":
        # Update button to show RESUME
        if UI_ELEMENTS['play_button']:
            UI_ELEMENTS['play_button'].props('icon=play_arrow')
            UI_ELEMENTS['play_button'].text = "RESUME"
    elif play_status == "stopped":
        # Update button to show PLAY
        if UI_ELEMENTS['play_button']:
            UI_ELEMENTS['play_button'].props('icon=play_arrow')
            UI_ELEMENTS['play_button'].text = "PLAY"



async def open_native_file_dialog():
    """Called when the NiceGUI button is clicked."""
    result = await app.native.main_window.create_file_dialog(allow_multiple=False)

    if result and len(result) > 0:
        path = result[0]
        UI_ELEMENTS['file_input'].value = path
        ui.notify(f'File chosen: {path}')

        # Stop any active playback before loading new file
        if APP_STATE['play_status'] in ['playing', 'paused']:
            logger.info("Stopping playback due to new file load")
            APP_STATE['play_status'] = 'stopped'
            ui.notify("Stopping playback...")
            state_changed()

        try:
            # Read and display file contents in UI
            with open(path, 'r', encoding='utf-8') as fc:
                APP_STATE['loaded_file_data'] = fc.read()
            APP_STATE["source_file_path"] = path

            # Update the code display
            if UI_ELEMENTS['source_code_display']:
                UI_ELEMENTS['source_code_display'].set_content(APP_STATE['loaded_file_data'])

            # Send load_file command to typer process
            if mqtt_client and mqtt_client.is_connected():
                load_msg = json.dumps({"cmd": "load_file", "file": path})
                mqtt_client.publish("TYPER", load_msg, qos=1)
                logger.info(f"Sent load_file command to TYPER for: {path}")
            else:
                logger.warning("MQTT client not connected, cannot send file to typer")

        except Exception as e:
            ui.notify(f'Error loading file: {e}', type='negative')
            logger.error(f'Error loading file {path}: {e}')

def open_source_folder():
    """Open the folder containing the source file in Windows Explorer."""
    source_path = APP_STATE.get('source_file_path')

    if not source_path:
        ui.notify("No source file loaded", type='warning')
        logger.warning("Attempted to open folder but no source file is loaded")
        return

    try:
        # Get the directory containing the file
        folder_path = os.path.dirname(os.path.abspath(source_path))

        # Open the folder in Windows Explorer
        subprocess.Popen(f'explorer "{folder_path}"')
        logger.info(f"Opened folder in Explorer: {folder_path}")
        ui.notify(f"Opened folder: {folder_path}")
    except Exception as e:
        ui.notify(f'Error opening folder: {e}', type='negative')
        logger.error(f'Error opening folder for {source_path}: {e}')

def open_in_editor():
    """Open the source file in VS Code if available, otherwise in Notepad."""
    source_path = APP_STATE.get('source_file_path')

    if not source_path:
        ui.notify("No source file loaded", type='warning')
        logger.warning("Attempted to open file in editor but no source file is loaded")
        return

    try:
        # Try to open with VS Code first
        try:
            # Try 'code' command (VS Code CLI)
            subprocess.Popen(['code', source_path])
            logger.info(f"Opened file in VS Code: {source_path}")
            ui.notify("Opened in VS Code")
        except (FileNotFoundError, OSError):
            # VS Code not found, try notepad as fallback
            logger.info("VS Code not found, falling back to Notepad")
            subprocess.Popen(['notepad.exe', source_path])
            logger.info(f"Opened file in Notepad: {source_path}")
            ui.notify("Opened in Notepad (VS Code not found)")
    except Exception as e:
        ui.notify(f'Error opening file in editor: {e}', type='negative')
        logger.error(f'Error opening file {source_path} in editor: {e}')

# UI callback functions
def update_slider_label(e):
    logger.debug(f"Ghost Coder Speed Changed: {e.value}")
    APP_STATE['typing_speed_value'] = int(e.value)
    UI_ELEMENTS['typing_speed_value'] = int(e.value)
    UI_ELEMENTS['typing_speed_label'].set_text(f"Ghost Coding Speed: {int(e.value)} ms")
    publish_app_state()

def toggle_pause_on_new_line(e):
    logger.debug(f"Auto pause on new line: {e.value}")
    APP_STATE['pause_on_new_line'] = e.value
    publish_app_state()

def start_playback_paused(e):
    logger.debug(f"Start playback paused: {e.value}")
    APP_STATE['start_playback_paused'] = e.value
    publish_app_state()

def toggle_auto_home_on_newline(e):
    logger.debug(f"Auto home on newline: {e.value}")
    APP_STATE['auto_home_on_newline'] = e.value
    publish_app_state()

def toggle_control_on_newline(e):
    logger.debug(f"Control on newline: {e.value}")
    APP_STATE['control_on_newline'] = e.value
    publish_app_state()

def toggle_replace_quad_spaces_with_tab(e):
    logger.debug(f"Replace quad spaces with tab: {e.value}")
    APP_STATE['replace_quad_spaces_with_tab'] = e.value
    publish_app_state()

def toggle_pause_on_app_change(e):
    logger.debug(f"Pause On App Focus Change: {e.value}")
    APP_STATE['pause_on_window_not_focused'] = e.value
    publish_app_state()

def toggle_refocus_on_resume(e):
    logger.debug(f"Refocus Window On Resume: {e.value}")
    APP_STATE['refocus_window_on_resume'] = e.value
    publish_app_state()
    # Publish to STATE topic

def toggle_varied_coding_speed(e):
    logger.debug(f"Varied coding speed: {e.value}")
    APP_STATE['varied_coding_speed'] = e.value
    publish_app_state()

def toggle_playback():
    # Check if a file is loaded
    if not APP_STATE.get('source_file_path'):
        ui.notify("No source file is loaded", type='warning')
        logger.warning("Play clicked but no source file is loaded")
        return

    current_status = APP_STATE.get('play_status')

    if current_status == 'playing':
        logger.info("Pause clicked")
        APP_STATE['play_status'] = 'paused'
        publish_app_state()
        ui.notify("Pausing Playback...")

    elif current_status == 'paused':
        logger.info("Resume clicked")
        APP_STATE['play_status'] = 'playing'
        publish_app_state()
        ui.notify("Resuming Playback...")
        if mqtt_client and mqtt_client.is_connected():
            load_msg = json.dumps({"cmd": "play"})
            mqtt_client.publish("TYPER", load_msg, qos=1)
            logger.info(f"Sent play command TYPER")
        else:
            logger.warning("MQTT client not connected, cannot send file to typer")

    else:  # stopped
        logger.info("Play clicked")
        APP_STATE['play_status'] = 'playing'
        publish_app_state()

        if mqtt_client and mqtt_client.is_connected():
            load_msg = json.dumps({"cmd": "play"})
            mqtt_client.publish("TYPER", load_msg, qos=1)
            logger.info(f"Sent play command TYPER")
        else:
            logger.warning("MQTT client not connected, cannot send file to typer")
        ui.notify("Starting playback in 5 seconds")
    state_changed()

def stop_playback():
    # Check if playback is actually running
    current_status = APP_STATE.get('play_status')
    if current_status == 'stopped':
        ui.notify("No source is playing", type='warning')
        logger.warning("Stop clicked but nothing is playing")
        return

    logger.info("Stop playback clicked")
    APP_STATE['play_status'] = 'stopped'
    ui.notify("Stopping playback...")
    state_changed()

def on_advance_newline_button():
    # Check if a file is loaded
    if not APP_STATE.get('source_file_path'):
        ui.notify("No source file is loaded", type='warning')
        logger.warning("Advance newline clicked but no source file is loaded")
        return

    # Check if playback has started
    current_status = APP_STATE.get('play_status')
    if current_status == 'stopped':
        ui.notify("Playback has not started yet", type='warning')
        logger.warning("Advance newline clicked but playback has not started")
        return

    logger.info("Advance to newline clicked")

    # Send advance_newline command to typer process
    if mqtt_client and mqtt_client.is_connected():
        advance_msg = json.dumps({"cmd": "advance_newline"})
        mqtt_client.publish("TYPER", advance_msg, qos=1)
        logger.info("Sent advance_newline command to TYPER")
        ui.notify("Advancing to newline")
    else:
        logger.warning("MQTT client not connected")

def on_advance_token_button():
    # Check if a file is loaded
    if not APP_STATE.get('source_file_path'):
        ui.notify("No source file is loaded", type='warning')
        logger.warning("Advance token clicked but no source file is loaded")
        return

    # Check if playback has started
    current_status = APP_STATE.get('play_status')
    if current_status == 'stopped':
        ui.notify("Playback has not started yet", type='warning')
        logger.warning("Advance token clicked but playback has not started")
        return

    logger.info("Advance to token clicked")

    # Send advance_token command to typer process
    if mqtt_client and mqtt_client.is_connected():
        advance_msg = json.dumps({"cmd": "advance_token"})
        mqtt_client.publish("TYPER", advance_msg, qos=1)
        logger.info("Sent advance_token command to TYPER")
        ui.notify("Advancing token")
    else:
        logger.warning("MQTT client not connected")


def show_hotkey_dialog(slot: int, button_name: str):
    """Show a dialog to configure a hotkey for the given slot."""
    with ui.dialog() as dialog, ui.card():
        ui.label(f'Configure Hotkey for {button_name}').classes('text-lg font-bold')
        ui.separator()

        with ui.column().classes('gap-2'):
            ui.button('Keyboard Hotkey',
                     icon='keyboard',
                     on_click=lambda: set_hotkey(slot, 'keyboard', dialog)).classes('w-full')
            ui.button('Mouse Hotkey',
                     icon='mouse',
                     on_click=lambda: set_hotkey(slot, 'mouse', dialog)).classes('w-full')
            ui.button('Gamepad Hotkey',
                     icon='sports_esports',
                     on_click=lambda: set_hotkey(slot, 'gamepad', dialog)).classes('w-full')
            ui.button('Clear Hotkey',
                     icon='clear',
                     on_click=lambda: clear_hotkey(slot, dialog)).classes('w-full')
            ui.separator()
            ui.button('Cancel (or press ESC)',
                     icon='close',
                     on_click=lambda: dialog.close()).classes('w-full')

        # Add keyboard handler for escape key to close dialog
        # Note: If in recording mode, the listener will handle ESC and cancel the registration
        dialog.on('keydown.esc', lambda: dialog.close())

    dialog.open()

def set_hotkey(slot: int, input_type: str, dialog):
    """Send MQTT command to register a hotkey."""
    if mqtt_client and mqtt_client.is_connected():
        register_msg = json.dumps({
            "cmd": "register",
            "slot": slot,
            "input": input_type,
            "suppress": False
        })
        mqtt_client.publish("LISTENER", register_msg, qos=1)
        logger.info(f"Sent register command for slot {slot} with input {input_type}")
        ui.notify(f"Press/click the {input_type} input now to register hotkey...")
        dialog.close()
    else:
        logger.warning("MQTT client not connected")
        ui.notify("MQTT not connected", type='negative')
        dialog.close()

def clear_hotkey(slot: int, dialog):
    """Clear the hotkey for the given slot."""
    if mqtt_client and mqtt_client.is_connected():
        unregister_msg = json.dumps({
            "cmd": "unregister",
            "slot": slot
        })
        mqtt_client.publish("LISTENER", unregister_msg, qos=1)
        logger.info(f"Sent unregister command for slot {slot}")

        # Update local state
        APP_STATE['hotkeys'][slot] = None
        update_hotkey_labels()
        save_hotkeys()  # Auto-save when hotkey is cleared

        ui.notify(f"Hotkey cleared")
        dialog.close()
    else:
        logger.warning("MQTT client not connected")
        ui.notify("MQTT not connected", type='negative')
        dialog.close()

def update_hotkey_labels():
    """Update the hotkey label displays and button borders."""
    hotkey_names = {
        1: "Play | Pause | Resume",
        2: "Stop",
        3: "Adv. to newline",
        4: "Adv. Token"
    }

    # Map slots to button elements
    button_map = {
        1: UI_ELEMENTS.get('play_button'),
        2: UI_ELEMENTS.get('stop_button'),
        3: UI_ELEMENTS.get('advance_to_next_newline_button'),
        4: UI_ELEMENTS.get('advance_to_next_token_button')
    }

    for slot, label in UI_ELEMENTS['hotkey_labels'].items():
        if label:
            hotkey_info = APP_STATE['hotkeys'].get(slot)
            button = button_map.get(slot)

            if hotkey_info:
                source = hotkey_info.get('source', '').capitalize()
                value = hotkey_info.get('value', '')
                label.set_text(f"{hotkey_names[slot]}: [{source}: {value}]")

                # Add green border to button if hotkey is set
                if button:
                    button.style('border: 2px solid green')
            else:
                label.set_text(f"{hotkey_names[slot]}: []")

                # Remove green border from button if no hotkey
                if button:
                    button.style('border: none')

def play_button_set_hotkey(e):
    show_hotkey_dialog(1, "Play/Pause/Resume")

def stop_button_set_hotkey(e):
    show_hotkey_dialog(2, "Stop")

def advance_to_next_newline_button_set_hotkey(e):
    show_hotkey_dialog(3, "Advance to Newline")

def advance_to_next_token_button_set_hotkey(e):
    show_hotkey_dialog(4, "Advance to Token")

def handle_hotkey_trigger(slot: int):
    """Handle a hotkey trigger event from the listener."""
    if slot == 1:  # Play/Pause/Resume
        toggle_playback()
    elif slot == 2:  # Stop
        stop_playback()
    elif slot == 3:  # Advance to newline
        on_advance_newline_button()
    elif slot == 4:  # Advance to token
        on_advance_token_button()
    else:
        logger.warning(f"Unknown hotkey slot: {slot}")




def build_ui():
    """Build the main UI layout."""
    ui.timer(0.01, check_mqtt_messages)


    with ui.row().classes('w-full').style('position: relative; gap: 0;'):

    
        # Left pane - Controls
        with ui.column().classes('p-4').style('gap: 0.1rem; width: 45%;'):
            ui.label("How to use this App:").classes('font-bold text-xl')
            ui.label("1. Select the source code file to play back")
            ui.label("2. Adjust playback speed and settings")
            ui.label("3. Set global hotkeys for playback control")
            ui.label("4. Start playback using controls or hotkeys")
            ui.separator().style("height:0.175rem;")

            ui.label("Source File to Play:").classes('font-bold')
            UI_ELEMENTS['file_input'] = ui.input(value='').props('readonly').classes('w-full')
            with ui.row():
                UI_ELEMENTS['select_source_file_button'] = ui.button(
                    'Select Source File To Play',
                    icon='file_open',
                    on_click=open_native_file_dialog
                )
                UI_ELEMENTS['open_folder_button'] = ui.button(
                    'Open Folder',
                    icon='folder_open',
                    on_click=open_source_folder
                )
                UI_ELEMENTS['open_in_editor_button'] = ui.button(
                    'Open in Editor',
                    icon='edit',
                    on_click=open_in_editor
                )

            ui.separator().style("")

            UI_ELEMENTS['typing_speed_label'] = ui.label(f"Ghost Coding Speed: {UI_ELEMENTS['typing_speed_value']} ms").classes('font-bold')
            ui.slider(min=50, max=500, step=25, value=50, on_change=update_slider_label).classes('w-full')

            with ui.row():
                UI_ELEMENTS["advance_to_next_newline_button"] = ui.checkbox("Auto Pause on New Line", value=False, on_change=toggle_pause_on_new_line)
                UI_ELEMENTS["start_playback_paused"] = ui.checkbox("Start Playback Paused", value=False, on_change=start_playback_paused)

            with ui.row():
                UI_ELEMENTS["auto_home_on_newline"] = ui.checkbox("Auto Home on Newline", value=True, on_change=toggle_auto_home_on_newline)
                UI_ELEMENTS["control_on_newline"] = ui.checkbox("Ctrl on Newline", value=True, on_change=toggle_control_on_newline)
            with ui.row():
                UI_ELEMENTS["replace_quad_spaces_with_tab"] = ui.checkbox("Replace Quad Spaces With Tab", value=True, on_change=toggle_replace_quad_spaces_with_tab)
                UI_ELEMENTS["pause_on_window_not_focused"] = ui.checkbox("Pause Playback On App Focus Change", value=True, on_change=toggle_pause_on_app_change)
            with ui.row():
                UI_ELEMENTS["refocus_window_on_resume"] = ui.checkbox("Refocus Window On Resume", value=True, on_change=toggle_refocus_on_resume)
                UI_ELEMENTS["varied_coding_speed"] = ui.checkbox("Varied Coding Speed", value=False, on_change=toggle_varied_coding_speed)

            ui.separator().style("height:0.175rem;")

            with ui.row():
                UI_ELEMENTS['play_button'] = ui.button("PLAY", icon='play_arrow', on_click=toggle_playback)
                UI_ELEMENTS['play_button'].on('contextmenu', play_button_set_hotkey)

                UI_ELEMENTS['stop_button'] = ui.button("STOP", icon='stop', on_click=stop_playback)
                UI_ELEMENTS['stop_button'].on('contextmenu', stop_button_set_hotkey)
                UI_ELEMENTS['advance_to_next_newline_button'] = ui.button("ADV. NEWLINE", icon='fast_forward', on_click=on_advance_newline_button)
                UI_ELEMENTS['advance_to_next_newline_button'].on('contextmenu', advance_to_next_newline_button_set_hotkey)
                UI_ELEMENTS['advance_to_next_token_button'] = ui.button("ADV. TOKEN", icon='fast_forward', on_click=on_advance_token_button)
                UI_ELEMENTS['advance_to_next_token_button'].on('contextmenu', advance_to_next_token_button_set_hotkey)

            ui.separator().style("height:0.175rem;")
            ui.separator().style("height:0.175rem;background-color:unset;")
            ui.label("Hotkeys (right click a button to set):").classes('font-bold')

            with ui.row().classes('w-full'):
                with ui.column().style("width:48.5%;"):
                    UI_ELEMENTS['hotkey_labels'][1] = ui.label("Play | Pause | Resume: []").classes('font-bold').style()
                    UI_ELEMENTS['hotkey_labels'][2] = ui.label("Stop: []").classes('font-bold')
                with ui.column().style("width:48.5%;"):
                    UI_ELEMENTS['hotkey_labels'][4] = ui.label("Adv. Token: []").classes('font-bold')
                    UI_ELEMENTS['hotkey_labels'][3] = ui.label("Adv. to newline: []").classes('font-bold')
            with ui.row().classes('w-full'):
                ui.separator().style("height:0.275rem;background-color:unset;")
                ui.label("Misc. Settings:").classes('font-bold')
                ui.separator().style("height:0.175rem;")
                dark = ui.dark_mode()
                ui.switch('Dark mode').bind_value(dark)

        # Right pane - Source code display
        with ui.column().classes('p-4').style('width: 55%; border-left: 2px solid #ccc;'):
            ui.label("Source Code Preview:").classes('font-bold text-xl')
            UI_ELEMENTS['source_code_display'] = ui.code('').classes('w-full').style('max-height: 800px; overflow: auto; font-size: 12px;')



def main():
    parser = argparse.ArgumentParser(description="Ghost Coder - MQTT-based coding assistant")
    parser.add_argument("--port", type=int, help="Specify the MQTT broker port (overrides random port selection)")
    parser.add_argument("--logging", action="store_true", help="Enable logging output")
    parser.add_argument("--extmqtt", type=str, help="Use external MQTT broker (format: host:port)")
    parser.add_argument("--extbroker", type=str, help="Use external MQTT broker (format: host:port)")
    # args = parser.parse_args()
    args, _unknown = parser.parse_known_args()

    # Configure logging based on --logging flag
    if args.logging:
        logger.configure(
            handlers=[
                {
                    "sink": "ghost_coder.log",
                    "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}:{function}:{line} - {message}",
                    "rotation": "10 MB",
                    "retention": "7 days",
                    "level": "DEBUG"
                },
                {
                    "sink": lambda msg: print(msg, end=""),
                    "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}:{function}:{line} - {message}",
                    "level": "DEBUG"
                }
            ]
        )
        logger.enable("ghost_coder")
        logger.info("Logging enabled - writing to ghost_coder.log and console")
    else:
        logger.disable("ghost_coder")

    # Parse external MQTT broker if provided (support both --extbroker and --extmqtt)
    ext_broker_arg = args.extbroker or args.extmqtt
    if ext_broker_arg:
        try:
            broker_host, broker_port_str = ext_broker_arg.split(":")
            available_port = int(broker_port_str)
            logger.info(f"Using external MQTT broker at {broker_host}:{available_port}")
        except ValueError:
            logger.error(f"Invalid --extbroker format: {ext_broker_arg}. Expected format: host:port")
            print(f"Error: Invalid --extbroker format. Expected format: host:port (e.g., 127.0.0.1:1883)")
            return
    else:
        broker_host = "127.0.0.1"
        # Use specified port or get a random one
        if args.port:
            available_port = args.port
            logger.info(f"Using specified port: {available_port}")
        else:
            available_port = get_random_available_port()
            logger.info(f"Random port: {available_port}")

    # Start child processes
    child_processes = {}

    # Start broker process only if not using external broker
    if not ext_broker_arg:
        logger.info("Starting broker process")
        child_processes["broker"] = mp.Process(target=broker_process, args=(broker_host, available_port, args.logging), name="broker")
        child_processes["broker"].start()

        # Wait a moment for broker to start
        time.sleep(1)
    else:
        logger.info("Skipping internal broker - using external MQTT broker")

    # Start listener process
    logger.info("Starting listener process")
    child_processes["listener"] = mp.Process(target=listener_process, args=(broker_host, available_port, args.logging), name="listener")
    child_processes["listener"].start()

    # Start typer process
    logger.info("Starting typer process")
    child_processes["typer"] = mp.Process(target=typer_process, args=(broker_host, available_port, args.logging), name="typer")
    child_processes["typer"].start()

    # Setup UI in main thread
    app.native.window_args['resizable'] = False
    
    def on_shutdown():
        """Send CLOSE message when UI is closing."""
        if mqtt_client and mqtt_client.is_connected():
            logger.info("UI closing, sending CLOSE message to APP topic")
            close_msg = json.dumps({"command": "CLOSE"})
            mqtt_client.publish("APP", close_msg, qos=1)
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            logger.info("UI MQTT client disconnected")
        
        # Terminate child processes
        for cpn, cp in child_processes.items():
            if cp and cp.is_alive():
                logger.info(f"Terminating {cpn} process")
                cp.terminate()
                cp.join(timeout=5)
    
    app.on_shutdown(on_shutdown)
    
    # Load saved hotkeys before building UI
    load_hotkeys()

    # Build UI
    build_ui()

    # Setup MQTT after UI is ready
    def on_mqtt_ready():
        setup_mqtt_client(broker_host, available_port)
        # Wait a bit for MQTT to connect, then send hotkeys and update UI
        ui.timer(1.0, lambda: (send_hotkeys_to_listener(), update_hotkey_labels()), once=True)

    ui.timer(0.5, on_mqtt_ready, once=True)
    
    # Start process monitor in background
    def monitor_processes():
        dead_processes_reported = set()
        for cpn, cp in child_processes.items():
            if cp and not cp.is_alive() and cpn not in dead_processes_reported:
                logger.warning(f"Process '{cpn}' is no longer alive (exit code: {cp.exitcode})")
                dead_processes_reported.add(cpn)
                if mqtt_client and mqtt_client.is_connected():
                    message = json.dumps({
                        "message": f"{cpn.upper()} PROCESS ENDED",
                        "process": cpn,
                        "exit_code": cp.exitcode
                    })
                    mqtt_client.publish("APP", message, qos=1)

    ui.timer(0.5, monitor_processes)  # Check every half second
    # ui.timer(1, publish_app_state) 
    
    logger.info("Starting UI in main thread")
    ui.run(
        title=f"Ghost Coder {APP_VERSION}",
        native=True,
        window_size=(1600, 900),
        reload=False,
    )

if __name__ == "__main__":
    mp.freeze_support()
    if mp.current_process().name == 'MainProcess':
        try:
            mp.set_start_method("spawn")
        except RuntimeError:
            pass
        main()