import ctypes
import time
import tkinter as tk
from ctypes import wintypes
from enum import Enum

from PIL import Image, ImageTk


EDGE = 20
POPUP_WIDTH = 242
POPUP_HEIGHT = 56
CROSSHAIR_GAP = 30
RADIUS = 16
BORDER = "#555860"
SURFACE = "#282a2e"
TRANSPARENT = "#010203"
FADE_IN = 0.18
HOLD = 1.35
FADE_OUT = 0.30


class FishingState(str, Enum):
    INACTIVE = "Inactive"
    VERIFYING = "Verifying"
    ACTIVE = "Active"
    PAUSED = "Paused"
    ERROR = "Error"


STATE_COLORS = {
    FishingState.INACTIVE: "#e5e5e7", FishingState.VERIFYING: "#ffe584",
    FishingState.ACTIVE: "#86e8b3", FishingState.PAUSED: "#ffb575", FishingState.ERROR: "#ff9393",
}


def overlay_box(bounds):
    width, height = min(360, bounds["width"] // 3), min(360, bounds["height"] // 3)
    return (bounds["left"] + bounds["width"] - width - EDGE,
            bounds["top"] + bounds["height"] - height - EDGE,
            width, height)


def popup_box(bounds):
    return (bounds["left"] + (bounds["width"] - POPUP_WIDTH) // 2,
            bounds["top"] + bounds["height"] // 2 + CROSSHAIR_GAP)


def rounded_box(canvas, x1, y1, x2, y2, radius, color):
    points = (x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1)
    canvas.create_polygon(points, smooth=True, splinesteps=24, fill=color)


class FishingStatusPopup:
    def __init__(self, root, artwork):
        self.root = root
        self.window = None
        self.timer = None
        self.logo = None
        with Image.open(artwork) as image:
            fitted = image.convert("RGBA")
            fitted.thumbnail((33, 33), Image.Resampling.LANCZOS)
            self.logo = ImageTk.PhotoImage(fitted, master=root)

    def create(self):
        self.window = tk.Toplevel(self.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-transparentcolor", TRANSPARENT)
        self.window.attributes("-alpha", 0.0)
        self.window.geometry(f"{POPUP_WIDTH}x{POPUP_HEIGHT}")
        canvas = tk.Canvas(self.window, width=POPUP_WIDTH, height=POPUP_HEIGHT,
                           highlightthickness=0, borderwidth=0, background=TRANSPARENT)
        canvas.pack()
        rounded_box(canvas, 0, 0, POPUP_WIDTH, POPUP_HEIGHT, RADIUS, BORDER)
        rounded_box(canvas, 3, 3, POPUP_WIDTH - 3, POPUP_HEIGHT - 3, RADIUS - 3, SURFACE)
        canvas.create_image(28, POPUP_HEIGHT // 2, image=self.logo)
        canvas.create_text(52, POPUP_HEIGHT // 2, anchor="w", text="Bobber Buddy", fill="#f3f3f4",
                           font=("Segoe UI Semibold", 11))
        self.status = canvas.create_text(POPUP_WIDTH - 16, POPUP_HEIGHT // 2, anchor="e",
                                         font=("Segoe UI Semibold", 10))
        self.canvas = canvas
        self.window.update_idletasks()

    def hide(self):
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.withdraw()

    def show(self, state, bounds, on_finished=None, *, persistent=False, guidance=None, notification=False):
        if self.window is None:
            self.create()
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        if guidance is not None:
            self.canvas.delete("all")
            rounded_box(self.canvas, 0, 0, POPUP_WIDTH, POPUP_HEIGHT, RADIUS, BORDER)
            rounded_box(self.canvas, 3, 3, POPUP_WIDTH - 3, POPUP_HEIGHT - 3, RADIUS - 3, SURFACE)
            self.canvas.create_text(POPUP_WIDTH // 2, POPUP_HEIGHT // 2, text=guidance, fill=STATE_COLORS[state],
                                    font=("Segoe UI Semibold", 12))
        else:
            self.canvas.itemconfigure(self.status, text=state.value, fill=STATE_COLORS[state])
        self.window.attributes("-alpha", 0.0)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT)
        hwnd = user32.GetAncestor(self.window.winfo_id(), 2)
        styles = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, styles | 0x080000A0)
        self.window.deiconify()
        left, top = popup_box(bounds)
        if guidance is not None:
            top = bounds["top"] + 20
        elif notification:
            top += POPUP_HEIGHT + 8
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), left, top, POPUP_WIDTH, POPUP_HEIGHT, 0x0010)
        if persistent:
            self.window.attributes("-alpha", 0.9)
            return
        began = time.monotonic()

        def animate():
            elapsed = time.monotonic() - began
            if elapsed >= FADE_IN + HOLD + FADE_OUT:
                self.timer = None
                self.window.withdraw()
                if on_finished is not None:
                    on_finished()
                return
            opacity = min(1.0, elapsed / FADE_IN, (FADE_IN + HOLD + FADE_OUT - elapsed) / FADE_OUT)
            self.window.attributes("-alpha", opacity)
            self.timer = self.root.after(16, animate)

        animate()
