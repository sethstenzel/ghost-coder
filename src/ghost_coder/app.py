import multiprocessing as mp
import argparse
import time
from loguru import logger
from nicegui import ui, app
from queue import Queue
import paho.mqtt.client as Client
from ghost_coder.utils import get_random_available_port
from ghost_coder.inspector import inspector_process
from ghost_coder.broker import broker_process
from ghost_coder.listener import listener_process
from ghost_coder.state import state_process
from ghost_coder.typer import typer_process


APP_VERSION = "0.1.0"
logger.configure(handlers=[{"sink": lambda msg: print(msg, end=""), "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}: {message}"}])

UI_REFS = {
    'opened_files': None,
    'open_files_contents': '',
    'play_button': None,
    'stop_button': None,
    'advance_to_next_newline_button': None,
    'advance_to_next_token_button': None,
    'typing_speed_value': 50,
    'typing_speed_label': None,
    'code_editor': None,
    'right_pane': None,
    'toggle_button': None,
    'right_pane_visible': False,
    'is_playing': False,
}

# MQTT setup
mqtt_queue = Queue()
mqtt_client = None

def on_mqtt_connect(client, userdata, flags, rc):
    """Callback when MQTT client connects to broker."""
    if rc == 0:
        logger.info(f"UI connected to MQTT broker on port {userdata['port']}")
        client.subscribe([("UI", 1), ("APP", 1), ("LISTENER", 1), ("LISTENER_RESPONSE", 1), ("STATE", 1), ("TYPER", 1)])
        logger.info("UI subscribed to topics: UI, APP, LISTENER, LISTENER_RESPONSE, STATE, TYPER")
    else:
        logger.error(f"UI failed to connect to MQTT broker, return code: {rc}")

def on_mqtt_message(client, userdata, message):
    """Callback when MQTT message is received."""
    mqtt_queue.put((message.topic, message.payload.decode()))
    logger.debug(f"UI received message - Topic: {message.topic}, Payload: {message.payload.decode()}")

