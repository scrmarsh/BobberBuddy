import asyncio
import ctypes
import json
import sys
import time
from collections import deque
from ctypes import wintypes
from pathlib import Path
from threading import Event, Thread

import mss
import webview
from webview.errors import WebViewException

from coordinate_reader import CoordinateReader
from app_info import ASSETS, DATA_DIR
from navigation import guidance, world_key
from game_window import MinecraftWindow, client_area
from seed_map import APP_NAME, FULLSCREEN_MODES, MAP_ZOOM_COMMANDS, VERSIONS, map_url, numeric_map_seed
from status_popup import BORDER, EDGE, RADIUS, SURFACE, overlay_box


OVERLAY_ALPHA = 210  # 82% opacity on the Windows layered window.


# Keep the provider's actual map node and controls in place. The styling
# only promotes the existing canvas and its popups into the compact window.
MINIMAP = """
(() => {
    if (location.origin !== 'https://www.chunkbase.com' ||
        location.pathname.replace(/\\/$/, '') !== '/apps/seed-map') return false;
    const map = document.querySelector('[data-testid="map-container"]');
    if (!map || !map.querySelector('canvas')) return false;
    const canvasBox = map.parentElement;
    map.dataset.bobberCanvas = '';
    canvasBox.dataset.bobberMap = '';
    const app = map.closest('astro-island') || canvasBox.parentElement.parentElement;
    app.dataset.bobberApp = '';
    for (let node = app; node && node !== document.body; node = node.parentElement)
        node.dataset.bobberAppPath = '';
    for (let node = canvasBox; node && node !== document.body; node = node.parentElement)
        node.dataset.bobberMapPath = '';
    if (!document.getElementById('bobberbuddy-style')) {
        const css = document.createElement('style');
        css.id = 'bobberbuddy-style';
        css.textContent = `
            html:not([data-bobber-edit]) body { overflow: hidden !important; }
            html:not([data-bobber-edit]) body > :not([data-bobber-map-path], #bobberbuddy-position),
            html:not([data-bobber-edit]) [data-bobber-map-path]:not([data-bobber-map]) > :not([data-bobber-map-path]) {
                display: none !important;
            }
            html:not([data-bobber-edit]) [data-bobber-map] {
                position: fixed !important; inset: 0 !important; margin: 0 !important; border: 0 !important;
                width: 100vw !important; height: 100vh !important; min-height: 0 !important;
                max-height: none !important; z-index: 2147483645 !important;
            }
            html[data-bobber-edit] body { overflow-y: auto !important; margin: 0 !important; }
            html[data-bobber-edit] :is(body, [data-bobber-app-path]:not([data-bobber-app]))
                > :not([data-bobber-app-path], [data-base-ui-portal], [data-radix-portal], [role="dialog"],
                       :has([role="dialog"], [role="listbox"], [role="menu"])) {
                display: none !important;
            }
            html[data-bobber-edit] [data-bobber-app-path]:not([data-bobber-app]) {
                width: auto !important; max-width: none !important;
                margin: 0 !important; padding: 4px !important;
            }
            html[data-bobber-edit] [data-bobber-map] {
                height: clamp(210px, 52vh, 500px) !important; min-height: 0 !important;
            }
            iframe, ins.adsbygoogle, [data-ad-slot], [data-ad-client] {
                display: none !important;
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
        map.addEventListener('click', event => {
            if (!event.altKey || !event.target.closest('canvas')) return;
            const viewport = event.target.getBoundingClientRect();
            const hash = new URLSearchParams(location.hash.slice(1));
            const scale = 16 ** (1 - Number(hash.get('zoom') ?? 0.42));
            const x = Number(hash.get('x') ?? 0), z = Number(hash.get('z') ?? 0);
            window.bobberDestination = [Math.round(x + (event.clientX - viewport.left - viewport.width/2) * scale),
                                        Math.round(z + (event.clientY - viewport.top - viewport.height/2) * scale)];
        });
        setTimeout(() => window.dispatchEvent(new Event('resize')), 0);
    }
    window.bobberLayout = () => {
        const editing = document.documentElement.hasAttribute('data-bobber-edit');
        for (const [key, value] of Object.entries({
            position: editing ? 'relative' : 'fixed', inset: editing ? 'auto' : '0',
            width: editing ? '100%' : '100vw',
            height: editing ? 'clamp(210px, 52vh, 500px)' : '100vh',
            'min-height': '0', 'max-height': 'none', margin: '0',
        })) canvasBox.style.setProperty(key, value, 'important');
        map.style.setProperty('height', '100%', 'important');
        map.style.setProperty('width', '100%', 'important');
    };
    window.bobberLayout();
    setTimeout(() => window.dispatchEvent(new Event('resize')), 0);
    return true;
})()
"""

