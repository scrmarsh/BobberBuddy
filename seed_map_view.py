import asyncio
import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path
from threading import Event, Thread

import mss
import webview
from webview.errors import WebViewException

from coordinate_reader import CoordinateReader
from game_window import MinecraftWindow, client_area
from seed_map import map_url


# Keep the provider's actual map node and controls in place. The styling
# only promotes the existing canvas and its popups into the compact window.
MINIMAP = """
(() => {
    if (location.origin !== 'https://www.chunkbase.com' ||
        location.pathname.replace(/\\/$/, '') !== '/apps/seed-map') return false;
    const map = document.querySelector('[data-testid="map-container"]');
    if (!map || !map.querySelector('canvas')) return false;
    const canvasBox = map.parentElement;
    for (const [key, value] of Object.entries({
        position: 'fixed', inset: '0', margin: '0', border: '0',
        width: '100vw', height: '100vh', 'min-height': '0',
        'max-height': 'none', 'max-width': 'none', 'z-index': '2147483645',
    })) canvasBox.style.setProperty(key, value, 'important');
    if (!document.getElementById('bobberbuddy-style')) {
        const css = document.createElement('style');
        css.id = 'bobberbuddy-style';
        css.textContent = `
            body { overflow: hidden !important; }
            [data-bobber-recenter] .absolute:has(button[title="Save Marker"], button[aria-label="Save Marker"]) {
                visibility: hidden !important;
            }
            #bobberbuddy-position {
                position: fixed; left: 10px; bottom: 10px; z-index: 2147483646;
                border-radius: 5px; padding: 6px 9px; background: #101826ed;
                color: #fff; font: 13px 'Segoe UI', sans-serif; pointer-events: none;
            }
        `;
        document.head.append(css);
        const badge = document.createElement('div');
        badge.id = 'bobberbuddy-position';
        badge.textContent = 'Chunk Base';
        document.body.append(badge);
        canvasBox.addEventListener('pointerdown', () => delete canvasBox.dataset.bobberRecenter, true);
        setTimeout(() => window.dispatchEvent(new Event('resize')), 0);
    }
    return true;
})()
"""


def execute(window, script):
    # The WebView2 DOM can be ready while the remote page is still loading ads.
    # Use the Windows host directly so we don't depend on the site's JS bridge.
    from System import Func, Object

    native = window.native
    if native is None or native.IsDisposed:
        return None
    control = native.browser.webview

    def submit():
        return control.ExecuteScriptAsync(script) if control.CoreWebView2 is not None else None

    try:
        task = control.Invoke(Func[Object](submit))
    except Exception as exc:
        if native.IsDisposed or not control.IsHandleCreated:
            return None
        raise WebViewException(str(exc)) from exc
    if task is None:
        return None
    deadline = time.monotonic() + 3
    while not task.IsCompleted and time.monotonic() < deadline:
        time.sleep(0.03)
    if task.IsCompleted and not task.IsFaulted and not task.IsCanceled:
        return json.loads(str(task.Result))
    return None


def set_status(window, label):
    execute(window, f"document.getElementById('bobberbuddy-position')?.replaceChildren({json.dumps(label)})")


def center(window, position):
    x, z = map(str, position)
    return execute(window, """
        (() => {
            if (location.origin !== 'https://www.chunkbase.com') return false;
            const x = document.querySelector('[data-testid="map-goto-x"]');
            const z = document.querySelector('[data-testid="map-goto-z"]');
            const go = document.querySelector('[data-testid="map-goto-go"]');
            const map = document.querySelector('[data-testid="map-container"]');
            if (!x || !z || !go || !map) return false;
            const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            for (const [field, value] of [[x, %s], [z, %s]]) {
                setValue.call(field, value);
                field.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                field.dispatchEvent(new Event('change', {bubbles: true}));
            }
            map.parentElement.dataset.bobberRecenter = 'true';
            setTimeout(() => go.click(), 40);
            return true;
        })()
    """ % (json.dumps(x), json.dumps(z)))


async def follow(views, config, stopping):
    game = MinecraftWindow(config["edition"])
    reader = None
    try:
        if config["follow"]:
            reader = CoordinateReader(config["edition"])
    except (ImportError, OSError, RuntimeError):
        for window, _, _ in views:
            set_status(window, "Chunk Base · Windows OCR unavailable")
    else:
        if reader is not None:
            for window, _, _ in views:
                set_status(window, "Chunk Base · Waiting for Minecraft coordinates")
    current = None
    pending = None
    misses = 0
    overlay_bounds = object()
    with mss.MSS() as screen:
        while not stopping.is_set() and (active := [view for view in views if not view[2].is_set()]):
            hwnd = game.foreground_game()
            bounds = client_area(hwnd) if hwnd else None
            if bounds != overlay_bounds:
                for window, overlay, _ in active:
                    if overlay:
                        position_overlay(window, bounds)
                overlay_bounds = bounds
            if hwnd is not None and reader is not None:
                try:
                    position = await reader.read(screen, hwnd)
                except (ImportError, OSError, RuntimeError, mss.ScreenShotError):
                    position = None
                if position is None:
                    misses += 1
                    pending = None
                    if misses == 3:
                        for window, _, _ in active:
                            set_status(window, "Chunk Base · Coordinates not readable · Last map shown")
                else:
                    misses = 0
                    # Confirm a sudden long jump twice to filter out missed signs/digits.
                    if current is None or max(abs(a - b) for a, b in zip(current, position)) > 160:
                        confirmed = pending is not None and max(abs(a - b) for a, b in zip(pending, position)) <= 24
                        pending = position
                    else:
                        confirmed = True
                    if confirmed:
                        for window, _, _ in active:
                            set_status(window, f"Chunk Base · Player X {position[0]:,}  Z {position[1]:,}")
                        if current != position and all([center(window, position) for window, _, _ in active]):
                            current = position
            await asyncio.sleep(1.0)


