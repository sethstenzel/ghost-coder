from nicegui import ui
from pynput import keyboard, mouse
import threading

# Shared state
state_lock = threading.Lock()
current_hotkey = None          # ('keyboard', 'Key.space') or ('keyboard', 'a') or ('mouse', 'Button.left')
pressed_count = 0
waiting_for_hotkey = False


def format_hotkey(hotkey):
    """Return a human-readable description of the hotkey tuple."""
    if hotkey is None:
        return 'None'
    source, value = hotkey
    if source == 'keyboard':
        return f'Key: {value}'
    else:
        return f'Mouse: {value}'


def format_key_event(key):
    """Convert a pynput keyboard key into a stable string representation."""
    try:
        # Letter/number keys etc.
        if hasattr(key, 'char') and key.char is not None:
            return key.char
        # Special keys like Key.space, Key.shift, etc.
        return str(key)  # e.g. 'Key.space'
    except Exception:
        return str(key)


def format_mouse_event(button):
    """Convert a pynput mouse button into a stable string."""
    return str(button)  # e.g. 'Button.left'


def keyboard_on_press(key):
    global current_hotkey, pressed_count, waiting_for_hotkey
    event_value = format_key_event(key)

    with state_lock:
        if waiting_for_hotkey:
            # Set new hotkey from this keyboard event
            current_hotkey = ('keyboard', event_value)
            pressed_count = 0
            waiting_for_hotkey = False
        else:
            # Check if this matches the current keyboard hotkey
            if current_hotkey is not None:
                source, value = current_hotkey
                if source == 'keyboard' and value == event_value:
                    pressed_count += 1


def mouse_on_click(x, y, button, pressed):
    global current_hotkey, pressed_count, waiting_for_hotkey
    # Only consider the press event (not release)
    if not pressed:
        return

    event_value = format_mouse_event(button)

    with state_lock:
        if waiting_for_hotkey:
            # Set new hotkey from this mouse event
            current_hotkey = ('mouse', event_value)
            pressed_count = 0
            waiting_for_hotkey = False
        else:
            # Check if this matches the current mouse hotkey
            if current_hotkey is not None:
                source, value = current_hotkey
                if source == 'mouse' and value == event_value:
                    pressed_count += 1


# Start global listeners in background threads
keyboard_listener = keyboard.Listener(on_press=keyboard_on_press)
keyboard_listener.start()

mouse_listener = mouse.Listener(on_click=mouse_on_click)
mouse_listener.start()


# --- NiceGUI UI ---

label = ui.label('No hotkey set yet.')
status_label = ui.label('Click "Set Hotkey" and then press a key or mouse button.')
ui.separator()


def on_set_hotkey_click():
    global waiting_for_hotkey
    with state_lock:
        waiting_for_hotkey = True
    status_label.text = 'Waiting for next keyboard or mouse input...'


ui.button('Set Hotkey', on_click=on_set_hotkey_click)


# Periodically refresh the label from shared state
def update_label():
    with state_lock:
        desc = format_hotkey(current_hotkey)
        count = pressed_count
        waiting = waiting_for_hotkey

    label.text = f'Current hotkey: {desc} | Pressed: {count}'
    if waiting:
        status_label.text = 'Waiting for next keyboard or mouse input...'
    else:
        status_label.text = 'Click "Set Hotkey" to choose a new hotkey.'


# Run the updater 10 times per second
ui.timer(0.1, update_label)

# Run as a desktop-style app (NiceGUI's native window)
ui.run(native=True, title='NiceGUI Global Hotkey Demo')