def check_mqtt_messages():
    """Process MQTT messages from the queue."""
    import json
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
            if data.get("command") == "CLOSE":
                logger.info("UI received CLOSE command, shutting down")
                app.shutdown()
            elif data.get("message"):
                msg = data.get("message")
                logger.info(f"APP message: {msg}")
                ui.notify(f"System: {msg}")
            else:
                logger.info(f"APP data: {data}")
                ui.notify(f"System: {data}")
        
        # Handle UI topic messages
        elif topic == "UI":
            if data.get("message"):
                msg = data.get("message")
                logger.info(f"UI message: {msg}")
                ui.notify(f"MQTT: {msg}")
            else:
                logger.info(f"UI data: {data}")
                ui.notify(f"MQTT: {data}")
        
        # Handle LISTENER topic messages (hotkey events)
        elif topic == "LISTENER":
            logger.info(f"LISTENER message: {payload}")
            try:
                event = data
                
                # Handle help info messages
                if "info" in event:
                    help_info = event["info"]
                    help_text = f"Listener Help:\n\nDescription: {help_info.get('description', 'N/A')}\n\n"
                    
                    if "features" in help_info:
                        help_text += "Features:\n"
                        for feature in help_info["features"]:
                            help_text += f"  • {feature}\n"
                        help_text += "\n"
                    
                    if "workflow" in help_info:
                        help_text += "Workflow:\n"
                        for step in help_info["workflow"]:
                            help_text += f"  {step}\n"
                    
                    logger.info(f"Help response:\n{help_text}")
                    ui.notify("Help information logged to console", type="info")
                
                # Handle hotkey triggered events
                elif event.get("event") == "hotkey_triggered":
                    slot = event.get("slot")
                    source = event.get("source")
                    value = event.get("value")
                    message = event.get("message", "")
                    
                    notify_msg = f"Hotkey {slot}: {source} '{value}'"
                    if message:
                        notify_msg += f" - {message}"
                    
                    ui.notify(notify_msg, type="positive")
            except Exception as e:
                logger.error(f"Error parsing LISTENER message: {e}")
        
        # Handle LISTENER_RESPONSE topic messages (help, gamepad list, etc.)
        elif topic == "LISTENER_RESPONSE":
            logger.info(f"LISTENER_RESPONSE message: {payload}")
            try:
                response = data
                
                if response.get("command") == "help":
                    # Display help information
                    usage = response.get("usage", {})
                    help_text = f"Listener Help:\n\nDescription: {usage.get('description', 'N/A')}\n\n"
                    
                    if "features" in usage:
                        help_text += "Features:\n"
                        for feature in usage["features"]:
                            help_text += f"  • {feature}\n"
                        help_text += "\n"
                    
                    if "workflow" in usage:
                        help_text += "Workflow:\n"
                        for step in usage["workflow"]:
                            help_text += f"  {step}\n"
                    
                    logger.info(f"Help response:\n{help_text}")
                    ui.notify("Help information logged to console", type="info")
                
                elif response.get("command") == "get_gamepads":
                    gamepads = response.get("gamepads", [])
                    if gamepads:
                        gamepad_list = "Available gamepads:\n" + "\n".join(
                            f"  {gp['index']}: {gp['name']}" for gp in gamepads
                        )
                        logger.info(gamepad_list)
                        ui.notify(f"Found {len(gamepads)} gamepad(s)", type="info")
                    else:
                        logger.info("No gamepads available")
                        ui.notify("No gamepads detected", type="warning")
            except Exception as e:
                logger.error(f"Error parsing LISTENER_RESPONSE message: {e}")

        # Handle STATE topic messages
        elif topic == "STATE":
            logger.info(f"STATE message: {payload}")
            try:
                response = data

                # Check if it's a get response
                if "result" in response:
                    result = response.get("result")
                    if isinstance(result, dict):
                        # Full state response - check for play_status updates
                        logger.info(f"Current state: {result}")
                        ui.notify(f"State: {len(result)} items", type="info")

                        # Update UI based on play_status
                        if "play_status" in result:
                            _update_play_button_ui(result["play_status"])
                    else:
                        # Single key response
                        logger.info(f"State value: {result}")
                        ui.notify(f"State value retrieved", type="info")

                # Check for errors
                if "error" in response:
                    error_msg = response.get("error")
                    logger.warning(f"State error: {error_msg}")
                    ui.notify(f"State error: {error_msg}", type="warning")

                # Check for warnings
                if "warning" in response:
                    warning_msg = response.get("warning")
                    logger.info(f"State warning: {warning_msg}")
            except Exception as e:
                logger.error(f"Error parsing STATE message: {e}")

        # Handle TYPER topic messages
        elif topic == "TYPER":
            logger.info(f"TYPER message: {payload}")
            try:
                response = data

                # Check for result
                if "result" in response:
                    result = response.get("result")
                    message_text = response.get("message", "")

                    if isinstance(result, list):
                        # Data preview response
                        logger.info(f"Typer data: {len(result)} tokens")
                        ui.notify(f"Typer: {len(result)} tokens loaded", type="info")
                    elif result == "ok" and message_text:
                        # Success message
                        logger.info(f"Typer: {message_text}")
                        ui.notify(f"Typer: {message_text}", type="positive")
                    else:
                        logger.info(f"Typer result: {result}")
                        ui.notify(f"Typer: {result}", type="info")

                # Check for errors
                if "error" in response:
                    error_msg = response.get("error")
                    logger.warning(f"Typer error: {error_msg}")
                    ui.notify(f"Typer error: {error_msg}", type="negative")

                # Check for warnings
                if "warning" in response:
                    warning_msg = response.get("warning")
                    logger.info(f"Typer warning: {warning_msg}")
                    ui.notify(f"Typer: {warning_msg}", type="warning")

                # Check for help info
                if "info" in response:
                    help_info = response.get("info")
                    logger.info(f"Typer help info received")
                    ui.notify("Typer help information logged to console", type="info")
            except Exception as e:
                logger.error(f"Error parsing TYPER message: {e}")

