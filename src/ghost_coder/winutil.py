"""Minimal Win32 window helpers (ctypes) used by the typer for focus tracking."""

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
    user32.GetDpiForSystem.restype = wintypes.UINT

SW_RESTORE = 9
SPI_GETWORKAREA = 0x0030
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def get_foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    return user32.GetForegroundWindow() or 0


def get_window_title(hwnd: int) -> str:
    if not IS_WINDOWS or not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_work_area() -> Optional[tuple[int, int, int, int]]:
    """Primary monitor work area (excluding the taskbar) as (left, top, width, height) in DPI-independent pixels."""
    if not IS_WINDOWS:
        return None
    rect = wintypes.RECT()
    if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
        return None
    # DPI-unaware processes already get scaled values and a DPI of 96.
    scale = (user32.GetDpiForSystem() or 96) / 96
    return (round(rect.left / scale), round(rect.top / scale),
            round((rect.right - rect.left) / scale), round((rect.bottom - rect.top) / scale))


def is_window(hwnd: int) -> bool:
    return bool(IS_WINDOWS and hwnd and user32.IsWindow(hwnd))


def focus_window(hwnd: int) -> bool:
    """Bring hwnd to the foreground. Returns True if it is the foreground window afterwards.

    Windows restricts SetForegroundWindow for background processes, so we attach to the
    current foreground thread's input queue, and fall back to the ALT-key trick.
    """
    if not is_window(hwnd):
        return False
    if get_foreground_window() == hwnd:
        return True

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    fg = get_foreground_window()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    our_thread = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if fg_thread and fg_thread != our_thread:
            attached = bool(user32.AttachThreadInput(our_thread, fg_thread, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(our_thread, fg_thread, False)

    if get_foreground_window() != hwnd:
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        user32.SetForegroundWindow(hwnd)

    time.sleep(0.05)
    return get_foreground_window() == hwnd
