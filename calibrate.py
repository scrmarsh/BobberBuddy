import ctypes
import json
import os
import sys
import time
import tkinter as tk
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Event
from tkinter import filedialog, messagebox, ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from bite import BiteDetector, FishingFrame
from game_window import MinecraftWindow, client_area
from held_rod import HeldRod, reference_from_region
from hotbar import HotbarRod, read_selected_slot
from hotkeys import GameZoomKeys, Hotkeys, KEYS, MAP_KEYS, VIRTUAL_KEYS
from matcher import Matcher, bgr
from seed_map import APP_NAME, MAP_ZOOM_COMMANDS, SeedMap, coordinate
from status_popup import FishingState, FishingStatusPopup
from app_info import ASSETS, DATA_DIR, FROZEN, VERSION
from navigation import guidance, world_key
from updates import (RELEASE_NOTES_URL, download_release, downloads_directory, is_update,
                     latest_release, release_asset, start_installer, update_result)


LOCAL = DATA_DIR
ART = ASSETS / "bobber.png"
ICON = ART.with_suffix(".ico")
SETTINGS = LOCAL / "calibration.json"
FISHING_LOG = LOCAL / "fishing-sessions.jsonl"


def default_template(edition):
    filename = f"{edition}-bobber.png"
    local = LOCAL / filename
    return local if local.is_file() else ASSETS / filename


def fishing_matcher(edition, path):
    variants = ()
    if edition == "bedrock" and path == default_template(edition):
        variants = ((ASSETS / "bedrock-vibrant-bobber.png", 0.89),
                    (ASSETS / "bedrock-vibrant-lit-bobber.png", 0.91),
                    (ASSETS / "bedrock-vibrant-level-bobber.png", 0.92))
        variants = tuple((reference, minimum) for reference, minimum in variants if reference.is_file())
    return Matcher(path, variants=variants)


EDITIONS = {"Java Edition": "java", "Bedrock Edition": "bedrock"}
EXAMPLE = {"java": "java.png", "bedrock": "bedrock.jpg"}
EXAMPLE_REGION = {"java": (610, 180, 760, 320), "bedrock": (560, 420, 720, 550)}
INITIAL_CUTOFF = {"java": 0.82, "bedrock": 0.82}
COUNTDOWN = 4
DURATION = 12
INTERVAL = 0.08
RECAST_DELAY = 0.9
RECAST_GRACE = 1.25
CAST_SETTLE = 1.4
INITIAL_CAST_DELAY = 1.8
BACKGROUND = "#101826"
PANEL = "#192536"
CARD = "#1d6177"
TEXT = "#f0f4fa"
MUTED = "#acbacb"
MINT = "#73f5c7"
WARNING = "#ffbb91"
DEFAULT_HOTKEYS = {"toggle": "F10", "emergency": "F9", "zoom_in": "=", "zoom_out": "-"}
HOTKEY_LABELS = {"toggle": "Toggle fishing", "emergency": "Emergency stop",
                 "zoom_in": "Zoom minimap in", "zoom_out": "Zoom minimap out"}


