"""Send Ctrl+Break only to the verified target's console (called by stop.ps1)."""
import ctypes
from ctypes import wintypes
import os
import sys
import time


def send_break(pid):
    if os.name != 'nt' or pid <= 0:
        return False
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.AttachConsole.argtypes = [wintypes.DWORD]
    kernel.AttachConsole.restype = wintypes.BOOL
    kernel.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    kernel.SetConsoleCtrlHandler.restype = wintypes.BOOL
    kernel.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
    kernel.FreeConsole()
    if not kernel.AttachConsole(pid):
        return False
    try:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        ignore_own_signal = callback_type(lambda event: True)
        if not kernel.SetConsoleCtrlHandler(ctypes.cast(ignore_own_signal, ctypes.c_void_p), True):
            return False
        sent = bool(kernel.GenerateConsoleCtrlEvent(1, 0))
        time.sleep(0.15)  # Keep our handler alive while Windows dispatches the event.
        return sent
    finally:
        kernel.FreeConsole()


if __name__ == '__main__':
    sys.exit(0 if send_break(int(sys.argv[1])) else 1)