OVERLAY_STYLE = f"""
(() => {{
    const map = document.querySelector('[data-bobber-map]');
    if (!map) return false;
    for (const [key, value] of Object.entries({{
        'box-sizing': 'border-box', 'border': '4px solid {BORDER}',
        'border-radius': '{RADIUS}px', 'overflow': 'hidden', 'background': '{SURFACE}',
    }})) map.style.setProperty(key, value, 'important');
    window.dispatchEvent(new Event('resize'));
    return true;
}})()
"""


def set_map_editing(window, editing):
    execute(window, f"""(() => {{
        document.documentElement.toggleAttribute('data-bobber-edit', {json.dumps(editing)});
        window.bobberLayout?.();
        window.scrollTo(0, 0);
        window.dispatchEvent(new Event('resize'));
    }})()""")


class PauseMap:
    """Open only after Escape from gameplay releases the game cursor."""

    def __init__(self):
        self.editing = False
        self.until = float("-inf")

    def update(self, command, *, foreground, captured, now):
        if not foreground or command == "pause_close" or (captured and self.editing):
            self.editing = False
            self.until = float("-inf")
        elif command == "pause_open":
            self.until = now + 1
        if foreground and not captured and now <= self.until:
            self.editing = True
            self.until = float("-inf")
        return self.editing


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


def set_status(window, label, *, player_marker=False):
    execute(window, f"""(() => {{
        const status = document.getElementById('bobberbuddy-position');
        if (!status) return;
        status.replaceChildren({json.dumps(label)});
        status.toggleAttribute('data-player', {json.dumps(player_marker)});
    }})()""")


def center(window, position, *, player_marker=False):
    return execute(window, """
        (() => {
            if (location.origin !== 'https://www.chunkbase.com' ||
                location.pathname.replace(/\\/$/, '') !== '/apps/seed-map') return false;
            const route = new URLSearchParams(location.hash.slice(1));
            route.set('x', %s);
            route.set('z', %s);
            location.hash = route.toString();
            if (%s) document.getElementById('bobberbuddy-position')?.setAttribute('data-player', '');
            return true;
        })()
    """ % (json.dumps(str(position[0])), json.dumps(str(position[1])), json.dumps(player_marker)))


def zoom_map(window, action):
    label = json.dumps(MAP_ZOOM_COMMANDS[action])
    return execute(window, """
        (() => {
            if (location.origin !== 'https://www.chunkbase.com' ||
                location.pathname.replace(/\\/$/, '') !== '/apps/seed-map') return false;
            const label = %s;
            const map = document.querySelector('[data-bobber-map]');
            const button = map?.querySelector(`button[aria-label="${label}"], button[title="${label}"]`);
            if (!button) return false;
            button.click();
            return true;
        })()
    """ % label)