def _update_play_button_ui(play_status: str):
    """Update the play button UI based on play_status from STATE."""
    if play_status == "playing":
        UI_REFS['is_playing'] = True
        if UI_REFS['play_button']:
            UI_REFS['play_button'].props('icon=pause')
            UI_REFS['play_button'].text = "PAUSE"
        if UI_REFS['stop_button']:
            UI_REFS['stop_button'].enable()
        # Enable advance buttons when playing
        if UI_REFS['advance_to_next_newline_button']:
            UI_REFS['advance_to_next_newline_button'].enable()
        if UI_REFS['advance_to_next_token_button']:
            UI_REFS['advance_to_next_token_button'].enable()
    elif play_status == "paused":
        UI_REFS['is_playing'] = False
        if UI_REFS['play_button']:
            UI_REFS['play_button'].props('icon=play_arrow')
            UI_REFS['play_button'].text = "RESUME"
        # Keep advance buttons enabled when paused
        if UI_REFS['advance_to_next_newline_button']:
            UI_REFS['advance_to_next_newline_button'].enable()
        if UI_REFS['advance_to_next_token_button']:
            UI_REFS['advance_to_next_token_button'].enable()
    elif play_status == "stopped":
        UI_REFS['is_playing'] = False
        if UI_REFS['play_button']:
            UI_REFS['play_button'].props('icon=play_arrow')
            UI_REFS['play_button'].text = "PLAY"
        # Disable all control buttons when stopped
        if UI_REFS['stop_button']:
            UI_REFS['stop_button'].disable()
        if UI_REFS['advance_to_next_newline_button']:
            UI_REFS['advance_to_next_newline_button'].disable()
        if UI_REFS['advance_to_next_token_button']:
            UI_REFS['advance_to_next_token_button'].disable()

def setup_mqtt_client(port):
    """Initialize and connect the MQTT client."""
    global mqtt_client

    mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION1, "ui_client")
    mqtt_client.user_data_set({"port": port})
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect("127.0.0.1", port, keepalive=60)
        mqtt_client.loop_start()
        logger.info(f"UI MQTT client connecting to 127.0.0.1:{port}")
    except Exception as e:
        logger.error(f"Failed to connect UI MQTT client: {e}")

def initialize_default_state():
    """Send default checkbox states to STATE process on startup."""
    import json
    if mqtt_client and mqtt_client.is_connected():
        # Default values from UI checkboxes
        default_states = [
            {"key": "speed", "value": 50, "type": "int"},
            {"key": "pause_on_new_line", "value": False, "type": "bool"},
            {"key": "start_playback_paused", "value": False, "type": "bool"},
            {"key": "auto_home_on_newline", "value": True, "type": "bool"},
            {"key": "control_on_newline", "value": True, "type": "bool"},
            {"key": "replace_quad_spaces_with_tab", "value": True, "type": "bool"},
            {"key": "pause_on_window_not_focused", "value": True, "type": "bool"},
            {"key": "refocus_window_on_resume", "value": True, "type": "bool"},
        ]

        for state in default_states:
            state_msg = json.dumps({"cmd": "add", "key": state["key"], "value": state["value"], "type": state["type"]})
            mqtt_client.publish("STATE", state_msg, qos=1)
            logger.info(f"Initialized default state: {state['key']} = {state['value']}")
    else:
        logger.warning("MQTT client not connected, cannot initialize default state")

async def open_native_file_dialog():
    """Called when the NiceGUI button is clicked."""
    import json
    result = await app.native.main_window.create_file_dialog(allow_multiple=False)

    if result and len(result) > 0:
        path = result[0]
        UI_REFS['file_input'].value = path
        ui.notify(f'File chosen: {path}')
        UI_REFS['play_button'].enable()

        try:
            # Read and display file contents in UI
            with open(path, 'r', encoding='utf-8') as fc:
                UI_REFS['file_contents'] = fc.read()
                UI_REFS['code_editor'].set_content(UI_REFS['file_contents'])
                toggle_right_pane()

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

# UI callback functions
def update_slider_label(e):
    import json
    UI_REFS['typing_speed_value'] = int(e.value)
    UI_REFS['typing_speed_label'].set_text(f"Ghost Coding Speed: {int(e.value)} ms")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "speed", "value": int(e.value), "type": "int"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated speed state to {int(e.value)}")

def toggle_pause_on_new_line(e):
    import json
    logger.info(f"Auto pause on new line: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "pause_on_new_line", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated pause_on_new_line state to {e.value}")

def start_playback_paused(e):
    import json
    logger.info(f"Start playback paused: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "start_playback_paused", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated start_playback_paused state to {e.value}")

def toggle_auto_home_on_newline(e):
    import json
    logger.info(f"Auto home on newline: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "auto_home_on_newline", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated auto_home_on_newline state to {e.value}")

def toggle_control_on_newline(e):
    import json
    logger.info(f"Control on newline: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "control_on_newline", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated control_on_newline state to {e.value}")

