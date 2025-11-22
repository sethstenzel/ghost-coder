from nicegui import ui
from pynput import keyboard, mouse
from inputs import get_gamepad, devices
import threading
import time

# ----------------- Shared State -----------------

state_lock = threading.Lock()
current_hotkey = None
pressed_count = 0
waiting_for_hotkey = False

selected_source = 'keyboard'
selected_gamepad_name = None

# Collect gamepad names
gamepad_devices = devices.gamepads
gamepad_names = [
    getattr(dev, 'name', f'Gamepad {i+1}') for i, dev in enumerate(gamepad_devices)
]


# ---------------- Formatting Helpers ----------------

def format_hotkey(hotkey):
    if hotkey is None:
        return 'None'
    source, value = hotkey
    return f'{source.capitalize()}: {value}'


def format_key_event(key):
    try:
        if hasattr(key, 'char') and key.char:
            return key.char
        return str(key)
    except:
        return str(key)


def format_mouse_event(button):
    return str(button)


# ---------------- Keyboard Handler ----------------

def keyboard_on_press(key):
    global current_hotkey, pressed_count, waiting_for_hotkey

    with state_lock:
        if selected_source != 'keyboard':
            return

        event_value = format_key_event(key)

        if waiting_for_hotkey:
            current_hotkey = ('keyboard', event_value)
            pressed_count = 0
            waiting_for_hotkey = False
        else:
            if current_hotkey == ('keyboard', event_value):
                pressed_count += 1


# ---------------- Mouse Handler ----------------

def mouse_on_click(x, y, button, pressed):
    global waiting_for_hotkey, current_hotkey, pressed_count

    if not pressed:
        return

    with state_lock:
        if selected_source != 'mouse':
            return

        event_value = format_mouse_event(button)

        if waiting_for_hotkey:
            current_hotkey = ('mouse', event_value)
            pressed_count = 0
            waiting_for_hotkey = False
        else:
            if current_hotkey == ('mouse', event_value):
                pressed_count += 1


# ---------------- Gamepad Thread (Polling) ----------------

def gamepad_thread():
    global waiting_for_hotkey, current_hotkey, pressed_count

    while True:
        try:
            # Only poll when gamepad is selected to reduce CPU usage
            with state_lock:
                should_poll = selected_source == 'gamepad'

            if not should_poll:
                time.sleep(0.05)
                continue

            events = get_gamepad()
            for ev in events:
                if ev.ev_type not in ('Key', 'Absolute'):
                    continue
                if ev.state == 0:
                    continue  # only presses

                # Filter by selected device if available
                dev_name = getattr(ev.device, 'name', None)

                with state_lock:
                    if selected_source != 'gamepad':
                        continue

                    if selected_gamepad_name and dev_name != selected_gamepad_name:
                        continue

                    if waiting_for_hotkey:
                        current_hotkey = ('gamepad', ev.code)
                        pressed_count = 0
                        waiting_for_hotkey = False
                    else:
                        if current_hotkey == ('gamepad', ev.code):
                            pressed_count += 1
        except Exception as e:
            print('[gamepad thread] error:', e)
            time.sleep(0.5)  # Longer sleep on error


# Start listeners
keyboard.Listener(on_press=keyboard_on_press).start()
mouse.Listener(on_click=mouse_on_click).start()
threading.Thread(target=gamepad_thread, daemon=True).start()


# ---------------- UI ----------------

label = ui.label('No hotkey set.')
status_label = ui.label('Choose a source and set a hotkey.')
ui.separator()

# ---- Source Change Handler ----
def on_source_change(e):
    global selected_source, current_hotkey, pressed_count

    with state_lock:
        selected_source = e.value
        current_hotkey = None
        pressed_count = 0

    if e.value == 'gamepad':
        gamepad_select.set_visibility(True)
        if not gamepad_names:
            gamepad_info.set_text('No gamepads found.')
        else:
            gamepad_info.set_text('')
    else:
        gamepad_select.set_visibility(False)
        gamepad_info.set_text('')


# ---- Gamepad Change Handler ----
def on_gamepad_change(e):
    global selected_gamepad_name, current_hotkey, pressed_count

    with state_lock:
        selected_gamepad_name = e.value
        current_hotkey = None
        pressed_count = 0


source_select = ui.select(
    options=['keyboard', 'mouse', 'gamepad'],
    value='keyboard',
    label='Source',
    on_change=on_source_change
)

gamepad_select = ui.select(
    options=gamepad_names if gamepad_names else [],
    value=gamepad_names[0] if gamepad_names else None,
    label='Gamepad Device',
    on_change=on_gamepad_change
)
gamepad_select.visible = False

# Set initial gamepad if available
if gamepad_names:
    selected_gamepad_name = gamepad_names[0]

gamepad_info = ui.label('')


# ---- Set Hotkey Button ----

def on_set_hotkey():
    global waiting_for_hotkey
    with state_lock:
        waiting_for_hotkey = True

    if selected_source == 'keyboard':
        status_label.set_text('Press any keyboard key...')
    elif selected_source == 'mouse':
        status_label.set_text('Press any mouse button...')
    elif selected_source == 'gamepad':
        status_label.set_text('Press any gamepad button...')


ui.button('Set Hotkey', on_click=on_set_hotkey)


# ---- UI Updater ----

def update_ui():
    with state_lock:
        label.set_text(f'Hotkey: {format_hotkey(current_hotkey)} | Count: {pressed_count}')


ui.timer(0.2, update_ui)  # Update UI every 200ms instead of 100ms

ui.run(native=True, title='Hotkey Listener (Keyboard / Mouse / Gamepad)')