def show_route(window, position, route):
    destination = route.get("destination")
    caption = guidance(position, destination["position"], route.get("radius", 8)) if destination else ""
    payload = json.dumps(dict(position=position, destination=destination, points=route.get("waypoints", {}),
                              caption=caption), ensure_ascii=True)
    return execute(window, """
        (() => {
            const map = document.querySelector('[data-bobber-map]');
            if (!map) return false;
            let layer = map.querySelector('[data-bobber-route]');
            if (!layer) {
                layer = document.createElementNS('http://www.w3.org/2000/svg','svg');
                layer.dataset.bobberRoute = '';
                layer.setAttribute('style','position:absolute;inset:0;width:100%%;height:100%%;pointer-events:none;z-index:20');
                map.append(layer);
            }
            const data = %s;
            const render = () => {
                if (!layer.isConnected) return;
                const hash = new URLSearchParams(location.hash.slice(1));
                const scale = 16 ** (1 - Number(hash.get('zoom') ?? 0.42));
                const width = layer.clientWidth, height = layer.clientHeight;
                const x = Number(hash.get('x') ?? data.position[0]);
                const z = Number(hash.get('z') ?? data.position[1]);
                const point = ([px,pz]) => [width/2 + (px-x)/scale, height/2 + (pz-z)/scale];
                const add = (tag, attrs, label) => {
                    let node = document.createElementNS(layer.namespaceURI, tag);
                    for (const [key,value] of Object.entries(attrs)) node.setAttribute(key, value);
                    if (label) node.textContent = label;
                    layer.append(node);
                };
                layer.replaceChildren();
                const [playerX, playerZ] = point(data.position);
                add('circle', {cx:playerX, cy:playerZ, r:6, fill:'#73f5c7', stroke:'white',
                               'stroke-width':2, 'data-bobber-player':''});
                for (const [name, value] of Object.entries(data.points)) {
                    const [cx,cy] = point(value);
                    add('circle',{cx,cy,r:4,fill:'#fbbd67',stroke:'#14171a'});
                    add('text',{x:cx+6,y:cy-5,fill:'white','font-size':11,'paint-order':'stroke',stroke:'#14171a'},name);
                }
                if (data.destination) {
                    const [x1,y1] = point(data.position);
                    const [x2,y2] = point(data.destination.position);
                    add('line',{x1,y1,x2,y2,stroke:'#83ffbf','stroke-width':3,'stroke-dasharray':'6 4'});
                    add('circle',{cx:x2,cy:y2,r:6,fill:'#83ffbf',stroke:'#14171a'});
                    add('text',{x:width/2,y:50,'text-anchor':'middle',fill:'white','font-size':14,
                                'paint-order':'stroke',stroke:'#14171a','stroke-width':4},data.caption);
                }
            };
            if (layer.bobberRender) {
                window.removeEventListener('hashchange',layer.bobberRender);
                window.removeEventListener('resize',layer.bobberRender);
            }
            layer.bobberRender = render;
            window.addEventListener('hashchange',render);
            window.addEventListener('resize',render);
            render();
            return true;
        })()
    """ % payload)


def map_world(window):
    return execute(window, """
        (() => {
            if (location.origin !== 'https://www.chunkbase.com' ||
                location.pathname.replace(/\\/$/, '') !== '/apps/seed-map') return null;
            const seedLabel = [...document.querySelectorAll('label')].find(label => label.textContent.trim() === 'Seed:');
            const platform = document.querySelector('[data-testid="platform"]');
            if (!seedLabel?.control || !platform) return null;
            return {seed: seedLabel.control.value, platform: platform.value.split('//').pop()};
        })()
    """)


def map_view_state(window):
    return execute(window, """(() => {
        const map = document.querySelector('[data-testid="map-container"]');
        return {ready: !!map?.hasAttribute('data-bobber-canvas'),
                dimension: new URLSearchParams(location.hash.slice(1)).get('dimension')};
    })()""")


