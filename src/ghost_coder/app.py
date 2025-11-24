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


APP_VERSION = "0.1.0"
logger.configure(handlers=[{"sink": lambda msg: print(msg, end=""), "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}: {message}"}])

UI_REFS = {
    'opened_files': None,
    'open_files_contents': '',
    'play_button': None,
    'stop_button': None,
    'advance_to_next_newline_button': None,
    'advance_to_next_token_button': None,
    'typing_speed_value': 100,
    'typing_speed_label': None,
    'code_editor': None,
    'right_pane': None,
    'toggle_button': None,
    'right_pane_visible': False,
}

# MQTT setup
mqtt_queue = Queue()
mqtt_client = None

def on_mqtt_connect(client, userdata, flags, rc):
    """Callback when MQTT client connects to broker."""
    if rc == 0:
        logger.info(f"UI connected to MQTT broker on port {userdata['port']}")
        client.subscribe([("UI", 1), ("APP", 1), ("LISTENER", 1), ("LISTENER_RESPONSE", 1)])
        logger.info("UI subscribed to topics: UI, APP, LISTENER, LISTENER_RESPONSE")
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

async def open_native_file_dialog():
    """Called when the NiceGUI button is clicked."""
    result = await app.native.main_window.create_file_dialog(allow_multiple=False)

    if result and len(result) > 0:
        path = result[0]
        UI_REFS['file_input'].value = path
        ui.notify(f'File chosen: {path}')
        UI_REFS['play_button'].enable()

        try:
            with open(path, 'r', encoding='utf-8') as fc:
                UI_REFS['file_contents'] = fc.read()
                UI_REFS['code_editor'].set_content(UI_REFS['file_contents'])
                UI_REFS['right_pane'].style('display: flex;')
                UI_REFS['toggle_button'].props('icon=chevron_right')
        except Exception as e:
            ui.notify(f'Error loading file: {e}{type(e)}', type='negative')

# UI callback functions
def update_slider_label(e):
    UI_REFS['typing_speed_value'] = int(e.value)
    UI_REFS['typing_speed_label'].set_text(f"Ghost Coding Speed: {int(e.value)} ms")

def toggle_pause_on_new_line(e):
    logger.info(f"Auto pause on new line: {e.value}")

def start_playback_paused(e):
    logger.info(f"Start playback paused: {e.value}")

def toggle_auto_home_on_newline(e):
    logger.info(f"Auto home on newline: {e.value}")

def toggle_control_on_newline(e):
    logger.info(f"Control on newline: {e.value}")

def toggle_replace_quad_spaces_with_tab(e):
    logger.info(f"Replace quad spaces with tab: {e.value}")

def start_playback():
    logger.info("Start playback clicked")
    ui.notify("Playback started")

def stop_playback():
    logger.info("Stop playback clicked")
    ui.notify("Playback stopped")

def on_advance_newline_button():
    logger.info("Advance to newline clicked")
    ui.notify("Advanced to newline")

def on_advance_token_button():
    logger.info("Advance to token clicked")
    ui.notify("Advanced to token")

def build_ui():
    """Build the main UI layout."""
    ui.timer(0.01, check_mqtt_messages)
    
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

    with ui.row().classes('w-full').style('position: relative; gap: 0;'):
        with ui.column():
            with ui.column().classes('p-4').style('gap: 0.1rem; flex: 1;'):
                ui.label("How to use this App:").classes('font-bold text-xl')
                ui.label("1. Select the target application")
                ui.label("2. Select the source code file to play back")
                ui.label("3. Adjust playback speed and settings")
                ui.label("4. Start playback and use controls")

                ui.separator().style("height:0.175rem;")

                ui.label("Source File to Play:").classes('font-bold')
                UI_REFS['file_input'] = ui.input(value='').props('readonly').classes('w-full')
                ui.button('Pick file (native PyWebview dialog)', icon='file_open', on_click=open_native_file_dialog)

                ui.separator().style("")

                UI_REFS['typing_speed_label'] = ui.label(f"Ghost Coding Speed: {UI_REFS['typing_speed_value']} ms").classes('font-bold')
                ui.slider(min=100, max=500, step=25, value=100, on_change=update_slider_label).classes('w-full')

                with ui.row():
                    ui.checkbox("Auto Pause on New Line", value=False, on_change=toggle_pause_on_new_line)
                    ui.checkbox("Start Playback Paused", value=False, on_change=start_playback_paused)

                with ui.row():
                    ui.checkbox("Auto Home on Newline", value=True, on_change=toggle_auto_home_on_newline)
                    ui.checkbox("Ctrl on Newline", value=True, on_change=toggle_control_on_newline)
                    ui.checkbox("Replace Quad Spaces with Tab", value=True, on_change=toggle_replace_quad_spaces_with_tab)

                ui.separator().style("height:0.175rem;")

                with ui.row():
                    UI_REFS['play_button'] = ui.button("START", icon='play_arrow', on_click=start_playback)
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
    args = parser.parse_args()

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
    child_processes["broker"] = mp.Process(target=broker_process, args=(broker_config, available_port), name="broker")
    child_processes["broker"].start()
    
    # Wait a moment for broker to start
    time.sleep(1)

    # Start listener process
    logger.info("Starting listener process")
    child_processes["listener"] = mp.Process(target=listener_process, args=(available_port,), name="listener")
    child_processes["listener"].start()

    # Start inspector if enabled
    if args.inspector:
        logger.info("Starting inspector process")
        child_processes["inspector"] = mp.Process(target=inspector_process, args=(available_port,), name="inspector")
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
