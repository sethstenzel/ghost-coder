import json
import multiprocessing as mp
import argparse
import time
from loguru import logger
from nicegui import ui, app
from queue import Queue
import paho.mqtt.client as Client
from ghost_coder.utils import get_random_available_port
from ghost_coder.broker import broker_process
from ghost_coder.listener import listener_process
from ghost_coder.typer import typer_process


APP_VERSION = "0.1.0"
logger.configure(
    handlers=[{
            "sink": lambda msg: print(msg, end=""),
            "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}: {message}"
    }]
)

UI_ELEMENTS = {
    'source_file_path_field': None,
    'select_source_file_button': None,
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
    'refocus_window_on_resume': None
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
    'source_file_path': '',
    'loaded_file_data': '',
    'loaded_file_parsed_data': ''
}

# MQTT setup
mqtt_queue = Queue()
mqtt_client = None

def setup_mqtt_client(port):
    """Initialize and connect the MQTT client."""
    global mqtt_client

    mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION2, "ui_client")
    mqtt_client.user_data_set({"port": port})
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message

    try:
        mqtt_client.connect("127.0.0.1", port, keepalive=60)
        mqtt_client.loop_start()
        logger.info(f"UI MQTT client connecting to 127.0.0.1:{port}")
    except Exception as e:
        logger.error(f"Failed to connect UI MQTT client: {e}")

def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    """Callback when MQTT client connects to broker."""
    if rc == 0:
        logger.info(f"UI connected to MQTT broker on port {userdata['port']}")
        client.subscribe([("UI", 1), ("APP", 1), ("STATE", 1)])
        logger.info("UI subscribed to topics: UI, APP, STATE")
    else:
        logger.error(f"UI failed to connect to MQTT broker, return code: {rc}")

def on_mqtt_message(client, userdata, message):
    """Callback when MQTT message is received."""
    mqtt_queue.put((message.topic, message.payload.decode()))
    logger.debug(f"UI received message - Topic: {message.topic}, Payload: {message.payload.decode()}")

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
        if UI_ELEMENTS['stop_button']:
            UI_ELEMENTS['stop_button'].enable()
        if UI_ELEMENTS['advance_to_next_newline_button']:
            UI_ELEMENTS['advance_to_next_newline_button'].disable()
        if UI_ELEMENTS['advance_to_next_token_button']:
            UI_ELEMENTS['advance_to_next_token_button'].disable()
    elif play_status == "paused":
        # Update button to show RESUME
        if UI_ELEMENTS['play_button']:
            UI_ELEMENTS['play_button'].props('icon=play_arrow')
            UI_ELEMENTS['play_button'].text = "RESUME"
        if UI_ELEMENTS['stop_button']:
            UI_ELEMENTS['stop_button'].enable()
        if UI_ELEMENTS['advance_to_next_newline_button']:
            UI_ELEMENTS['advance_to_next_newline_button'].enable()
        if UI_ELEMENTS['advance_to_next_token_button']:
            UI_ELEMENTS['advance_to_next_token_button'].enable()
    elif play_status == "stopped":
        # Update button to show PLAY
        if UI_ELEMENTS['play_button']:
            UI_ELEMENTS['play_button'].props('icon=play_arrow')
            UI_ELEMENTS['play_button'].text = "PLAY"
        if UI_ELEMENTS['stop_button']:
            UI_ELEMENTS['stop_button'].disable()
        if UI_ELEMENTS['advance_to_next_newline_button']:
            UI_ELEMENTS['advance_to_next_newline_button'].disable()
        if UI_ELEMENTS['advance_to_next_token_button']:
            UI_ELEMENTS['advance_to_next_token_button'].disable()



async def open_native_file_dialog():
    """Called when the NiceGUI button is clicked."""
    result = await app.native.main_window.create_file_dialog(allow_multiple=False)

    if result and len(result) > 0:
        path = result[0]
        UI_ELEMENTS['file_input'].value = path
        ui.notify(f'File chosen: {path}')
        UI_ELEMENTS['play_button'].enable()

        try:
            # Read and display file contents in UI
            with open(path, 'r', encoding='utf-8') as fc:
                APP_STATE['loaded_file_data'] = fc.read()
            APP_STATE["source_file_path"] = path
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

def toggle_playback():
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
    logger.info("Stop playback clicked")
    APP_STATE['play_status'] = 'stopped'
    ui.notify("Stopping playback...")
    state_changed()