async def follow(views, config, stopping, zoom_requests=None, pause_requests=None):
    game = MinecraftWindow(config["edition"])
    reader = None
    try:
        if config["follow"]:
            reader = CoordinateReader(config["edition"])
    except (ImportError, OSError, RuntimeError):
        pass
    current = None
    pending = None
    pending_reads = 0
    confirmation_misses = 0
    misses = 0
    overlay_state = None
    pause = PauseMap()
    editing_by_view = {}
    has_overlay = any(overlay for _, overlay, _ in views)
    initialized = set()
    current_center = {}
    view_dimensions = {}
    shown_status = {}
    navigation = config.get("navigation", {})
    navigation_dirty = True
    position_file = DATA_DIR / "player-position.json"
    selection_file = DATA_DIR / "map-destination.json"
    last_position_write = float("-inf")
    expected = dict(seed=numeric_map_seed(config["seed"]), platform=VERSIONS[config["edition"]][config["version"]])
    world_label = f"Chunk Base · {config['edition'].title()} {config['version']} · {config['dimension']}"

    def show_status(window, label, *, player_marker=False):
        state = (label, player_marker)
        if shown_status.get(window.uid) != state:
            set_status(window, label, player_marker=player_marker)
            shown_status[window.uid] = state

    with mss.MSS() as screen:
        while not stopping.is_set() and (active := [view for view in views if not view[2].is_set()]):
            delay = 0.23
            hwnd = game.foreground_game()
            bounds = client_area(hwnd) if hwnd else None
            command = None
            while pause_requests:
                command = pause_requests.popleft()
            editing = has_overlay and pause.update(command, foreground=bounds is not None,
                                                   captured=bool(hwnd and game.focused()),
                                                   now=time.monotonic())
            if (bounds, editing) != overlay_state:
                for window, overlay, _ in active:
                    if overlay and window.events.shown.is_set():
                        position_overlay(window, bounds, editing=editing, edition=config["edition"])
                if all(window.events.shown.is_set() for window, overlay, _ in active if overlay):
                    overlay_state = (bounds, editing)
            for window, overlay, _ in active:
                if window.uid in initialized or not window.events.shown.is_set():
                    continue
                actual = map_world(window)
                if actual and actual["seed"].strip():
                    try:
                        actual["seed"] = numeric_map_seed(actual["seed"])
                    except ValueError:
                        continue
                if actual == expected and execute(window, MINIMAP):
                    if overlay and not execute(window, OVERLAY_STYLE):
                        continue
                    initialized.add(window.uid)
                    if config["x"] is not None:
                        center(window, (config["x"], config["z"]))
                        show_status(window, f"{world_label} · X {config['x']:,}  Z {config['z']:,}")
                    if config["follow"]:
                        show_status(window, f"{world_label} · Waiting for Minecraft coordinates" if reader is not None
                                   else f"{world_label} · Windows OCR unavailable")
            ready = [(window, overlay) for window, overlay, _ in active if window.uid in initialized]
            for window, overlay in ready:
                state = map_view_state(window)
                if state:
                    dimension = {'overworld': 'Overworld', 'nether': 'Nether', 'end': 'End'}.get(
                        state.get('dimension'), config['dimension'])
                    if not state.get('ready'):
                        if not execute(window, MINIMAP):
                            initialized.discard(window.uid)
                            continue
                        if overlay:
                            execute(window, OVERLAY_STYLE)
                        editing_by_view.pop(window.uid, None)
                    if not state.get('ready') or view_dimensions.get(window.uid) != dimension:
                        current_center.pop(window.uid, None)
                        navigation_dirty = True
                    view_dimensions[window.uid] = dimension
                if overlay and editing_by_view.get(window.uid, False) != editing:
                    set_map_editing(window, editing)
                    editing_by_view[window.uid] = editing
            while ready and zoom_requests:
                action = zoom_requests.popleft()
                if action.startswith("navigation "):
                    navigation = json.loads(action.partition(" ")[2])
                    navigation_dirty = True
                    continue
                for window, overlay in ready:
                    if action == "show_window":
                        if not overlay:
                            window.restore()
                            window.show()
                    else:
                        zoom_map(window, action)
            for window, overlay in ready:
                if overlay and not editing:
                    continue
                selection = execute(window, "(() => {const point=window.bobberDestination; delete window.bobberDestination; return point;})()")
                if selection is not None:
                    try:
                        DATA_DIR.mkdir(parents=True, exist_ok=True)
                        selection_file.write_text(json.dumps(dict(world=world_key(config), position=selection)),
                                                  encoding="utf-8")
                    except OSError:
                        pass
            if hwnd is None or editing:
                # The map can be panned while Minecraft is unfocused. Recenter after returning
                # to the game even if the player is standing on the same block.
                current_center.clear()
                pending = None
                pending_reads = 0
                confirmation_misses = 0
                misses = 0
            elif reader is not None:
                strict = pending is not None and confirmation_misses < 2
                try:
                    position = await asyncio.wait_for(reader.read(screen, hwnd, confirm=strict), timeout=1.2)
                except (ImportError, OSError, RuntimeError, TimeoutError, mss.ScreenShotError):
                    position = None
                if position is None:
                    misses += 1
                    if strict:
                        confirmation_misses += 1
                        delay = 0.07
                    else:
                        pending_reads = 0
                    if misses >= 8:
                        pending = None
                        confirmation_misses = 0
                    if misses == 3:
                        for window, _ in ready:
                            show_status(window, f"{world_label} · Coordinates not readable · Last map shown")
                else:
                    misses = 0
                    # Recheck likely teleports with multiple OCR variants. Ordinary
                    # jumps near the player still use the walking update rate.
                    if current is None or max(abs(a - b) for a, b in zip(current, position)) > 48:
                        if pending == position:
                            pending_reads += 1
                        else:
                            pending_reads = 1
                            if pending is None or max(abs(a - b) for a, b in zip(pending, position)) > 24:
                                confirmation_misses = 0
                        confirmed = pending_reads >= 3 or (strict and pending is not None
                                    and max(abs(a - b) for a, b in zip(pending, position)) <= 24)
                        pending = position
                        if not confirmed:
                            delay = 0.07
                            for window, _ in ready:
                                show_status(window, f"{world_label} · Checking new coordinates")
                    else:
                        confirmed = True
                    if confirmed:
                        changed_position = position != current
                        pending = None
                        pending_reads = 0
                        confirmation_misses = 0
                        current = position
                        for window, overlay in ready:
                            dimension = view_dimensions.get(window.uid, config['dimension'])
                            label = f"Chunk Base · {config['edition'].title()} {config['version']} · {dimension}"
                            if current_center.get(window.uid) != position:
                                if not center(window, position, player_marker=overlay):
                                    continue
                                current_center[window.uid] = position
                            show_status(window, f"{label} · Player X {position[0]:,}  Z {position[1]:,}",
                                        player_marker=overlay)
                            if changed_position or navigation_dirty:
                                show_route(window, position, navigation if dimension == config['dimension'] else {})
                        if changed_position or navigation_dirty or time.monotonic() - last_position_write >= 3:
                            try:
                                DATA_DIR.mkdir(parents=True, exist_ok=True)
                                pending_file = position_file.with_suffix(".tmp")
                                pending_file.write_text(json.dumps(dict(world=world_key(config), position=position,
                                                                       observed=time.time())), encoding="utf-8")
                                pending_file.replace(position_file)
                                last_position_write = time.monotonic()
                            except OSError:
                                pass
                        navigation_dirty = False
            await asyncio.sleep(delay)


