"""XInput gamepad polling via ctypes.

Replaces the ``inputs`` package, whose Windows gamepad reader spins without sleeping (a full
CPU core while a controller is connected) and needs a module reload to detect hot-plugged pads.

Input names match the ones ``inputs`` produced, so hotkeys saved by earlier versions still work:
buttons use evdev names (``BTN_SOUTH``...), the D-pad ``ABS_HAT0X+`` / ``ABS_HAT0Y-`` style
values, and the analog triggers ``ABS_Z`` / ``ABS_RZ``.
"""

import ctypes
import sys
import time
from typing import Optional

GAMEPAD_NAME = "Microsoft X-Box 360 pad"  # the name earlier versions stored with gamepad hotkeys
MAX_PADS = 4
TRIGGER_THRESHOLD = 128  # analog triggers report 0-255
RESCAN_INTERVAL = 2.0  # XInputGetState on an empty slot is slow, so only probe them occasionally

ERROR_SUCCESS = 0
ERROR_DEVICE_NOT_CONNECTED = 1167

# wButtons bit -> input name
BUTTONS: tuple[tuple[int, str], ...] = (
    (0x0001, "ABS_HAT0Y-"),  # D-pad up
    (0x0002, "ABS_HAT0Y+"),  # D-pad down
    (0x0004, "ABS_HAT0X-"),  # D-pad left
    (0x0008, "ABS_HAT0X+"),  # D-pad right
    (0x0010, "BTN_SELECT"),  # Start (named as the inputs package did)
    (0x0020, "BTN_START"),   # Back
    (0x0040, "BTN_THUMBL"),
    (0x0080, "BTN_THUMBR"),
    (0x0100, "BTN_TL"),
    (0x0200, "BTN_TR"),
    (0x1000, "BTN_SOUTH"),   # A
    (0x2000, "BTN_EAST"),    # B
    (0x4000, "BTN_WEST"),    # X
    (0x8000, "BTN_NORTH"),   # Y
)

DLL_NAMES = ("XInput1_4.dll", "XInput9_1_0.dll", "XInput1_3.dll")


class XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", XInputGamepad)]


def _load_dll():
    if sys.platform != "win32":
        return None
    for name in DLL_NAMES:
        try:
            dll = ctypes.WinDLL(name)
        except OSError:
            continue
        dll.XInputGetState.argtypes = [ctypes.c_uint, ctypes.POINTER(XInputState)]
        dll.XInputGetState.restype = ctypes.c_uint
        return dll
    return None


def pressed_inputs(pad: XInputGamepad) -> frozenset[str]:
    """Names of all inputs held down in a gamepad state (analog sticks are ignored: they drift)."""
    held = {name for bit, name in BUTTONS if pad.wButtons & bit}
    if pad.bLeftTrigger > TRIGGER_THRESHOLD:
        held.add("ABS_Z")
    if pad.bRightTrigger > TRIGGER_THRESHOLD:
        held.add("ABS_RZ")
    return frozenset(held)


class XInputPoller:
    """Turns XInput state snapshots into press / release edges."""

    def __init__(self, dll=None):
        self._dll = dll if dll is not None else _load_dll()
        self._state = XInputState()
        self._packets: dict[int, int] = {}  # connected slot -> last packet number
        self._held: dict[int, frozenset[str]] = {}
        self._last_scan = float("-inf")

    @property
    def available(self) -> bool:
        return self._dll is not None

    def _read(self, slot: int) -> Optional[XInputState]:
        if self._dll.XInputGetState(slot, ctypes.byref(self._state)) == ERROR_SUCCESS:
            return self._state
        return None

    def connected(self) -> list[str]:
        """Names of connected gamepads (probes every slot)."""
        if not self.available:
            return []
        state = XInputState()
        return [GAMEPAD_NAME for slot in range(MAX_PADS)
                if self._dll.XInputGetState(slot, ctypes.byref(state)) == ERROR_SUCCESS]

    def poll(self) -> list[tuple[str, bool]]:
        """Return (input name, pressed) for every input that changed since the last poll."""
        if not self.available:
            return []
        now = time.monotonic()
        rescan = now - self._last_scan >= RESCAN_INTERVAL
        if rescan:
            self._last_scan = now
        events: list[tuple[str, bool]] = []
        for slot in range(MAX_PADS):
            if slot not in self._packets and not rescan:
                continue
            state = self._read(slot)
            if state is None:
                # Disconnected: release anything it was holding.
                self._packets.pop(slot, None)
                events.extend((name, False) for name in sorted(self._held.pop(slot, frozenset())))
                continue
            if self._packets.get(slot) == state.dwPacketNumber:
                continue
            self._packets[slot] = state.dwPacketNumber
            held = pressed_inputs(state.Gamepad)
            before = self._held.get(slot, frozenset())
            self._held[slot] = held
            events.extend((name, False) for name in sorted(before - held))
            events.extend((name, True) for name in sorted(held - before))
        return events