def start(views, config, stopping):
    def watch_parent():
        sys.stdin.read()
        stopping.set()
        for window, _, closed in views:
            if not closed.is_set() and window.native and not window.native.IsDisposed:
                window.destroy()

    Thread(target=watch_parent, daemon=True).start()
    deadline = time.monotonic() + 35
    ready = []
    pending = list(views)
    while pending and not stopping.wait(0.5) and time.monotonic() < deadline:
        for view in pending[:]:
            window, _, closed = view
            if closed.is_set():
                pending.remove(view)
            elif window.events.shown.is_set() and execute(window, MINIMAP):
                pending.remove(view)
                ready.append(view)
                if config["x"] is not None:
                    center(window, (config["x"], config["z"]))
                    set_status(window, f"Chunk Base · X {config['x']:,}  Z {config['z']:,}")
    for window, _, closed in pending:
        if not stopping.is_set() and not closed.is_set() and window.native and not window.native.IsDisposed:
            print("Seedside could not load a map view.", file=sys.stderr, flush=True)
            window.destroy()
    if ready and not stopping.is_set():
        asyncio.run(follow(ready, config, stopping))


class MonitorInfo(ctypes.Structure):
    _fields_ = (("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                ("work", wintypes.RECT), ("flags", wintypes.DWORD))


def place_map(window, display):
    from System.Windows.Forms import FormStartPosition

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    user32.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT)

    # Capture coordinates are physical pixels, including negative monitor offsets.
    original_dpi = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        point = wintypes.POINT(display["left"] + display["width"] // 2,
                               display["top"] + display["height"] // 2)
        monitor = user32.MonitorFromPoint(point, 0)
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError("The selected map display is unavailable.")
        native = window.native
        hwnd = native.Handle.ToInt64()
        bounds = info.monitor
        native.StartPosition = FormStartPosition.Manual
        if not user32.SetWindowPos(hwnd, None, bounds.left, bounds.top,
                                   bounds.right - bounds.left, bounds.bottom - bounds.top, 0x0014):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if original_dpi:
            user32.SetThreadDpiAwarenessContext(original_dpi)


def configure_overlay(window):
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD)
    hwnd = window.native.Handle.ToInt64()
    styles = user32.GetWindowLongPtrW(hwnd, -20)
    # Layered + transparent passes mouse hits through the entire Windows overlay.
    user32.SetWindowLongPtrW(hwnd, -20, styles | 0x08080020)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)


def position_overlay(window, bounds):
    user32 = ctypes.WinDLL("user32")
    user32.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.SetThreadDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    hwnd = window.native.Handle.ToInt64()
    if bounds is None:
        user32.ShowWindowAsync(hwnd, 0)
        return
    configure_overlay(window)
    width, height = min(360, bounds["width"] // 3), min(360, bounds["height"] // 3)
    original_dpi = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        user32.SetWindowPos(hwnd, ctypes.c_void_p(-1),
                            bounds["left"] + bounds["width"] - width - 20,
                            bounds["top"] + bounds["height"] - height - 20,
                            width, height, 0x0050)
    finally:
        if original_dpi:
            user32.SetThreadDpiAwarenessContext(original_dpi)


def main():
    config = json.loads(sys.stdin.readline())
    views = []
    if config["mode"] in {"display", "both"}:
        display = config["target_display"]
        fullscreen = webview.create_window("Seedside Map · Chunk Base", map_url(config),
                                          width=760, height=640, frameless=True, background_color="#101826")
        fullscreen.events.before_show += lambda: place_map(fullscreen, display)
        views.append((fullscreen, False, Event()))
    if config["mode"] in {"overlay", "both"}:
        overlay = webview.create_window("Seedside Game Overlay · Chunk Base", map_url(config),
                                       width=360, height=360, min_size=(120, 120), frameless=True,
                                       hidden=True, on_top=True, focus=False, background_color="#101826")
        overlay.events.before_show += lambda: configure_overlay(overlay)
        views.append((overlay, True, Event()))
    for window, _, closed in views:
        window.events.closed += closed.set
    icon = Path(__file__).parent / "assets" / "bobber.ico"
    webview.start(start, (views, config, Event()), gui="edgechromium", icon=str(icon) if icon.is_file() else None)


if __name__ == "__main__":
    main()