def close_views(views):
    from System import Action

    for window, _, closed in views:
        if closed.is_set() or not window.events.before_show.wait(5):
            continue
        native = window.native
        if native is not None and not native.IsDisposed:
            window.bobber_shutdown = True
            native.BeginInvoke(Action(native.Close))


def start(views, config, stopping):
    zoom_requests = deque(maxlen=32)
    pause_requests = deque(maxlen=8)

    def watch_parent():
        for line in sys.stdin:
            action = line.strip()
            if action in {"pause_open", "pause_close"}:
                pause_requests.append(action)
            elif action in MAP_ZOOM_COMMANDS or action == "show_window" or action.startswith("navigation "):
                zoom_requests.append(action)
        stopping.set()
        close_views(views)

    Thread(target=watch_parent, daemon=True).start()
    asyncio.run(follow(views, config, stopping, zoom_requests, pause_requests))


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


def configure_overlay(window, *, editing=False):
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    user32.SetLayeredWindowAttributes.argtypes = (wintypes.HWND, wintypes.DWORD, wintypes.BYTE, wintypes.DWORD)
    hwnd = window.native.Handle.ToInt64()
    styles = user32.GetWindowLongPtrW(hwnd, -20)
    # Keep Escape and the Minecraft menu in the game, even when the map takes clicks.
    styles |= 0x08080000  # WS_EX_NOACTIVATE | WS_EX_LAYERED
    styles = styles & ~0x20 if editing else styles | 0x20  # WS_EX_TRANSPARENT
    user32.SetWindowLongPtrW(hwnd, -20, styles)
    user32.SetLayeredWindowAttributes(hwnd, 0, 255 if editing else OVERLAY_ALPHA, 2)


