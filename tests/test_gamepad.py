"""XInput poller tests against a fake XInput DLL."""

import ctypes

from ghost_coder import gamepad
from ghost_coder.gamepad import ERROR_DEVICE_NOT_CONNECTED, ERROR_SUCCESS, GAMEPAD_NAME, XInputPoller


class FakeXInput:
    def __init__(self):
        self.pads = {}  # slot -> (packet, buttons, lt, rt)
        self.calls = []

    def XInputGetState(self, slot, state_ref):
        self.calls.append(slot)
        if slot not in self.pads:
            return ERROR_DEVICE_NOT_CONNECTED
        packet, buttons, lt, rt = self.pads[slot]
        state = ctypes.cast(state_ref, ctypes.POINTER(gamepad.XInputState)).contents
        state.dwPacketNumber = packet
        state.Gamepad.wButtons = buttons
        state.Gamepad.bLeftTrigger = lt
        state.Gamepad.bRightTrigger = rt
        return ERROR_SUCCESS


def test_press_and_release_edges():
    dll = FakeXInput()
    dll.pads[0] = (1, 0, 0, 0)
    poller = XInputPoller(dll)
    assert poller.poll() == []
    dll.pads[0] = (2, 0x1000 | 0x0001, 0, 200)  # A, D-pad up, right trigger
    assert sorted(poller.poll()) == [("ABS_HAT0Y-", True), ("ABS_RZ", True), ("BTN_SOUTH", True)]
    dll.pads[0] = (2, 0, 0, 0)  # same packet number: unchanged, not even decoded
    assert poller.poll() == []
    dll.pads[0] = (3, 0x0001, 0, 10)
    assert sorted(poller.poll()) == [("ABS_RZ", False), ("BTN_SOUTH", False)]


def test_unplug_releases_held_inputs():
    dll = FakeXInput()
    dll.pads[1] = (1, 0x4000, 0, 0)
    poller = XInputPoller(dll)
    assert poller.poll() == [("BTN_WEST", True)]
    del dll.pads[1]
    assert poller.poll() == [("BTN_WEST", False)]


def test_empty_slots_are_only_probed_on_rescan(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(gamepad.time, "monotonic", lambda: clock[0])
    dll = FakeXInput()
    dll.pads[0] = (1, 0, 0, 0)
    poller = XInputPoller(dll)
    poller.poll()
    assert dll.calls == [0, 1, 2, 3]
    dll.calls.clear()
    poller.poll()
    assert dll.calls == [0]
    dll.pads[2] = (1, 0x0010, 0, 0)
    clock[0] += gamepad.RESCAN_INTERVAL
    assert poller.poll() == [("BTN_SELECT", True)]


def test_connected_names():
    dll = FakeXInput()
    dll.pads[0] = dll.pads[3] = (1, 0, 0, 0)
    assert XInputPoller(dll).connected() == [GAMEPAD_NAME, GAMEPAD_NAME]