def toggle_replace_quad_spaces_with_tab(e):
    import json
    logger.info(f"Replace quad spaces with tab: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "replace_quad_spaces_with_tab", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated replace_quad_spaces_with_tab state to {e.value}")

def toggle_pause_on_app_change(e):
    import json
    logger.info(f"Pause On App Focus Change: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "pause_on_window_not_focused", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated pause_on_window_not_focused state to {e.value}")

def toggle_refocus_on_resume(e):
    import json
    logger.info(f"Refocus Window On Resume: {e.value}")

    # Publish to STATE topic
    if mqtt_client and mqtt_client.is_connected():
        state_msg = json.dumps({"cmd": "add", "key": "refocus_window_on_resume", "value": e.value, "type": "bool"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info(f"Updated refocus_window_on_resume state to {e.value}")

def toggle_playback():
    import json

    if UI_REFS['is_playing']:
        # Currently playing, so pause
        logger.info("Pause clicked")

        if mqtt_client and mqtt_client.is_connected():
            pause_msg = json.dumps({"cmd": "pause"})
            mqtt_client.publish("TYPER", pause_msg, qos=1)
            logger.info("Sent pause command to TYPER")

            ui.notify("Pausing playback...")

            # Update UI - button text will be updated to "RESUME" by state sync
            UI_REFS['is_playing'] = False
            UI_REFS['play_button'].props('icon=play_arrow')
            UI_REFS['play_button'].text = "RESUME"
            UI_REFS['stop_button'].enable()
            # Keep advance buttons enabled when paused
            UI_REFS['advance_to_next_newline_button'].enable()
            UI_REFS['advance_to_next_token_button'].enable()
        else:
            logger.warning("MQTT client not connected")
            ui.notify("Error: Not connected to MQTT", type="negative")
    else:
        # Currently paused/stopped, so play or resume
        button_text = UI_REFS['play_button'].text if UI_REFS['play_button'] else "PLAY"

        if button_text == "RESUME":
            # Resuming from pause - send pause command to toggle pause off
            logger.info("Resume clicked")

            if mqtt_client and mqtt_client.is_connected():
                pause_msg = json.dumps({"cmd": "pause"})
                mqtt_client.publish("TYPER", pause_msg, qos=1)
                logger.info("Sent pause command to TYPER (to resume)")

                ui.notify("Resuming playback...")

                # Update UI
                UI_REFS['is_playing'] = True
                UI_REFS['play_button'].props('icon=pause')
                UI_REFS['play_button'].text = "PAUSE"
                UI_REFS['stop_button'].enable()
                # Enable advance buttons when playing
                UI_REFS['advance_to_next_newline_button'].enable()
                UI_REFS['advance_to_next_token_button'].enable()
            else:
                logger.warning("MQTT client not connected")
                ui.notify("Error: Not connected to MQTT", type="negative")
        else:
            # Starting fresh playback
            logger.info("Play clicked")

            if mqtt_client and mqtt_client.is_connected():
                play_msg = json.dumps({"cmd": "play"})
                mqtt_client.publish("TYPER", play_msg, qos=1)
                logger.info("Sent play command to TYPER")

                ui.notify("Starting playback...")

                # Update UI
                UI_REFS['is_playing'] = True
                UI_REFS['play_button'].props('icon=pause')
                UI_REFS['play_button'].text = "PAUSE"
                UI_REFS['stop_button'].enable()
                # Enable advance buttons when playing
                UI_REFS['advance_to_next_newline_button'].enable()
                UI_REFS['advance_to_next_token_button'].enable()
            else:
                logger.warning("MQTT client not connected")
                ui.notify("Error: Not connected to MQTT", type="negative")

def stop_playback():
    import json
    logger.info("Stop playback clicked")

    # Send stop command to typer process and update state
    if mqtt_client and mqtt_client.is_connected():
        stop_msg = json.dumps({"cmd": "stop"})
        mqtt_client.publish("TYPER", stop_msg, qos=1)
        logger.info("Sent stop command to TYPER")

        # Update play_status state
        state_msg = json.dumps({"cmd": "add", "key": "play_status", "value": "stopped", "type": "str"})
        mqtt_client.publish("STATE", state_msg, qos=1)
        logger.info("Updated play_status state to 'stopped'")

        ui.notify("Stopping playback...")

        # Update UI - reset to play state and disable all control buttons
        UI_REFS['is_playing'] = False
        UI_REFS['play_button'].props('icon=play_arrow')
        UI_REFS['play_button'].text = "PLAY"
        UI_REFS['stop_button'].disable()
        # Disable advance buttons when stopped
        UI_REFS['advance_to_next_newline_button'].disable()
        UI_REFS['advance_to_next_token_button'].disable()
    else:
        logger.warning("MQTT client not connected")
        ui.notify("Error: Not connected to MQTT", type="negative")

def on_advance_newline_button():
    import json
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
    import json
    logger.info("Advance to token clicked")

    # Send advance_token command to typer process
    if mqtt_client and mqtt_client.is_connected():
        advance_msg = json.dumps({"cmd": "advance_token"})
        mqtt_client.publish("TYPER", advance_msg, qos=1)
        logger.info("Sent advance_token command to TYPER")
        ui.notify("Advancing token")
    else:
        logger.warning("MQTT client not connected")


def toggle_right_pane():
    if UI_REFS['right_pane_visible']:
        UI_REFS['right_pane'].style('display: none;')
        UI_REFS['toggle_button'].props('icon=chevron_right')
        UI_REFS['right_pane_visible'] = False
        if hasattr(app, 'native') and app.native.main_window:
            app.native.main_window.resize(700, 900)
        UI_REFS['toggle_button'].style("top: 65%;")
    else:
        UI_REFS['right_pane'].style('display: flex;')
        UI_REFS['toggle_button'].props('icon=chevron_left')
        UI_REFS['right_pane_visible'] = True
        if hasattr(app, 'native') and app.native.main_window:
            app.native.main_window.resize(1400, 900)
            UI_REFS['toggle_button'].style("top: 48%;")
            

def build_ui():
    """Build the main UI layout."""
    ui.timer(0.01, check_mqtt_messages)
    

    with ui.row().classes('w-full').style('position: relative; gap: 0;'):
        with ui.column():
            with ui.column().classes('p-4').style('gap: 0.1rem; flex: 1;'):
                ui.label("How to use this App:").classes('font-bold text-xl')
                ui.label("1. Select the source code file to play back")
                ui.label("2. Adjust playback speed and settings")
                ui.label("3. Set global hotkeys for playback control")
                ui.label("4. Start playback using controls or hotkeys")

                ui.separator().style("height:0.175rem;")

                ui.label("Source File to Play:").classes('font-bold')
                UI_REFS['file_input'] = ui.input(value='').props('readonly').classes('w-full')
                ui.button('Pick file (native PyWebview dialog)', icon='file_open', on_click=open_native_file_dialog)

                ui.separator().style("")

                UI_REFS['typing_speed_label'] = ui.label(f"Ghost Coding Speed: {UI_REFS['typing_speed_value']} ms").classes('font-bold')
                ui.slider(min=50, max=500, step=25, value=100, on_change=update_slider_label).classes('w-full')

                with ui.row():
                    ui.checkbox("Auto Pause on New Line", value=False, on_change=toggle_pause_on_new_line)
                    ui.checkbox("Start Playback Paused", value=False, on_change=start_playback_paused)

                with ui.row():
                    ui.checkbox("Auto Home on Newline", value=True, on_change=toggle_auto_home_on_newline)
                    ui.checkbox("Ctrl on Newline", value=True, on_change=toggle_control_on_newline)
                with ui.row():
                    ui.checkbox("Replace Quad Spaces With Tab", value=True, on_change=toggle_replace_quad_spaces_with_tab)
                    ui.checkbox("Pause Playback On App Focus Change", value=True, on_change=toggle_pause_on_app_change)
                with ui.row():
                    ui.checkbox("Refocus Window On Resume", value=True, on_change=toggle_refocus_on_resume)

                ui.separator().style("height:0.175rem;")

                with ui.row():
                    UI_REFS['play_button'] = ui.button("PLAY", icon='play_arrow', on_click=toggle_playback)
                    UI_REFS['play_button'].disable()
                    UI_REFS['stop_button'] = ui.button("STOP", icon='stop', on_click=stop_playback)
                    UI_REFS['stop_button'].disable()
                    UI_REFS['advance_to_next_newline_button'] = ui.button("ADV. NEWLINE", icon='fast_forward', on_click=on_advance_newline_button)
                    UI_REFS['advance_to_next_newline_button'].disable()
                    UI_REFS['advance_to_next_token_button'] = ui.button("ADV. TOKEN", icon='fast_forward', on_click=on_advance_token_button)
                    UI_REFS['advance_to_next_token_button'].disable()

                ui.separator().style("height:0.175rem;")
                ui.separator().style("height:0.175rem;background-color:unset;")
                ui.label("Hotkeys:").classes('font-bold')

                with ui.row().classes('w-full'):
                    with ui.column().style("width:48.5%;"):
                        ui.label("Play | Pause | Resume: []").classes('font-bold').style()
                        ui.label("Stop: []").classes('font-bold')
                    with ui.column().style("width:48.5%;"):
                        ui.label("Adv. Token: []").classes('font-bold')
                        ui.label("Adv. to newline: []").classes('font-bold')

            UI_REFS['toggle_button'] = ui.button(icon='chevron_right', on_click=toggle_right_pane).props('flat round').style(
                'position: absolute; right: 0; top: 65%; transform: translateY(-55%); z-index: 100;'
            )

        UI_REFS['right_pane'] = ui.column().classes('p-4').style(
            'gap: 0.1rem; flex: 1; display: none; border-left: 2px solid #ccc; height: 100vh; overflow: auto;'
        )
        with UI_REFS['right_pane']:
            ui.label("File Contents").classes('font-bold text-lg').style("gap: unset;")
            UI_REFS['code_editor'] = ui.code().classes('w-full').style('height: 700px;')

def main():
    parser = argparse.ArgumentParser(description="Ghost Coder - MQTT-based coding assistant")
    parser.add_argument("--port", type=int, help="Specify the MQTT broker port (overrides random port selection)")
    parser.add_argument("--inspector", action="store_true", help="Enable the MQTT inspector process")
    parser.add_argument("--logging", action="store_true", help="Enable logging output")
    args = parser.parse_args()

    # Configure logging based on --logging flag
    if args.logging:
        logger.enable("ghost_coder")
    else:
        logger.disable("ghost_coder")

    # Use specified port or get a random one
    if args.port:
        available_port = args.port
        logger.info(f"Using specified port: {available_port}")
    else:
        available_port = get_random_available_port()
        logger.info(f"Random port: {available_port}")

    broker_config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"127.0.0.1:{available_port}",
            }
        },
    }

    # Start child processes
    mp.set_start_method("spawn", force=True)
    
    child_processes = {}
    
    # Start broker process
    logger.info("Starting broker process")
    child_processes["broker"] = mp.Process(target=broker_process, args=(broker_config, available_port, args.logging), name="broker")
    child_processes["broker"].start()

    # Wait a moment for broker to start
    time.sleep(1)

    # Start listener process
    logger.info("Starting listener process")
    child_processes["listener"] = mp.Process(target=listener_process, args=(available_port, args.logging), name="listener")
    child_processes["listener"].start()

    # Start state process
    logger.info("Starting state process")
    child_processes["state"] = mp.Process(target=state_process, args=(available_port, args.logging), name="state")
    child_processes["state"].start()

    # Start typer process
    logger.info("Starting typer process")
    child_processes["typer"] = mp.Process(target=typer_process, args=(available_port, args.logging), name="typer")
    child_processes["typer"].start()

    # Start inspector if enabled
    if args.inspector:
        logger.info("Starting inspector process")
        child_processes["inspector"] = mp.Process(target=inspector_process, args=(available_port, args.logging), name="inspector")
        child_processes["inspector"].start()

    # Setup UI in main thread
    app.native.window_args['resizable'] = False
    
    def on_shutdown():
        """Send CLOSE message when UI is closing."""
        import json
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
    
    # Build UI
    build_ui()

    # Setup MQTT after UI is ready
    ui.timer(0.5, lambda: setup_mqtt_client(available_port), once=True)

    # Initialize default state values after MQTT connection is established
    ui.timer(1.5, initialize_default_state, once=True)
    
    # Start process monitor in background
    def monitor_processes():
        import json
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
    
    ui.timer(1.0, monitor_processes)  # Check every second
    
    logger.info("Starting UI in main thread")
    ui.run(
        title=f"Ghost Coder {APP_VERSION}",
        native=True,
        window_size=(700, 900),
        reload=False,
    )

if __name__ == "__main__":
    main()