def fishing_capture_area(monitor, box, edition):
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    if not (0 <= x1 < x2 <= monitor["width"] and 0 <= y1 < y2 <= monitor["height"]
            and width <= 640 and height <= 480):
        return None
    if edition == "bedrock":
        # Allow a little room above the selection for a higher cast.
        y1 -= min(55, y1 // 2, height // 2, 480 - height)
        side_margin = min(25, width // 4, (640 - width) // 2)
        x1 = max(0, x1 - side_margin)
        x2 = min(monitor["width"], x2 + side_margin)
    return dict(left=monitor["left"] + x1, top=monitor["top"] + y1,
                width=x2 - x1, height=y2 - y1)


def automatic_capture_area(bounds):
    """Keep the search around the casting crosshair at any window size."""
    if not bounds or bounds["width"] < 240 or bounds["height"] < 160:
        return None
    width = min(640, bounds["width"] * 3 // 10)
    height = min(480, bounds["height"] * 2 // 5)
    return dict(left=bounds["left"] + (bounds["width"] - width) // 2,
                top=bounds["top"] + (bounds["height"] - height) // 2,
                width=width, height=height)


def water_in_cast_area(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, (82, 30, 32), (132, 255, 245))
    if cv2.countNonZero(blue) >= blue.size * 0.45:
        return True
    # Vibrant Visuals can shade water almost black or dark teal. Require it
    # both under the crosshair and across the nearby foreground.
    teal = cv2.inRange(hsv, (70, 38, 18), (112, 255, 155))
    height, width = teal.shape
    aim = teal[height * 3 // 8:height * 5 // 8, width * 3 // 8:width * 5 // 8]
    foreground = teal[height // 2:, width // 4:width * 3 // 4]
    if not aim.size or not foreground.size:
        return False
    aim_coverage = cv2.countNonZero(aim) / aim.size
    foreground_coverage = cv2.countNonZero(foreground) / foreground.size
    # Strong foreground coverage permits a reflection over part of the aim area.
    return ((aim_coverage >= 0.72 and foreground_coverage >= 0.60)
            or (aim_coverage >= 0.20 and foreground_coverage >= 0.82))


def capture(area, matcher, stop, threshold=None):
    if stop.wait(COUNTDOWN):
        return None
    with mss.MSS() as screen:
        if matcher is None:
            shot = screen.grab(area)
            return Image.frombytes("RGB", shot.size, shot.rgb)
        frames = []
        detector = BiteDetector(matcher, threshold)
        deadline = time.monotonic() + DURATION
        while not stop.is_set() and time.monotonic() < deadline:
            now = time.monotonic()
            frame = cv2.cvtColor(np.asarray(screen.grab(area)), cv2.COLOR_BGRA2BGR)
            frames.append((frame, detector.update(frame, now)))
            stop.wait(max(0, INTERVAL - (time.monotonic() - now)))
    return frames


def capture_rod_reference(edition, stop):
    if stop.wait(COUNTDOWN):
        return None
    game = MinecraftWindow(edition)
    if not game.focused():
        return None
    with mss.MSS() as screen:
        hwnd = game.identity[0]
        slot = read_selected_slot(screen, hwnd)
        area = client_area(hwnd)
        if slot is None or area is None:
            return None
        shot = screen.grab(area)
        return slot, Image.frombytes("RGB", shot.size, shot.rgb)


def watch(area, detector, game, auto_reel, stop, latest, rod=None, held_rod=None, uncast_rod=None):
    automatic = area is None
    reels = 0
    bites = 0
    recasts = 0
    casts = 0
    recast_at = None
    recast_target = None
    settle_until = 0
    clear_since = None
    cast_armed = True
    last_cast_attempt = float("-inf")
    with mss.MSS() as screen:
        def rod_selected():
            if rod is None:
                return False
            try:
                hwnd = game.identity[0]
                if rod.matches(read_selected_slot(screen, hwnd)):
                    return True
                return rod.matches(read_selected_slot(screen, hwnd, muted=True), muted=True)
            except mss.ScreenShotError:
                return False

        def rod_visible():
            if held_rod is None:
                return False
            try:
                return (held_rod.visible(screen, game.identity[0])
                        or (uncast_rod is not None and uncast_rod.visible(screen, game.identity[0])))
            except mss.ScreenShotError:
                return False

        def use_verified_rod():
            return (rod_selected() and rod_visible() and
                    (not automatic or area == automatic_capture_area(client_area(game.identity[0])))
                    and game.use_rod(stop))

        def stowed_hook_visible():
            try:
                return uncast_rod.visible(screen, game.identity[0])
            except mss.ScreenShotError:
                return False

        while not stop.is_set():
            now = time.monotonic()
            event = FishingFrame(None)
            focused = game.focused()
            if automatic:
                next_area = automatic_capture_area(client_area(game.identity[0])) if focused else None
                if area != next_area:
                    area = next_area
                    detector.reset()
                    recast_at = None
                    clear_since = None
                    cast_armed = True
            if not focused or area is None:
                if recast_at is not None:
                    cast_armed = True
                recast_at = None
                clear_since = None
                detector.reset()
                event = FishingFrame(None, watching=False)
            elif auto_reel and not rod_selected():
                recast_at = None
                clear_since = None
                cast_armed = True
                detector.reset()
                event = FishingFrame(None, rod_selected=False)
            elif auto_reel and not rod_visible():
                clear_since = None
                detector.reset()
                if recast_at is not None and now > recast_at + RECAST_GRACE:
                    recast_at = None
                    cast_armed = True
                event = FishingFrame(None, rod_visible=False)
            elif recast_at is not None:
                if now >= recast_at:
                    recast_at = None
                    if game.identity == recast_target and use_verified_rod():
                        recasts += 1
                        last_cast_attempt = now
                        settle_until = time.monotonic() + CAST_SETTLE
                    else:
                        cast_armed = True
                        clear_since = None
                    detector.reset()
            elif now >= settle_until:
                try:
                    frame = cv2.cvtColor(np.asarray(screen.grab(area)), cv2.COLOR_BGRA2BGR)
                except mss.ScreenShotError:
                    detector.reset()
                    clear_since = None
                    latest.append(FishingFrame(None, watching=False, reels=reels, bites=bites,
                                               recasts=recasts, casts=casts))
                    stop.wait(INTERVAL)
                    continue
                acquisition_region = None
                if automatic:
                    height, width = frame.shape[:2]
                    acquisition_region = (width // 5, height // 6, width * 4 // 5, height * 5 // 6)
                event = replace(detector.update(frame, now, acquisition_region=acquisition_region),
                                locked=detector.stable >= 4)
                if auto_reel:
                    float_visible = event.locked or (event.match is not None and event.match.score >= detector.threshold)
                    recovery_ready = uncast_rod is not None and now - last_cast_attempt >= 5
                    if float_visible:
                        clear_since = None
                    if event.locked:
                        cast_armed = False
                    if (not float_visible and (cast_armed or recovery_ready)
                            and (not automatic or uncast_rod is not None)):
                        if (not water_in_cast_area(frame)
                                or (uncast_rod is not None and not stowed_hook_visible())):
                            clear_since = None
                        elif clear_since is None:
                            clear_since = now
                        elif now - clear_since >= INITIAL_CAST_DELAY:
                            cast_armed = False
                            clear_since = None
                            last_cast_attempt = now
                            if use_verified_rod():
                                casts += 1
                                settle_until = time.monotonic() + CAST_SETTLE
                                detector.reset()
                if event.bite:
                    bites += 1
                    if auto_reel and use_verified_rod():
                        reels += 1
                        recast_target = game.identity
                        recast_at = time.monotonic() + RECAST_DELAY
            latest.append(replace(event, reels=reels, bites=bites, recasts=recasts, casts=casts))
            stop.wait(max(0, INTERVAL - (time.monotonic() - now)))
    return FishingFrame(None, reels=reels, bites=bites, recasts=recasts, casts=casts)


class Calibration:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        self.session_started = datetime.now().astimezone()
        self.session_totals = FishingFrame(None)
        self.closing = False
        self.fishing_state = FishingState.INACTIVE
        self.indicator_state = None
        self.gps_state = None
        self.next_gps_read = 0
        if ICON.is_file():
            root.iconbitmap(default=str(ICON))
        root.configure(background=BACKGROUND)
        self.configure_styles()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.update_pool = ThreadPoolExecutor(max_workers=1)
        self.update_task = None
        self.update_progress = None
        self.pending_update = None
        try:
            self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.settings = {}
        saved_edition = self.settings.get("edition")
        self.edition = tk.StringVar(value=saved_edition if saved_edition in EDITIONS else "Bedrock Edition")
        self.status_game = MinecraftWindow(EDITIONS[self.edition.get()])
        self.threshold = tk.DoubleVar()
        self.frame_index = tk.DoubleVar()
        self.status = tk.StringVar(value="Load an example or capture Minecraft to get started.")
        self.score = tk.StringVar()
        self.version_label = tk.StringVar(value=f"{APP_NAME} · v{VERSION}")
        self.update_status = tk.StringVar(value="Ready to check for updates")
        self.menu_status = tk.StringVar(value="Ready")
        self.menu_detail = tk.StringVar()
        self.catch_label = tk.StringVar(value="Estimated catches this session · 0")
        self.template_name = tk.StringVar()
        self.detect_label = tk.StringVar(value="Enable fishing")
        self.testing_bobber = False
        self.reeling = False
        self.start_on_launch = self.settings.get("fishing_on_launch", True)
        self.paths = {
            edition: Path(self.settings.get("templates", {}).get(key, default_template(key)))
            for edition, key in EDITIONS.items()
        }
        with mss.MSS() as screen:
            self.monitors = list(screen.monitors[1:])
        labels = [f"{i} · {m['width']}×{m['height']}" for i, m in enumerate(self.monitors, 1)]
        self.matcher = None
        self.scene = self.monitor_area = self.region = self.start = None
        self.rod_learning = None
        self.frames = []
        self.busy = False
        self.rendered = None
        self.capture_stop = Event()
        self.detect_stop = Event()
        self.detection = None
        self.matches = deque(maxlen=1)
        self.hotkeys = Hotkeys(root)
        self.zoom_keys = GameZoomKeys()
        self.key_labels = {name: tk.StringVar(value="—") for name in DEFAULT_HOTKEYS}
        self.rebinding = False
        self.build_menu()

        self.calibration = ttk.Frame(root)
        controls = ttk.Frame(self.calibration, padding=12)
        controls.pack(fill="x")
        chooser = ttk.Combobox(controls, textvariable=self.edition, values=list(EDITIONS), state="readonly", width=17)
        chooser.grid(row=0, column=0, sticky="w")
        chooser.bind("<<ComboboxSelected>>", self.switch_edition)
        ttk.Button(controls, text="Open template", command=self.open_template).grid(row=0, column=1, padx=4)
        ttk.Button(controls, text="Use online template", command=self.use_default).grid(row=0, column=2, padx=4)
        ttk.Button(controls, text="Online example", command=self.open_example).grid(row=0, column=3, padx=4)
        ttk.Button(controls, text="Open screenshot", command=self.open_scene).grid(row=0, column=4, padx=4)
        self.monitor = ttk.Combobox(controls, values=labels, state="readonly", width=16)
        self.monitor.grid(row=1, column=0, pady=(8, 0), sticky="w")
        self.seed_map = SeedMap(root, self.settings, self.save_settings, self.selected_monitor)
        if labels:
            saved_monitor = self.settings.get("monitor")
            self.monitor.current(self.monitors.index(saved_monitor) if saved_monitor in self.monitors else 0)
        ttk.Button(controls, text="Capture screen (4s)", command=self.snapshot).grid(row=1, column=1, pady=(8, 0))
        ttk.Button(controls, text="Sample region (12s)", command=self.sample).grid(row=1, column=2, pady=(8, 0))
        ttk.Button(controls, text="Use region as template", command=self.use_region).grid(row=1, column=3, pady=(8, 0))
        ttk.Button(controls, text="Clear region", command=self.clear_region).grid(row=1, column=4, pady=(8, 0))

        ttk.Button(controls, text="Back to menu", command=self.show_menu).grid(row=0, column=5, padx=(8, 0))
        ttk.Button(controls, text="Learn rod (4s)", command=self.learn_rod).grid(row=1, column=5, pady=(8, 0))
        tuning = ttk.Frame(self.calibration, padding=(12, 0, 12, 8))
        tuning.pack(fill="x")
        ttk.Label(tuning, text="Minimum match score").pack(side="left")
        ttk.Scale(tuning, from_=0.65, to=1, variable=self.threshold, command=self.update_score).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(tuning, textvariable=self.score, width=47).pack(side="right")
        self.canvas = tk.Canvas(self.calibration, width=800, height=320, background="#132329", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.start_region)
        self.canvas.bind("<B1-Motion>", self.drag_region)
        self.canvas.bind("<ButtonRelease-1>", self.finish_region)

        bottom = ttk.Frame(self.calibration, padding=12)
        bottom.pack(fill="x")
        ttk.Label(bottom, text="Replay frame").pack(side="left")
        self.replay = ttk.Scale(bottom, from_=0, to=0, variable=self.frame_index, command=lambda _: self.draw(), state="disabled")
        self.replay.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(bottom, text="Back to screenshot", command=self.clear_replay).pack(side="right")
        ttk.Label(self.calibration, textvariable=self.status, padding=(12, 0, 12, 10)).pack(anchor="w")
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.switch_edition()
        if self.key in self.settings.get("live_regions", {}):
            self.status.set("Capture Minecraft to reuse your saved search region.")
        self.show_menu()
        saved_keys = self.settings.get("hotkeys", {}).copy()
        if saved_keys.get("toggle") == "F8" and saved_keys.get("emergency") != "F10":
            saved_keys["toggle"] = "F10"
            self.settings["hotkeys"] = saved_keys
        for action, default in DEFAULT_HOTKEYS.items():
            try:
                key = saved_keys.get(action, default)
                self.set_hotkey(action, key)
                self.key_labels[action].set(key)
            except ValueError as error:
                self.menu_detail.set(str(error))
        self.hotkey_timer = root.after(50, self.poll_hotkeys)
        root.after_idle(self.restore_minimap)
        if self.settings.get("check_updates", True):
            root.after_idle(self.check_updates)

    def check_updates(self, *, retry_failed=False):
        if self.closing or (self.update_task is not None and not self.update_task.done()):
            return
        self.update_status.set("Checking GitHub…")
        self.update_progress = None
        auto_download = self.settings.get("download_updates", True)

        def check():
            result = latest_release()
            if result is None:
                return None, None, None
            version, release = result
            if auto_download and is_update(version):
                try:
                    archive = download_release(version, release, update_progress)
                    return version, (version, archive, release_asset(version, release)[1]), None
                except (OSError, ValueError, KeyError) as error:
                    return version, None, str(error)
            return version, None, None

        def update_progress(value):
            self.update_progress = value

        self.update_task = self.update_pool.submit(check)

        def poll():
            if self.closing:
                return
            if not self.update_task.done():
                if self.update_progress is not None:
                    self.update_status.set(f"Downloading update · {self.update_progress}%")
                self.root.after(100, poll)
                return
            try:
                version, downloaded, error = self.update_task.result()
            except (OSError, ValueError):
                self.update_status.set("GitHub unavailable. Check again later.")
                return
            status = f"Latest stable version · {version}" if version else "No stable release published"
            previous = update_result()
            if downloaded and previous.get("version") == version and previous.get("error") and not retry_failed:
                status = f"Could not install v{version} · {previous['error']}"
            elif downloaded:
                status = f"Update v{version} ready"
                if FROZEN and self.settings.get("install_updates", True):
                    if self.pending_update != downloaded:
                        self.pending_update = downloaded
                        self.root.after(500, self.install_when_idle, downloaded)
                else:
                    status += " · Open downloads folder to find the ZIP"
            elif error:
                status = f"Update v{version} · {error}"
            self.update_status.set(status)
            self.version_label.set(f"{APP_NAME} · v{VERSION}" + (f" · Update v{version}" if is_update(version) else ""))

        self.root.after(100, poll)

    def open_downloads(self):
        try:
            os.startfile(downloads_directory())
        except OSError as error:
            self.update_status.set(f"Could not open downloads · {error}")

    def install_when_idle(self, downloaded, idle_since=None):
        if self.closing or self.pending_update != downloaded:
            return
        version, archive, digest = downloaded
        if not self.settings.get("install_updates", True):
            self.pending_update = None
            self.update_status.set(f"Update v{version} ready · Open downloads folder to find the ZIP")
            return
        now = time.monotonic()
        fishing = self.fishing_state in {FishingState.VERIFYING, FishingState.ACTIVE}
        if fishing or self.testing_bobber or self.busy or self.rebinding:
            self.update_status.set(f"Update v{version} ready · waiting for fishing or calibration to finish")
            self.root.after(1000, self.install_when_idle, downloaded)
            return
        idle_since = now if idle_since is None else idle_since
        if now - idle_since < 3:
            self.root.after(1000, self.install_when_idle, downloaded, idle_since)
            return
        if self.detection is not None and not self.detection.done():
            self.detect_stop.set()
            self.root.after(250, self.install_when_idle, downloaded, idle_since)
            return
        self.pending_update = None
        self.update_status.set(f"Installing v{version} · Bobber Buddy will restart")
        try:
            self.save_settings()
            start_installer(version, archive, digest)
        except OSError as error:
            self.update_status.set(f"Could not start the update · {error}")
            return
        self.close(for_update=True)

    def open_settings(self, section="Fishing"):
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} Settings")
        dialog.transient(self.root)
        notebook = ttk.Notebook(dialog)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        tabs = {}
        for title in ("Fishing", "Map & Navigation", "Updates", "General"):
            panel = ttk.Frame(notebook, padding=12)
            notebook.add(panel, text=title)
            tabs[title] = panel
        notebook.select(tabs[section])

        def preference(tab, label, key):
            value = tk.BooleanVar(value=self.settings.get(key, True))
            def save():
                self.settings[key] = value.get()
                self.save_settings()
            ttk.Checkbutton(tabs[tab], text=label, variable=value, command=save).pack(anchor="w", pady=4)

        preference("Fishing", "Fishing notifications", "fishing_notifications")
        preference("Fishing", "Crosshair status indicator", "crosshair_status")
        ttk.Button(tabs["Fishing"], text="Change activation hotkey", command=lambda: self.rebind("toggle")).pack(fill="x")
        ttk.Button(tabs["Fishing"], text="Fishing calibration…", command=lambda: (dialog.destroy(), self.show_calibration())).pack(fill="x")
        preference("General", "Enable automatic fishing on launch", "fishing_on_launch")
        preference("Map & Navigation", "Show GPS HUD", "gps_hud")
        ttk.Button(tabs["Map & Navigation"], text="Minimap settings…",
                   command=lambda: self.seed_map.open_settings(self.edition.get())).pack(fill="x")
        ttk.Button(tabs["Map & Navigation"], text="Waypoints and destination…",
                   command=self.seed_map.open_waypoints).pack(fill="x")
        preference("Updates", "Check GitHub for updates on launch", "check_updates")
        preference("Updates", "Download verified updates automatically", "download_updates")
        preference("Updates", "Install updates and restart when idle", "install_updates")
        ttk.Label(tabs["Updates"], textvariable=self.version_label).pack(anchor="w")
        ttk.Label(tabs["Updates"], textvariable=self.update_status).pack(anchor="w", pady=4)
        ttk.Button(tabs["Updates"], text="Check for updates now",
                   command=lambda: self.check_updates(retry_failed=True)).pack(fill="x")
        ttk.Button(tabs["Updates"], text="Open downloads folder", command=self.open_downloads).pack(fill="x")
        ttk.Button(tabs["Updates"], text="View release notes",
                   command=lambda: webbrowser.open(RELEASE_NOTES_URL)).pack(fill="x")

    def restore_minimap(self):
        if self.closing:
            return
        try:
            self.seed_map.open_startup_overlay()
        except (OSError, ValueError, mss.ScreenShotError) as error:
            self.menu_detail.set(f"Minimap unavailable: {error}")

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BACKGROUND, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TButton", background="#293a51", bordercolor="#3e536d", padding=(12, 7))
        style.map("TButton", background=[("active", "#3a526d"), ("disabled", PANEL)])
        style.configure("Accent.TButton", background=MINT, foreground="#102820",
                        font=("Segoe UI Semibold", 11), bordercolor=MINT)
        style.map("Accent.TButton", background=[("active", "#a0ffde"), ("disabled", PANEL)])
        style.configure("TEntry", fieldbackground="#223248", foreground=TEXT, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground="#223248", arrowcolor=MUTED, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", "#223248")],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", "#223248")],
                  selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#3a526d")

    def build_menu(self):
        self.menu = ttk.Frame(self.root, padding=(20, 16))
        self.menu.columnconfigure(0, weight=2)
        self.menu.columnconfigure(1, weight=3)
        self.menu.rowconfigure(1, weight=1)
        ttk.Label(self.menu, text=APP_NAME, font=("Segoe UI Semibold", 22)).grid(
            row=0, column=0, sticky="w", pady=(0, 14))
        ttk.Button(self.menu, text="Minimap", command=lambda: self.seed_map.open_settings(self.edition.get())).grid(
            row=0, column=1, sticky="e", pady=(0, 14))

        card = tk.Frame(self.menu, background=CARD, highlightbackground="#28505c", highlightthickness=1)
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        art_path = ART if ART.is_file() else ART.with_name("night-fishing-bobber.png")
        self.fishing_popup = FishingStatusPopup(self.root, art_path)
        self.fishing_indicator = FishingStatusPopup(self.root, art_path)
        self.gps_indicator = FishingStatusPopup(self.root, art_path)
        with Image.open(art_path) as image:
            self.art = ImageTk.PhotoImage(image.resize((216, 216), Image.Resampling.LANCZOS))
        tk.Label(card, image=self.art, background=CARD, borderwidth=0).pack(pady=(4, 9))
        tk.Label(card, textvariable=self.menu_status, background=CARD, foreground=MINT,
                 font=("Segoe UI Semibold", 17)).pack()
        tk.Label(card, textvariable=self.menu_detail, background=CARD, foreground=MUTED, wraplength=230,
                 font=("Segoe UI", 10)).pack(padx=10, pady=(5, 13))
        tk.Label(card, textvariable=self.catch_label, background=CARD, foreground=TEXT,
                 font=("Segoe UI Semibold", 10)).pack(padx=8, pady=(0, 9))

        panel = tk.Frame(self.menu, background=PANEL, highlightbackground="#32465e", highlightthickness=1)
        panel.grid(row=1, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        for row, label in ((0, "Edition"), (2, "Bobber template"), (4, "Hotkeys")):
            tk.Label(panel, text=label, background=PANEL, foreground=TEXT, font=("Segoe UI Semibold", 11)).grid(
                row=row, column=0, columnspan=3, sticky="w", padx=14, pady=(12, 4))
        edition = ttk.Combobox(panel, textvariable=self.edition, values=list(EDITIONS), state="readonly")
        edition.grid(row=1, column=0, columnspan=3, sticky="ew", padx=14)
        edition.bind("<<ComboboxSelected>>", self.switch_edition)
        tk.Label(panel, textvariable=self.template_name, background="#223248", foreground=TEXT,
                 anchor="w", padx=10, font=("Segoe UI", 10), width=18).grid(
            row=3, column=0, columnspan=2, sticky="nsew", padx=(14, 7))
        ttk.Button(panel, text="Change", command=self.open_template).grid(row=3, column=2, sticky="e", padx=(0, 14))
        for row, (action, label) in enumerate(HOTKEY_LABELS.items(), 5):
            tk.Label(panel, text=label, background=PANEL, foreground=TEXT, font=("Segoe UI", 10)).grid(
                row=row, column=0, sticky="w", padx=(14, 4), pady=(3, 8))
            tk.Label(panel, textvariable=self.key_labels[action], background="#243448",
                     foreground=WARNING if action == "emergency" else MINT, font=("Consolas", 11, "bold"),
                     width=4, padx=4, pady=4).grid(row=row, column=1, padx=(0, 7), pady=(3, 8))
            ttk.Button(panel, text="Rebind", command=lambda name=action: self.rebind(name)).grid(
                row=row, column=2, sticky="ew", padx=(0, 14), pady=(3, 8))
        self.test_button = ttk.Button(panel, text="Test bobber", command=lambda: self.toggle_detection(test=True))
        self.test_button.grid(row=9, column=2, sticky="ew", padx=(0, 14), pady=(2, 10))
        ttk.Button(self.menu, text="Calibrate", command=self.show_calibration).grid(
            row=2, column=0, sticky="ew", padx=(0, 14), pady=(14, 0))
        self.detect_button = ttk.Button(self.menu, textvariable=self.detect_label, style="Accent.TButton",
                                        command=self.toggle_detection)
        self.detect_button.grid(row=2, column=1, sticky="ew", pady=(14, 0))
        actions = ttk.Frame(self.menu)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        ttk.Button(actions, text="Open Map Window",
                   command=lambda: self.seed_map.open_window(self.edition.get())).pack(side="left")
        ttk.Button(actions, text="Settings", command=self.open_settings).pack(side="left", padx=6)
        ttk.Button(actions, textvariable=self.version_label,
                   command=lambda: self.open_settings("Updates")).pack(side="right")

    def show_menu(self):
        self.rod_learning = None
        self.calibration.pack_forget()
        self.menu.pack(fill="both", expand=True)
        self.root.minsize(660, 570)
        self.root.geometry("690x590")
        self.update_menu()

    def show_calibration(self):
        if self.reeling and not self.detect_stop.is_set():
            self.start_on_launch = True
        self.detect_stop.set()
        self.menu.pack_forget()
        self.calibration.pack(fill="both", expand=True)
        self.root.minsize(820, 510)
        self.root.geometry("930x650")
        if self.scene is None and not self.settings.get("live_regions", {}).get(self.key):
            if (LOCAL / EXAMPLE[self.key]).is_file():
                self.open_example()
        self.draw()

    def update_menu(self):
        path = self.paths[self.edition.get()]
        if self.matcher is None:
            name = "Choose a template"
        elif path == LOCAL / f"{self.key}-in-game.png":
            name = "In-game bobber"
        elif path == default_template(self.key):
            name = "Default reference"
        else:
            name = path.name
        self.template_name.set(name)
        if self.detection is not None:
            return
        ready = self.matcher is not None and all(
            self.rod_reference(kind).is_file() for kind in ("hotbar", "held"))
        self.menu_status.set("Ready" if ready else "Setup needed")
        self.menu_detail.set("Equip your fishing rod and aim at open water." if ready else
                             "Choose a bobber template and learn your rod in Calibrate.")

    def rebind(self, action):
        self.rebinding = True
        dialog = tk.Toplevel(self.root, background=BACKGROUND)
        dialog.title("Rebind hotkey")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack()
        ttk.Label(body, text=HOTKEY_LABELS[action]).pack(anchor="w")
        selected = tk.StringVar(value=self.key_labels[action].get())
        options = MAP_KEYS if action in MAP_ZOOM_COMMANDS else KEYS
        picker = ttk.Combobox(body, state="readonly", values=options, textvariable=selected)
        picker.pack(fill="x", pady=8)
        error_text = tk.StringVar()
        ttk.Label(body, textvariable=error_text, foreground=WARNING, wraplength=260).pack(anchor="w")

        def dismiss():
            dialog.destroy()
            self.rebinding = False

        def apply():
            key = selected.get()
            try:
                self.set_hotkey(action, key)
            except ValueError as error:
                error_text.set(str(error))
                return
            self.key_labels[action].set(key)
            self.settings.setdefault("hotkeys", {})[action] = key
            self.save_settings()
            dismiss()

        actions = ttk.Frame(body)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Cancel", command=dismiss).pack(side="right")
        ttk.Button(actions, text="Save", style="Accent.TButton", command=apply).pack(side="right", padx=(0, 6))
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        dialog.bind("<Escape>", lambda _: dismiss())
        dialog.grab_set()
        picker.focus_set()

    def set_hotkey(self, action, key):
        if action in MAP_ZOOM_COMMANDS:
            self.zoom_keys.set(action, key, self.hotkeys.bindings)
        else:
            if VIRTUAL_KEYS.get(key) in self.zoom_keys.bindings.values():
                raise ValueError(f"{key} is already assigned to a zoom action.")
            self.hotkeys.set(action, key)

    def detection_area(self):
        monitor = self.selected_monitor()
        if monitor is None:
            return None
        box = self.saved_region(monitor)
        if box is None:
            return None
        return fishing_capture_area(monitor, box, self.key)

    def selected_monitor(self):
        index = self.monitor.current()
        return self.monitors[index] if index >= 0 else None

    def rod_reference(self, kind, *, custom=False):
        name = f"{self.key}-{kind}-rod.png"
        local = LOCAL / name
        return local if custom or local.is_file() else ASSETS / name

    def learn_rod(self):
        if not messagebox.askokcancel("Learn fishing rod",
                                     "Select the fishing rod in Minecraft. Click OK, then return to the game "
                                     "within four seconds. Keep the hotbar and first-person rod visible."):
            return

        def show_rod(captured):
            if captured is None:
                self.status.set("Rod slot not readable. Show the hotbar, select the rod, and try again.")
                return
            slot, scene = captured
            self.set_scene(scene, rod_slot=slot)
            self.status.set("Drag a tight box around the reel and handle of the rod you are holding.")

        self.start_capture(capture_rod_reference, (self.key, self.capture_stop), show_rod)

    def save_rod_references(self, box):
        try:
            reference = reference_from_region(self.scene, box)
            HeldRod(reference)
            LOCAL.mkdir(exist_ok=True)
            if not (cv2.imwrite(str(self.rod_reference("held", custom=True)), reference)
                    and cv2.imwrite(str(self.rod_reference("hotbar", custom=True)), self.rod_learning)):
                raise OSError("Could not save the rod references.")
        except (OSError, ValueError, cv2.error) as error:
            self.status.set(str(error))
            return
        self.rod_learning = None
        self.start_on_launch = True
        self.status.set("Both rod references saved. Fishing will start when you return to the menu.")

    def toggle_detection(self, *, test=False):
        if self.detection is not None:
            self.detect_stop.set()
            if self.reeling:
                self.set_fishing_state(FishingState.INACTIVE)
            return True
        if self.busy or self.rebinding or not self.menu.winfo_ismapped():
            return
        area = self.detection_area()
        if self.matcher is None:
            self.menu_status.set("Setup needed")
            self.menu_detail.set("Choose a bobber template in Calibrate.")
            return
        reeling = not test
        rod = None
        held_rod = None
        uncast_rod = None
        if reeling:
            path = self.rod_reference("hotbar")
            held_path = self.rod_reference("held")
            reference = cv2.imread(str(path)) if path.is_file() else None
            try:
                if reference is None or reference.shape[:2] != (40, 40):
                    raise ValueError("Hotbar reference unavailable.")
                held_rod = HeldRod(cv2.imread(str(held_path), cv2.IMREAD_UNCHANGED) if held_path.is_file() else None)
            except ValueError:
                self.menu_status.set("Rod setup needed")
                self.menu_detail.set("Use Learn rod in Calibrate to save the hotbar icon and held rod.")
                return
            alternate_path = ASSETS / f"{self.key}-muted-rod.png"
            alternate = (cv2.imread(str(alternate_path))
                         if path.parent == ASSETS and alternate_path.is_file() else None)
            rod = HotbarRod(reference, alternate)
            uncast_path = self.rod_reference("uncast")
            if uncast_path.is_file():
                try:
                    uncast_rod = HeldRod(cv2.imread(str(uncast_path), cv2.IMREAD_UNCHANGED))
                except ValueError:
                    pass
        self.detect_stop.clear()
        self.matches.clear()
        self.testing_bobber = test
        self.reeling = reeling
        if test:
            self.test_button.configure(text="Stop test")
            self.detect_button.state(["disabled"])
        else:
            self.detect_label.set("Disable fishing")
            self.test_button.state(["disabled"])
        self.menu_status.set("Scanning for bobber")
        self.menu_detail.set("Test only · Return to Minecraft and cast." if test else
                             "Return to Minecraft to check the bobber.")
        self.detection = self.pool.submit(watch, area, BiteDetector(self.matcher, self.threshold.get()),
                                          MinecraftWindow(self.key, area), self.reeling,
                                          self.detect_stop, self.matches, rod, held_rod, uncast_rod)
        if self.reeling:
            self.set_fishing_state(FishingState.VERIFYING)
        return True

    def show_fishing_status(self, state, on_finished=None):
        hwnd = MinecraftWindow(self.key).visible_game()
        area = client_area(hwnd) if hwnd is not None else None
        if area is None:
            area = self.selected_monitor()
        if area is None:
            area = dict(left=0, top=0, width=self.root.winfo_screenwidth(),
                        height=self.root.winfo_screenheight())
        try:
            self.fishing_popup.show(state, area, on_finished, notification=self.settings.get("crosshair_status", True))
        except (OSError, tk.TclError):
            if on_finished is not None:
                on_finished()

    def set_fishing_state(self, state):
        if self.fishing_state == state:
            return
        self.fishing_state = state
        if self.settings.get("fishing_notifications", True) and state != FishingState.VERIFYING:
            self.show_fishing_status(state)

    def refresh_fishing_indicator(self):
        hwnd = self.status_game.foreground_game() if self.status_game.edition == self.key else None
        if self.status_game.edition != self.key:
            self.status_game = MinecraftWindow(self.key)
            hwnd = self.status_game.foreground_game()
        bounds = client_area(hwnd) if hwnd and self.settings.get("crosshair_status", True) else None
        state = (self.fishing_state, bounds)
        if state == self.indicator_state:
            return
        self.indicator_state = state
        try:
            if bounds:
                self.fishing_indicator.show(self.fishing_state, bounds, persistent=True)
            else:
                self.fishing_indicator.hide()
        except (OSError, tk.TclError):
            pass

    def refresh_gps_indicator(self):
        now = time.monotonic()
        if now < self.next_gps_read:
            return
        self.next_gps_read = now + 0.25
        bounds = None
        caption = ""
        config = self.settings.get("seed_map", {})
        if config.get("seed") and self.seed_map.is_open():
            world = world_key(config)
            route_key = json.dumps(world, ensure_ascii=False)
            route = self.settings.get("navigation", {}).get(route_key, {})
            selection = LOCAL / "map-destination.json"
            if selection.exists():
                try:
                    requested = json.loads(selection.read_text(encoding="utf-8"))
                    if tuple(requested["world"]) == world:
                        px, pz = requested["position"]
                        route["destination"] = dict(position=[coordinate(px), coordinate(pz)], name="")
                        self.settings.setdefault("navigation", {})[route_key] = route
                        self.save_settings()
                        self.seed_map.send("navigation " + json.dumps(route))
                    selection.unlink()
                except (OSError, KeyError, ValueError):
                    pass
            destination = route.get("destination")
            hwnd = self.status_game.foreground_game() if destination and self.settings.get("gps_hud", True) else None
            if hwnd:
                try:
                    fix = json.loads((LOCAL / "player-position.json").read_text(encoding="utf-8"))
                    if tuple(fix["world"]) == world and time.time() - fix["observed"] <= 10:
                        bounds = client_area(hwnd)
                        caption = guidance(fix["position"], destination["position"], route.get("radius", 8))
                except (OSError, KeyError, ValueError):
                    pass
        state = (caption, bounds)
        if self.gps_state != state:
            self.gps_state = state
            if bounds:
                self.gps_indicator.show(FishingState.ACTIVE, bounds, persistent=True, guidance=caption)
            else:
                self.gps_indicator.hide()

    def tally_run(self, result):
        totals = self.session_totals
        self.session_totals = FishingFrame(None, reels=totals.reels + result.reels,
                                           bites=totals.bites + result.bites, recasts=totals.recasts + result.recasts,
                                           casts=totals.casts + result.casts)
        self.catch_label.set(f"Estimated catches this session · {self.session_totals.reels}")

    def poll_hotkeys(self):
        if self.seed_map.is_open():
            edition = self.settings.get("seed_map", {}).get("edition", self.key)
            try:
                for action in self.zoom_keys.poll(edition):
                    self.seed_map.send(action)
            except OSError as error:
                self.menu_detail.set(f"Minimap shortcuts unavailable: {error}")
        else:
            self.zoom_keys.close()
        pressed = set(self.hotkeys.poll())
        launch = self.start_on_launch and self.detection is None and self.menu.winfo_ismapped()
        if launch or pressed.intersection(("toggle", "emergency")):
            self.start_on_launch = False
        if "emergency" in pressed:
            self.detect_stop.set()
            self.capture_stop.set()
            if self.reeling:
                self.set_fishing_state(FishingState.INACTIVE)
        elif "toggle" in pressed or launch:
            if not self.toggle_detection() and (launch or not self.busy):
                self.set_fishing_state(FishingState.ERROR)
        if self.detection is not None:
            if self.detection.done():
                completed = self.detection
                self.detection = None
                self.detect_label.set("Enable fishing")
                self.test_button.configure(text="Test bobber")
                for control in (self.test_button, self.detect_button):
                    control.state(["!disabled"])
                self.update_menu()
                error = completed.exception()
                if error is not None:
                    self.set_fishing_state(FishingState.ERROR)
                    self.menu_status.set("Capture failed")
                    self.menu_detail.set(str(error))
                elif self.testing_bobber:
                    self.menu_status.set("Test complete")
                    self.menu_detail.set(f"Bites detected · {completed.result().bites}")
                elif self.reeling:
                    self.tally_run(completed.result())
                    self.set_fishing_state(FishingState.INACTIVE)
                    self.menu_status.set("Fishing disabled")
                self.testing_bobber = False
                self.reeling = False
            elif self.matches:
                event = self.matches.pop()
                if self.reeling and not self.detect_stop.is_set():
                    if not (event.watching and event.rod_selected and event.rod_visible):
                        self.set_fishing_state(FishingState.PAUSED)
                    else:
                        self.set_fishing_state(FishingState.ACTIVE)
                match = event.match
                score = match.score if match else 0
                if not event.watching:
                    self.menu_status.set("Fishing paused")
                    detail = "Resume Minecraft and keep the game in the foreground."
                elif not event.rod_selected:
                    self.menu_status.set("Fishing paused")
                    detail = "Waiting to verify the selected rod in the hotbar."
                elif not event.rod_visible:
                    self.menu_status.set("Fishing paused")
                    detail = "Rod selected · Waiting for the held rod to appear."
                else:
                    if event.bite:
                        self.menu_status.set("Bite detected")
                    elif event.bubbles:
                        self.menu_status.set("Bubbles approaching")
                    else:
                        self.menu_status.set("Bobber locked" if event.locked else "Scanning for bobber")
                    detail = f"Match {score:.3f} · minimum {self.threshold.get():.3f}"
                    if self.testing_bobber:
                        detail += f"\nTest only · Bites {event.bites}"
                if self.reeling and event.bites:
                    self.catch_label.set(f"Estimated catches this session · {self.session_totals.reels + event.reels}")
                    if event.reels < event.bites:
                        detail += f"\nBlocked reels · {event.bites - event.reels}"
                    detail += f"\nRecasts · {event.recasts}"
                self.menu_detail.set(detail)
        self.refresh_fishing_indicator()
        self.refresh_gps_indicator()
        self.hotkey_timer = self.root.after(50, self.poll_hotkeys)

    @property
    def key(self):
        return EDITIONS[self.edition.get()]

    def switch_edition(self, _=None):
        self.detect_stop.set()
        self.rod_learning = None
        self.threshold.set(self.settings.get("thresholds", {}).get(self.key, INITIAL_CUTOFF[self.key]))
        if self.monitor_area is not None:
            self.region = self.saved_region(self.monitor_area)
        self.matcher = None
        self.load_template(self.paths[self.edition.get()])
        if self.matcher is None:
            self.clear_replay()
        self.update_menu()

    def load_template(self, path):
        try:
            matcher = fishing_matcher(self.key, path)
        except (OSError, ValueError) as error:
            self.status.set(f"Template unavailable. Open a bobber crop. {error}")
            return
        self.matcher = matcher
        self.detect_stop.set()
        self.paths[self.edition.get()] = path
        self.clear_replay()
        self.status.set(f"Template: {path.name}. Drag on the screenshot to set a search region.")
        self.update_menu()

    def open_template(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            self.load_template(Path(path))

    def use_default(self):
        self.load_template(default_template(self.key))

    def open_example(self):
        self.load_scene(LOCAL / EXAMPLE[self.key], EXAMPLE_REGION[self.key])

    def open_scene(self):
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp")])
        if path:
            self.load_scene(Path(path))

    def load_scene(self, path, region=None):
        try:
            with Image.open(path) as image:
                self.set_scene(image.convert("RGB"), region=region)
        except OSError as error:
            messagebox.showerror("Screenshot unavailable", str(error))

    def set_scene(self, scene, monitor_area=None, region=None, *, rod_slot=None):
        self.rod_learning = rod_slot
        self.scene = scene
        self.monitor_area = monitor_area
        self.region = region
        self.clear_replay()
        self.status.set("Drag around the water where the bobber lands. Keep the game camera still for sampling.")

    def clear_region(self):
        if self.monitor_area is not None:
            self.settings.setdefault("live_regions", {}).pop(self.key, None)
        self.region = None
        self.clear_replay()

    def clear_replay(self):
        self.frames = []
        self.replay.configure(to=0, state="disabled")
        self.frame_index.set(0)
        self.scan_scene()

    def scan_scene(self):
        self.result = None
        if (self.rod_learning is None and self.scene is not None and self.matcher is not None
                and (self.region or self.monitor_area is None)):
            region = self.region or (0, 0, *self.scene.size)
            self.result = self.matcher.find(bgr(self.scene.crop(region)))
        self.draw()

    def start_region(self, event):
        if self.scene is None or self.frames:
            return
        self.start = (event.x, event.y)

    def drag_region(self, event):
        if self.start is not None:
            self.canvas.delete("drag")
            self.canvas.create_rectangle(*self.start, event.x, event.y, outline="#faf0aa", width=2, tags="drag")

    def finish_region(self, event):
        if self.start is None:
            return
        x, y, scale = self.offset
        width, height = self.scene.size
        x1, x2 = sorted(max(0, min(width, round((p - x) / scale))) for p in (self.start[0], event.x))
        y1, y2 = sorted(max(0, min(height, round((p - y) / scale))) for p in (self.start[1], event.y))
        self.start = None
        self.canvas.delete("drag")
        if min(x2 - x1, y2 - y1) >= 4:
            if self.rod_learning is not None:
                self.save_rod_references((x1, y1, x2, y2))
                return
            self.region = (x1, y1, x2, y2)
            if self.monitor_area is not None:
                self.settings.setdefault("live_regions", {})[self.key] = {
                    "monitor": self.monitor_area, "box": self.region,
                }
            self.scan_scene()

    def use_region(self):
        if self.region is None:
            self.status.set("Drag a tight rectangle around the bobber first.")
            return
        crop = self.scene.crop(self.region)
        if min(crop.size) < 4 or max(crop.size) > 256:
            self.status.set("Select a bobber crop between 4 and 256 pixels per side.")
            return
        LOCAL.mkdir(exist_ok=True)
        path = LOCAL / f"{self.key}-in-game.png"
        crop.save(path)
        self.clear_region()
        self.load_template(path)

    def snapshot(self):
        if self.monitor.current() < 0:
            self.status.set("Select a monitor first.")
            return
        area = self.monitors[self.monitor.current()]
        region = self.saved_region(area)
        self.run_capture(area, None, lambda scene: self.set_scene(scene, area, region))

    def saved_region(self, area):
        saved = self.settings.get("live_regions", {}).get(self.key, {})
        return tuple(saved["box"]) if saved.get("monitor") == area else None

    def sample(self):
        if self.monitor_area is None or self.region is None or self.matcher is None:
            self.status.set("Capture the game screen and drag a search region before sampling.")
            return
        area = fishing_capture_area(self.monitor_area, self.region, self.key)
        if area is None:
            self.status.set("Select a smaller region, up to 640 × 480 pixels.")
            return
        self.run_capture(area, self.matcher, self.set_replay)

    def run_capture(self, area, matcher, on_done):
        self.start_capture(capture, (area, matcher, self.capture_stop, self.threshold.get()), on_done)

    def start_capture(self, task, arguments, on_done):
        if self.busy:
            return
        self.busy = True
        self.capture_stop.clear()
        self.root.withdraw()
        future = self.pool.submit(task, *arguments)

        def poll():
            if not future.done():
                self.root.after(100, poll)
                return
            self.busy = False
            self.root.deiconify()
            self.root.lift()
            try:
                result = future.result()
                if self.capture_stop.is_set():
                    self.status.set("Capture stopped.")
                else:
                    on_done(result)
            except Exception as error:
                messagebox.showerror("Capture failed", str(error))

        self.root.after(100, poll)

    def set_replay(self, frames):
        self.frames = frames
        self.frame_index.set(0)
        self.replay.configure(to=max(0, len(frames) - 1), state="normal" if len(frames) > 1 else "disabled")
        bites = sum(event.bite for _, event in frames)
        wakes = sum(event.bubbles for _, event in frames)
        self.status.set(f"Replay only · {bites} bite events · {wakes} bubble frames · Cues use the captured cutoff")
        self.draw()

    def update_score(self, _=None):
        self.settings.setdefault("thresholds", {})[self.key] = self.threshold.get()
        self.draw()

    def draw(self):
        self.canvas.delete("all")
        cutoff = self.threshold.get()
        if self.frames:
            index = round(self.frame_index.get())
            frame, event = self.frames[index]
            result = event.match
            scene = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            active = sum(item.match is not None and item.match.score >= cutoff for _, item in self.frames)
            summary = f" · {active}/{len(self.frames)} pass · Frame {index + 1}"
            cue = ""
            if event.bite:
                cue = "Bite · "
            elif event.bubbles:
                cue = "Bubbles · "
            origin = (0, 0)
        else:
            scene, result = self.scene, self.result
            summary = ""
            cue = ""
            origin = self.region[:2] if self.region else (0, 0)

        score = "—" if result is None else f"{result.score:.3f}"
        self.score.set(f"{cue}{cutoff:.3f} min · {score} best{summary}")
        if scene is None:
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        scale = min(width / scene.width, height / scene.height)
        size = (max(1, round(scene.width * scale)), max(1, round(scene.height * scale)))
        x, y = (width - size[0]) // 2, (height - size[1]) // 2
        self.offset = (x, y, scale)
        self.rendered = ImageTk.PhotoImage(scene.resize(size, Image.Resampling.NEAREST))
        self.canvas.create_image(x, y, image=self.rendered, anchor="nw")

        def outline(box, color, offset):
            x1, y1, x2, y2 = box
            ox, oy = offset
            self.canvas.create_rectangle(x + (x1 + ox) * scale, y + (y1 + oy) * scale,
                                         x + (x2 + ox) * scale, y + (y2 + oy) * scale, outline=color, width=2)

        if self.region and not self.frames:
            outline(self.region, "#faf0aa", (0, 0))
        if result is not None:
            outline(result.box, "#62ef8f" if result.score >= cutoff else "#ff8a61", origin)

    def save_settings(self):
        self.settings.update(edition=self.edition.get(), templates={
            key: str(self.paths[edition]) for edition, key in EDITIONS.items()
        })
        self.settings.setdefault("thresholds", {})[self.key] = self.threshold.get()
        if self.monitor.current() >= 0:
            self.settings["monitor"] = self.monitors[self.monitor.current()]
        LOCAL.mkdir(exist_ok=True)
        pending = SETTINGS.with_suffix(".tmp")
        pending.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        pending.replace(SETTINGS)

    def close(self, *, for_update=False):
        if self.closing:
            return
        self.closing = True
        self.capture_stop.set()
        self.detect_stop.set()
        self.root.after_cancel(self.hotkey_timer)
        self.pool.shutdown(wait=True)
        self.update_pool.shutdown(wait=False, cancel_futures=True)
        if self.reeling and self.detection is not None and self.detection.exception() is None:
            self.tally_run(self.detection.result())
        totals = self.session_totals
        try:
            LOCAL.mkdir(exist_ok=True)
            record = dict(started=self.session_started.isoformat(timespec="seconds"),
                          ended=datetime.now().astimezone().isoformat(timespec="seconds"),
                          edition=self.key, estimated_catches=totals.reels, detected_bites=totals.bites,
                          reel_clicks=totals.reels, initial_casts=totals.casts, recasts=totals.recasts)
            with FISHING_LOG.open("a", encoding="utf-8") as output:
                output.write(json.dumps(record) + "\n")
            self.save_settings()
        except OSError as error:
            messagebox.showerror("Session could not be saved", str(error))
        finally:
            self.seed_map.close(wait=for_update)
            self.zoom_keys.close()
            self.hotkeys.close()
            self.root.withdraw()
            self.fishing_indicator.hide()
            self.gps_indicator.hide()
            if not for_update and self.settings.get("fishing_notifications", True):
                self.show_fishing_status(FishingState.INACTIVE, on_finished=self.root.destroy)
            else:
                self.root.destroy()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        from coordinate_reader import CoordinateReader

        root = tk.Tk()
        root.withdraw()
        try:
            Matcher(ASSETS / "bedrock-bobber.png")
            Matcher(ASSETS / "bedrock-vibrant-bobber.png")
            Matcher(ASSETS / "bedrock-vibrant-lit-bobber.png")
            Matcher(ASSETS / "bedrock-vibrant-level-bobber.png")
            Matcher(ASSETS / "java-bobber.png")
            hotbar = cv2.imread(str(ASSETS / "bedrock-hotbar-rod.png"))
            muted = cv2.imread(str(ASSETS / "bedrock-muted-rod.png"))
            assert all(ref is not None and ref.shape[:2] == (40, 40) for ref in (hotbar, muted))
            HotbarRod(hotbar, muted)
            for kind in ("held", "uncast"):
                HeldRod(cv2.imread(str(ASSETS / f"bedrock-{kind}-rod.png"), cv2.IMREAD_UNCHANGED))
            reader = CoordinateReader("bedrock")
            import webview
            from webview.platforms import winforms

            assert reader.ocr is not None and webview is not None and winforms is not None
            Path(sys.argv[2]).write_text(VERSION, encoding="ascii")
        finally:
            root.destroy()
        raise SystemExit
    if "--map-worker" in sys.argv:
        import msvcrt
        import traceback

        get_handle = ctypes.windll.kernel32.GetStdHandle
        get_handle.argtypes = [ctypes.c_ulong]
        get_handle.restype = ctypes.c_void_p
        for stream, number, flags, mode in (("stderr", -12, os.O_WRONLY, "w"),
                                            ("stdout", -11, os.O_WRONLY, "w"),
                                            ("stdin", -10, os.O_RDONLY, "r")):
            if getattr(sys, stream) is None:
                handle = get_handle(number)
                if handle and handle != ctypes.c_void_p(-1).value:
                    setattr(sys, stream, os.fdopen(msvcrt.open_osfhandle(handle, flags), mode,
                                                  encoding="utf-8", buffering=1))
        try:
            from seed_map_view import main

            main()
        except Exception:
            if sys.stderr is not None:
                traceback.print_exc(file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BobberBuddy.Desktop")
    root = tk.Tk()
    app = Calibration(root)
    if FROZEN and len(sys.argv) == 3 and sys.argv[1] == "--update-ready":
        def confirm_open():
            if root.winfo_viewable():
                Path(sys.argv[2]).write_text(VERSION, encoding="ascii")
                app.update_status.set(f"Updated to v{VERSION}")
            else:
                root.after(100, confirm_open)

        root.after(150, confirm_open)
    root.mainloop()