def editing_box(bounds, edition):
    width = min(780, bounds["width"] * (44 if edition == "bedrock" else 31) // 100)
    height = min(820, bounds["height"] - 2 * EDGE)
    return (bounds["left"] + bounds["width"] - width - EDGE,
            bounds["top"] + (bounds["height"] - height) // 2, width, height)


def position_overlay(window, bounds, *, editing=False, edition="bedrock"):
    from System import Action

    native = window.native
    if native is None or native.IsDisposed:
        return
    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user32.SetThreadDpiAwarenessContext.argtypes = (wintypes.HANDLE,)
    user32.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    user32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)

    def update():
        if bounds is None:
            native.Hide()
            return
        hwnd = native.Handle.ToInt64()
        configure_overlay(window, editing=editing)
        left, top, width, height = editing_box(bounds, edition) if editing else overlay_box(bounds)
        original_dpi = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            gdi32 = ctypes.WinDLL("gdi32")
            gdi32.CreateRoundRectRgn.argtypes = (ctypes.c_int,) * 6
            gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
            gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
            region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, RADIUS * 2, RADIUS * 2)
            if region and not user32.SetWindowRgn(hwnd, region, True):
                gdi32.DeleteObject(region)
            user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), left, top, width, height, 0x0010)
            # Showing only the HWND leaves WinForms' WebView2 child hidden.
            if not native.Visible:
                native.Show()
        finally:
            if original_dpi:
                user32.SetThreadDpiAwarenessContext(original_dpi)

    try:
        native.Invoke(Action(update))
    except Exception:
        if not native.IsDisposed:
            raise


def main():
    config = json.loads(sys.stdin.readline())
    views = []
    if config["mode"] in {"window", "both"}:
        geometry_path = DATA_DIR / "map-window.json"
        geometry = {}
        try:
            saved = json.loads(geometry_path.read_text(encoding="utf-8"))
            with mss.MSS() as screen:
                on_screen = any(d["left"] <= saved["x"] < d["left"] + d["width"] - 100
                                and d["top"] <= saved["y"] < d["top"] + d["height"] - 70
                                for d in screen.monitors[1:])
            if on_screen:
                geometry = {key: saved[key] for key in ("x", "y", "width", "height")}
        except (OSError, ValueError, KeyError, mss.ScreenShotError):
            pass
        window = webview.create_window(f"{APP_NAME} Minimap · Chunk Base", map_url(config),
                                       min_size=(400, 300), background_color="#101826",
                                       **({"width": 850, "height": 660} | geometry))
        def remember_geometry():
            native = window.native
            if native is None or native.IsDisposed:
                return
            rect = native.RestoreBounds
            if rect.Width >= 400 and rect.Height >= 300:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                geometry_path.write_text(json.dumps(dict(x=rect.X, y=rect.Y, width=rect.Width, height=rect.Height)),
                                         encoding="utf-8")
            if config["mode"] == "both" and not getattr(window, "bobber_shutdown", False):
                from System import Action
                native.BeginInvoke(Action(native.Hide))
                return False
        window.events.closing += remember_geometry
        views.append((window, False, Event()))
    if config["mode"] in FULLSCREEN_MODES:
        display = config["target_display"]
        fullscreen = webview.create_window(f"{APP_NAME} Minimap · Chunk Base", map_url(config),
                                          width=760, height=640, frameless=True, easy_drag=False, background_color="#101826")
        fullscreen.events.before_show += lambda: place_map(fullscreen, display)
        views.append((fullscreen, False, Event()))
    if config["mode"] in {"overlay", "both"}:
        overlay = webview.create_window(f"{APP_NAME} Game Overlay · Chunk Base", map_url(config, overlay=True),
                                       width=360, height=360, min_size=(120, 120), frameless=True, easy_drag=False,
                                       hidden=True, on_top=True, focus=False, background_color="#101826")
        overlay.events.before_show += lambda: configure_overlay(overlay)
        views.append((overlay, True, Event()))
    for window, _, closed in views:
        window.events.closed += closed.set
    errors = []
    stopping = Event()

    def run():
        try:
            start(views, config, stopping)
        except Exception as error:
            errors.append(error)
            stopping.set()
            close_views(views)

    icon = ASSETS / "bobber.ico"
    webview.start(run, gui="edgechromium", icon=str(icon) if icon.is_file() else None)
    if errors:
        raise errors[0]


if __name__ == "__main__":
    main()
