import ctypes
from ctypes import wintypes as win
from pathlib import PureWindowsPath


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
USER32.GetForegroundWindow.restype = win.HWND
USER32.GetWindowThreadProcessId.argtypes = (win.HWND, ctypes.POINTER(win.DWORD))
USER32.GetClassNameW.argtypes = (win.HWND, win.LPWSTR, ctypes.c_int)
USER32.GetWindowTextW.argtypes = (win.HWND, win.LPWSTR, ctypes.c_int)
USER32.IsIconic.argtypes = (win.HWND,)
USER32.IsWindowVisible.argtypes = (win.HWND,)
ENUM_WINDOWS = ctypes.WINFUNCTYPE(win.BOOL, win.HWND, win.LPARAM)
USER32.EnumWindows.argtypes = (ENUM_WINDOWS, win.LPARAM)
USER32.GetClientRect.argtypes = (win.HWND, ctypes.POINTER(win.RECT))
USER32.ClientToScreen.argtypes = (win.HWND, ctypes.POINTER(win.POINT))
SET_DPI_CONTEXT = getattr(USER32, "SetThreadDpiAwarenessContext", None)
if SET_DPI_CONTEXT is not None:
    SET_DPI_CONTEXT.argtypes = (win.HANDLE,)
    SET_DPI_CONTEXT.restype = win.HANDLE
KERNEL32.OpenProcess.argtypes = (win.DWORD, win.BOOL, win.DWORD)
KERNEL32.OpenProcess.restype = win.HANDLE
KERNEL32.QueryFullProcessImageNameW.argtypes = (win.HANDLE, win.DWORD, win.LPWSTR, ctypes.POINTER(win.DWORD))
KERNEL32.CloseHandle.argtypes = (win.HANDLE,)


class MouseInput(ctypes.Structure):
    _fields_ = (("dx", win.LONG), ("dy", win.LONG), ("data", win.DWORD),
                ("flags", win.DWORD), ("time", win.DWORD), ("extra", win.WPARAM))


class Input(ctypes.Structure):
    _fields_ = (("type", win.DWORD), ("mouse", MouseInput))


class CursorInfo(ctypes.Structure):
    _fields_ = (("size", win.DWORD), ("flags", win.DWORD),
                ("cursor", win.HANDLE), ("position", win.POINT))


USER32.GetCursorInfo.argtypes = (ctypes.POINTER(CursorInfo),)
USER32.SendInput.argtypes = (win.UINT, ctypes.POINTER(Input), ctypes.c_int)
USER32.SendInput.restype = win.UINT
RIGHT_DOWN = 0x0008
RIGHT_UP = 0x0010


def process_id(hwnd):
    pid = win.DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def client_area(hwnd):
    bounds = win.RECT()
    corner = win.POINT()
    previous = SET_DPI_CONTEXT(ctypes.c_void_p(-4)) if SET_DPI_CONTEXT is not None else None
    try:
        if not USER32.GetClientRect(hwnd, ctypes.byref(bounds)) or not USER32.ClientToScreen(hwnd, ctypes.byref(corner)):
            return None
        return dict(left=corner.x, top=corner.y, width=bounds.right, height=bounds.bottom)
    finally:
        if previous:
            SET_DPI_CONTEXT(previous)


class MinecraftWindow:
    def __init__(self, edition, area=None):
        self.edition = edition
        self.area = area
        self.identity = None
        self.recognized = False

    def foreground_game(self):
        hwnd = USER32.GetForegroundWindow()
        if not hwnd or USER32.IsIconic(hwnd) or not USER32.IsWindowVisible(hwnd):
            return None
        identity = (hwnd, process_id(hwnd))
        if identity != self.identity:
            self.identity = identity
            self.recognized = self.is_minecraft(*identity)
        return hwnd if self.recognized else None

    def visible_game(self):
        hwnd = self.foreground_game()
        if hwnd is not None:
            return hwnd
        found = None

        @ENUM_WINDOWS
        def find(hwnd, _):
            nonlocal found
            if (USER32.IsWindowVisible(hwnd) and not USER32.IsIconic(hwnd)
                    and self.is_minecraft(hwnd, process_id(hwnd))):
                found = hwnd
                return False
            return True

        USER32.EnumWindows(find, 0)
        return found

    def focused(self):
        hwnd = self.foreground_game()
        if hwnd is None:
            return False
        # Input is only useful while Minecraft has captured the mouse in gameplay.
        cursor = CursorInfo(size=ctypes.sizeof(CursorInfo))
        if not USER32.GetCursorInfo(ctypes.byref(cursor)) or cursor.flags & 1:
            return False
        bounds = client_area(hwnd)
        if bounds is None or bounds["width"] < 1 or bounds["height"] < 1:
            return False
        if self.area is None:
            return True
        left, top = self.area["left"], self.area["top"]
        return (bounds["left"] <= left and left + self.area["width"] <= bounds["left"] + bounds["width"]
                and bounds["top"] <= top and top + self.area["height"] <= bounds["top"] + bounds["height"])

    def is_minecraft(self, hwnd, pid):
        process = KERNEL32.OpenProcess(0x1000, False, pid)
        if not process:
            return False
        try:
            path = ctypes.create_unicode_buffer(32768)
            count = win.DWORD(len(path))
            if not KERNEL32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(count)):
                return False
        finally:
            KERNEL32.CloseHandle(process)
        executable = PureWindowsPath(path.value).name.casefold()
        if self.edition == "bedrock":
            return executable == "minecraft.windows.exe"
        name = ctypes.create_unicode_buffer(128)
        title = ctypes.create_unicode_buffer(256)
        USER32.GetClassNameW(hwnd, name, len(name))
        USER32.GetWindowTextW(hwnd, title, len(title))
        return (executable in {"java.exe", "javaw.exe"} and name.value in {"GLFW30", "LWJGL"}
                and title.value.casefold().startswith("minecraft"))

    def movement_pressed(self):
        return any(USER32.GetAsyncKeyState(key) & 0x8000 for key in
                   (0x57, 0x41, 0x53, 0x44, 0x20, 0x25, 0x26, 0x27, 0x28))

    def use_rod(self, stop):
        target = self.identity
        down = Input(0, MouseInput(flags=RIGHT_DOWN))
        up = Input(0, MouseInput(flags=RIGHT_UP))
        if (target is None or not self.focused() or target != self.identity or stop.is_set()
                or self.movement_pressed()
                or USER32.GetForegroundWindow() != target[0] or USER32.GetAsyncKeyState(0x02) & 0x8000):
            return False
        if USER32.SendInput(1, ctypes.byref(down), ctypes.sizeof(Input)) != 1:
            return False
        # Hold past a game tick so the click is visible to games that poll button state.
        try:
            stop.wait(0.055)
        finally:
            released = USER32.SendInput(1, ctypes.byref(up), ctypes.sizeof(Input))
        return released == 1
