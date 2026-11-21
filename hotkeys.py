import ctypes
from collections import deque
from ctypes import wintypes


KEYS = tuple(f"F{i}" for i in range(1, 12))
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
GWLP_WNDPROC = -4
RESULT = ctypes.c_ssize_t
WINDOW_PROC = ctypes.WINFUNCTYPE(RESULT, wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM)


class Hotkeys:
    """Register Windows shortcuts on the Tk thread and read their messages."""

    def __init__(self, root):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.bindings = {}
        self.messages = deque()
        root.update_idletasks()
        self.hwnd = wintypes.HWND(root.winfo_id())
        self.set_proc = self.user32.SetWindowLongPtrW
        self.set_proc.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p)
        self.set_proc.restype = ctypes.c_void_p
        self.call_proc = self.user32.CallWindowProcW
        self.call_proc.argtypes = (ctypes.c_void_p, *WINDOW_PROC._argtypes_)
        self.call_proc.restype = RESULT
        self.register = self.user32.RegisterHotKey
        self.register.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.unregister = self.user32.UnregisterHotKey
        self.unregister.argtypes = (wintypes.HWND, ctypes.c_int)
        self.callback = WINDOW_PROC(self.handle_message)
        self.previous_proc = self.set_proc(self.hwnd, GWLP_WNDPROC, self.callback)
        if self.previous_proc is None:
            raise ctypes.WinError(ctypes.get_last_error())

    def handle_message(self, hwnd, message, wparam, lparam):
        if message == WM_HOTKEY:
            self.messages.append(wparam)
            return 0
        return self.call_proc(self.previous_proc, hwnd, message, wparam, lparam)

    def set(self, action, key):
        previous = self.bindings.get(action)
        if previous is not None and previous[1] == key:
            return
        if key not in KEYS:
            raise ValueError("Choose a key from F1 through F11.")
        if any(current == key for _, current in self.bindings.values()):
            raise ValueError(f"{key} is already assigned to the other action.")
        # Use a separate ID for each key so a failed rebind leaves the old key active.
        identifier = KEYS.index(key) + 1
        if not self.register(self.hwnd, identifier, MOD_NOREPEAT, 0x70 + identifier - 1):
            raise ValueError(f"{key} is already in use. Choose another key.")
        if previous is not None:
            self.unregister(self.hwnd, previous[0])
        self.bindings[action] = (identifier, key)

    def poll(self):
        while self.messages:
            pressed = self.messages.popleft()
            for action, (identifier, _) in self.bindings.items():
                if identifier == pressed:
                    yield action
                    break

    def close(self):
        for identifier, _ in self.bindings.values():
            self.unregister(self.hwnd, identifier)
        self.bindings.clear()
        self.set_proc(self.hwnd, GWLP_WNDPROC, self.previous_proc)
