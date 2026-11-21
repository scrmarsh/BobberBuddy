import ctypes
from collections import deque
from ctypes import wintypes

from game_window import MinecraftWindow, USER32

KEYS = tuple(f"F{i}" for i in range(1, 12))
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
GWLP_WNDPROC = -4
RESULT = ctypes.c_ssize_t
WINDOW_PROC = ctypes.WINFUNCTYPE(RESULT, wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM)
KEYBOARD_PROC = ctypes.WINFUNCTYPE(RESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
MAP_KEYS = ("=", "-", *KEYS)
VIRTUAL_KEYS = {"=": 0xBB, "-": 0xBD, **{key: 0x70 + i for i, key in enumerate(KEYS)}}
CHAT_OPEN = {0x54, 0xBF, 0x6F}  # T, slash and numpad slash
CHAT_CLOSE = {0x0D, 0x1B}  # Enter and Esc


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


class GameZoomKeys:
    """Observe map shortcuts without consuming Minecraft's keyboard input."""

    def __init__(self):
        self.bindings = {}
        self.messages = deque(maxlen=64)
        self.down = set()
        self.game = None
        self.window = None
        self.chat = False
        self.cursor_released = False
        self.hook = None
        self.callback = KEYBOARD_PROC(self.handle_message)
        USER32.SetWindowsHookExW.argtypes = (ctypes.c_int, KEYBOARD_PROC, wintypes.HMODULE, wintypes.DWORD)
        USER32.SetWindowsHookExW.restype = wintypes.HHOOK
        USER32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
        USER32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        USER32.CallNextHookEx.restype = RESULT

    def set(self, action, key, global_bindings):
        if key not in MAP_KEYS:
            raise ValueError("Choose =, -, or a key from F1 through F11.")
        if VIRTUAL_KEYS[key] in (code for name, code in self.bindings.items() if name != action):
            raise ValueError(f"{key} is already assigned to the other zoom action.")
        if any(current == key for _, current in global_bindings.values()):
            raise ValueError(f"{key} is already assigned to a fishing action.")
        self.bindings[action] = VIRTUAL_KEYS[key]

    def handle_message(self, code, message, data):
        if code == 0:
            key = ctypes.cast(data, ctypes.POINTER(wintypes.DWORD))[0]
            if message in {0x0101, 0x0105}:  # Key up, including Alt combinations
                self.down.discard(key)
            elif key not in self.down and (key in CHAT_OPEN or key in CHAT_CLOSE or key in self.bindings.values()):
                if self.window is not None and USER32.GetForegroundWindow() == self.window:
                    self.down.add(key)
                    self.messages.append(key)
        return USER32.CallNextHookEx(None, code, message, data)

    def poll(self, edition):
        if self.game is None or self.game.edition != edition:
            self.game = MinecraftWindow(edition)
            self.window = None
        window = self.game.foreground_game()
        if window is None:
            self.messages.clear()
            self.down.clear()
            return
        if window != self.window:
            self.window = window
            self.chat = False
            self.cursor_released = False
            self.messages.clear()
        if self.hook is None:
            self.hook = USER32.SetWindowsHookExW(13, self.callback, None, 0)
            if not self.hook:
                raise ctypes.WinError(ctypes.get_last_error())
        active = self.game.focused()
        if self.chat and not active:
            self.cursor_released = True
        elif active and self.cursor_released:
            self.chat = False
            self.cursor_released = False
        while self.messages:
            key = self.messages.popleft()
            if key in CHAT_OPEN:
                self.chat = True
                self.cursor_released = not active
            elif key in CHAT_CLOSE:
                self.chat = False
                self.cursor_released = False
            elif active and not self.chat:
                for action, binding in self.bindings.items():
                    if binding == key:
                        yield action
                        break

    def close(self):
        if self.hook:
            USER32.UnhookWindowsHookEx(self.hook)
            self.hook = None
        self.window = None
        self.messages.clear()
        self.down.clear()