def on_advance_newline_button():
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
    logger.info("Advance to token clicked")

    # Send advance_token command to typer process
    if mqtt_client and mqtt_client.is_connected():
        advance_msg = json.dumps({"cmd": "advance_token"})
        mqtt_client.publish("TYPER", advance_msg, qos=1)
        logger.info("Sent advance_token command to TYPER")
        ui.notify("Advancing token")
    else:
        logger.warning("MQTT client not connected")


def play_button_set_hotkey(e):
    pass
def stop_button_set_hotkey(e):
    pass
def advance_to_next_newline_button_set_hotkey(e):
    pass
def advance_to_next_token_button_set_hotkey(e):
    pass




def build_ui():
    """Build the main UI layout."""
    ui.timer(0.01, check_mqtt_messages)
    

    with ui.row().classes('w-full').style('position: relative; gap: 0;'):
        with ui.column().classes('p-4').style('gap: 0.1rem; flex: 1;'):
            ui.label("How to use this App:").classes('font-bold text-xl')
            ui.label("1. Select the source code file to play back")
            ui.label("2. Adjust playback speed and settings")
            ui.label("3. Set global hotkeys for playback control")
            ui.label("4. Start playback using controls or hotkeys")
            ui.separator().style("height:0.175rem;")

            ui.label("Source File to Play:").classes('font-bold')
            UI_ELEMENTS['file_input'] = ui.input(value='').props('readonly').classes('w-full')
            UI_ELEMENTS['select_source_file_button'] = ui.button(
                'Select Source File To Play',
                icon='file_open',
                on_click=open_native_file_dialog
            )

            ui.separator().style("")

            UI_ELEMENTS['typing_speed_label'] = ui.label(f"Ghost Coding Speed: {UI_ELEMENTS['typing_speed_value']} ms").classes('font-bold')
            ui.slider(min=50, max=500, step=25, value=100, on_change=update_slider_label).classes('w-full')

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

            ui.separator().style("height:0.175rem;")

            with ui.row():
                UI_ELEMENTS['play_button'] = ui.button("PLAY", icon='play_arrow', on_click=toggle_playback)
                UI_ELEMENTS['play_button'].disable()
                UI_ELEMENTS['play_button'].on('contextmenu', play_button_set_hotkey)

                UI_ELEMENTS['stop_button'] = ui.button("STOP", icon='stop', on_click=stop_playback)
                UI_ELEMENTS['stop_button'].disable()
                UI_ELEMENTS['stop_button'].on('contextmenu', stop_button_set_hotkey)
                UI_ELEMENTS['advance_to_next_newline_button'] = ui.button("ADV. NEWLINE", icon='fast_forward', on_click=on_advance_newline_button)
                UI_ELEMENTS['advance_to_next_newline_button'].disable()
                UI_ELEMENTS['advance_to_next_newline_button'].on('contextmenu', advance_to_next_token_button_set_hotkey)
                UI_ELEMENTS['advance_to_next_token_button'] = ui.button("ADV. TOKEN", icon='fast_forward', on_click=on_advance_token_button)
                UI_ELEMENTS['advance_to_next_token_button'].disable()
                UI_ELEMENTS['advance_to_next_token_button'].on('contextmenu', advance_to_next_token_button_set_hotkey)

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


def main():
    parser = argparse.ArgumentParser(description="Ghost Coder - MQTT-based coding assistant")
    parser.add_argument("--port", type=int, help="Specify the MQTT broker port (overrides random port selection)")
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

    # Start child processes
    mp.set_start_method("spawn", force=True)
    
    child_processes = {}
    
    # Start broker process
    logger.info("Starting broker process")
    child_processes["broker"] = mp.Process(target=broker_process, args=(available_port, args.logging), name="broker")
    child_processes["broker"].start()

    # Wait a moment for broker to start
    time.sleep(1)

    # Start listener process
    # logger.info("Starting listener process")
    # child_processes["listener"] = mp.Process(target=listener_process, args=(available_port, args.logging), name="listener")
    # child_processes["listener"].start()

    # Start typer process
    logger.info("Starting typer process")
    child_processes["typer"] = mp.Process(target=typer_process, args=(available_port, args.logging), name="typer")
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
    
    # Build UI
    build_ui()

    # Setup MQTT after UI is ready
    ui.timer(0.5, lambda: setup_mqtt_client(available_port), once=True)
    
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
    
    ui.timer(0.5, monitor_processes)  # Check every second
    # ui.timer(1, publish_app_state) 
    
    logger.info("Starting UI in main thread")
    ui.run(
        title=f"Ghost Coder {APP_VERSION}",
        native=True,
        window_size=(700, 900),
        reload=False,
    )

if __name__ == "__main__":
    main()
