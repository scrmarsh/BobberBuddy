import ctypes
import json
import time
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from tkinter import filedialog, messagebox, ttk

import cv2
import mss
import numpy as np
from PIL import Image, ImageTk

from bite import BiteDetector, FishingFrame
from game_window import MinecraftWindow
from hotkeys import Hotkeys, KEYS
from matcher import Matcher, bgr
from seed_map import SeedMap


LOCAL = Path(__file__).resolve().parent / ".local"
ART = LOCAL.parent / "assets" / "bobber.png"
ICON = ART.with_suffix(".ico")
SETTINGS = LOCAL / "calibration.json"
EDITIONS = {"Java Edition": "java", "Bedrock Edition": "bedrock"}
EXAMPLE = {"java": "java.png", "bedrock": "bedrock.jpg"}
EXAMPLE_REGION = {"java": (610, 180, 760, 320), "bedrock": (560, 420, 720, 550)}
INITIAL_CUTOFF = {"java": 0.82, "bedrock": 0.77}
COUNTDOWN = 4
DURATION = 12
INTERVAL = 0.08
BACKGROUND = "#101826"
PANEL = "#192536"
CARD = "#1d6177"
TEXT = "#f0f4fa"
MUTED = "#acbacb"
MINT = "#73f5c7"
WARNING = "#ffbb91"
DEFAULT_HOTKEYS = {"toggle": "F8", "emergency": "F9"}


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


def watch(area, detector, game, auto_reel, stop, latest):
    reels = 0
    with mss.MSS() as screen:
        while not stop.is_set():
            now = time.monotonic()
            if auto_reel and not game.focused():
                detector.reset()
                latest.append(FishingFrame(None, watching=False, reels=reels))
            else:
                frame = cv2.cvtColor(np.asarray(screen.grab(area)), cv2.COLOR_BGRA2BGR)
                event = detector.update(frame, now)
                if event.bite and auto_reel and game.reel(stop):
                    reels += 1
                latest.append(replace(event, reels=reels))
            stop.wait(max(0, INTERVAL - (time.monotonic() - now)))


class Calibration:
    def __init__(self, root):
        self.root = root
        root.title("Seedside")
        if ICON.is_file():
            root.iconbitmap(default=str(ICON))
        root.configure(background=BACKGROUND)
        self.configure_styles()
        self.pool = ThreadPoolExecutor(max_workers=1)
        try:
            self.settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.settings = {}
        saved_edition = self.settings.get("edition")
        self.edition = tk.StringVar(value=saved_edition if saved_edition in EDITIONS else "Java Edition")
        self.threshold = tk.DoubleVar()
        self.frame_index = tk.DoubleVar()
        self.status = tk.StringVar(value="Load an example or capture Minecraft to get started.")
        self.score = tk.StringVar()
        self.menu_status = tk.StringVar(value="Ready")
        self.menu_detail = tk.StringVar()
        self.template_name = tk.StringVar()
        self.detect_label = tk.StringVar(value="Start detection")
        self.auto_reel = tk.BooleanVar(value=False)
        self.paths = {
            edition: Path(self.settings.get("templates", {}).get(key, LOCAL / f"{key}-bobber.png"))
            for edition, key in EDITIONS.items()
        }
        with mss.MSS() as screen:
            self.monitors = list(screen.monitors[1:])
        labels = [f"{i} · {m['width']}×{m['height']}" for i, m in enumerate(self.monitors, 1)]
        self.matcher = None
        self.scene = self.monitor_area = self.region = self.start = None
        self.frames = []
        self.busy = False
        self.rendered = None
        self.capture_stop = Event()
        self.detect_stop = Event()
        self.detection = None
        self.matches = deque(maxlen=1)
        self.hotkeys = Hotkeys(root)
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
        saved_keys = self.settings.get("hotkeys", {})
        for action, default in DEFAULT_HOTKEYS.items():
            try:
                key = saved_keys.get(action, default)
                self.hotkeys.set(action, key)
                self.key_labels[action].set(key)
            except ValueError as error:
                self.menu_detail.set(str(error))
        self.hotkey_timer = root.after(50, self.poll_hotkeys)

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BACKGROUND, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("TButton", background="#293a51", bordercolor="#3e536d", padding=(12, 7))
        style.configure("Panel.TCheckbutton", background=PANEL, indicatorbackground="#223248",
                        indicatorforeground=TEXT, padding=(0, 3))
        style.map("Panel.TCheckbutton", background=[("active", PANEL)],
                  indicatorbackground=[("selected", MINT)])
        style.map("TButton", background=[("active", "#3a526d"), ("disabled", PANEL)])
        style.configure("Accent.TButton", background=MINT, foreground="#102820",
                        font=("Segoe UI Semibold", 11), bordercolor=MINT)
        style.map("Accent.TButton", background=[("active", "#a0ffde"), ("disabled", PANEL)])
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
        ttk.Label(self.menu, text="Seedside", font=("Segoe UI Semibold", 22)).grid(
            row=0, column=0, sticky="w", pady=(0, 14))
        ttk.Button(self.menu, text="Seed map", command=lambda: self.seed_map.open_settings(self.edition.get())).grid(
            row=0, column=1, sticky="e", pady=(0, 14))

        card = tk.Frame(self.menu, background=CARD, highlightbackground="#28505c", highlightthickness=1)
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        art_path = ART if ART.is_file() else ART.with_name("night-fishing-bobber.png")
        with Image.open(art_path) as image:
            self.art = ImageTk.PhotoImage(image.resize((216, 216), Image.Resampling.LANCZOS))
        tk.Label(card, text="BobberBuddy", background=CARD, foreground=TEXT,
                 font=("Segoe UI Semibold", 12)).pack(pady=(10, 0))
        tk.Label(card, image=self.art, background=CARD, borderwidth=0).pack(pady=(4, 9))
        tk.Label(card, textvariable=self.menu_status, background=CARD, foreground=MINT,
                 font=("Segoe UI Semibold", 17)).pack()
        tk.Label(card, textvariable=self.menu_detail, background=CARD, foreground=MUTED, wraplength=230,
                 font=("Segoe UI", 10)).pack(padx=10, pady=(5, 13))

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
        for row, action, label in ((5, "toggle", "Start / stop detection"), (6, "emergency", "Emergency stop")):
            tk.Label(panel, text=label, background=PANEL, foreground=TEXT, font=("Segoe UI", 10)).grid(
                row=row, column=0, sticky="w", padx=(14, 4), pady=(3, 8))
            tk.Label(panel, textvariable=self.key_labels[action], background="#243448",
                     foreground=MINT if action == "toggle" else WARNING, font=("Consolas", 11, "bold"),
                     width=4, padx=4, pady=4).grid(row=row, column=1, padx=(0, 7), pady=(3, 8))
            ttk.Button(panel, text="Rebind", command=lambda name=action: self.rebind(name)).grid(
                row=row, column=2, sticky="ew", padx=(0, 14), pady=(3, 8))
        ttk.Checkbutton(panel, text="Auto-reel on bite", style="Panel.TCheckbutton", variable=self.auto_reel,
                        command=self.detect_stop.set).grid(row=7, column=0, columnspan=3, sticky="w",
                                                          padx=14, pady=(2, 10))
        ttk.Button(self.menu, text="Calibrate", command=self.show_calibration).grid(
            row=2, column=0, sticky="ew", padx=(0, 14), pady=(14, 0))
        ttk.Button(self.menu, textvariable=self.detect_label, style="Accent.TButton", command=self.toggle_detection).grid(
            row=2, column=1, sticky="ew", pady=(14, 0))

    def show_menu(self):
        self.calibration.pack_forget()
        self.menu.pack(fill="both", expand=True)
        self.root.minsize(660, 485)
        self.root.geometry("690x500")
        self.update_menu()

    def show_calibration(self):
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
        elif path == LOCAL / f"{self.key}-bobber.png":
            name = "Online reference"
        else:
            name = path.name
        self.template_name.set(name)
        if self.detection is not None:
            return
        self.menu_status.set("Ready" if self.matcher and self.detection_area() else "Setup needed")
        self.menu_detail.set(
            "Cast in Minecraft, then use your start hotkey."
            if self.matcher and self.detection_area() else
            "Choose a template and save a live search region in Calibrate."
        )

    def rebind(self, action):
        self.rebinding = True
        dialog = tk.Toplevel(self.root, background=BACKGROUND)
        dialog.title("Rebind hotkey")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack()
        ttk.Label(body, text="Start / stop detection" if action == "toggle" else "Emergency stop").pack(anchor="w")
        selected = tk.StringVar(value=self.key_labels[action].get())
        picker = ttk.Combobox(body, state="readonly", values=KEYS, textvariable=selected)
        picker.pack(fill="x", pady=8)
        error_text = tk.StringVar()
        ttk.Label(body, textvariable=error_text, foreground=WARNING, wraplength=260).pack(anchor="w")

        def dismiss():
            dialog.destroy()
            self.rebinding = False

        def apply():
            key = selected.get()
            try:
                self.hotkeys.set(action, key)
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

    def detection_area(self):
        monitor = self.selected_monitor()
        if monitor is None:
            return None
        box = self.saved_region(monitor)
        if box is None:
            return None
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= monitor["width"] and 0 <= y1 < y2 <= monitor["height"]):
            return None
        if x2 - x1 > 640 or y2 - y1 > 480:
            return None
        return dict(left=monitor["left"] + x1, top=monitor["top"] + y1,
                    width=x2 - x1, height=y2 - y1)

    def selected_monitor(self):
        index = self.monitor.current()
        return self.monitors[index] if index >= 0 else None

    def toggle_detection(self):
        if self.detection is not None:
            self.detect_stop.set()
            return
        if self.busy or self.rebinding or not self.menu.winfo_ismapped():
            return
        area = self.detection_area()
        if area is None or self.matcher is None:
            self.menu_status.set("Setup needed")
            self.menu_detail.set("Save a live region up to 640 × 480 pixels in Calibrate.")
            return
        self.detect_stop.clear()
        self.matches.clear()
        self.detect_label.set("Stop detection")
        self.menu_status.set("Searching")
        self.menu_detail.set("Return to Minecraft to check the bobber.")
        self.detection = self.pool.submit(watch, area, BiteDetector(self.matcher, self.threshold.get()),
                                          MinecraftWindow(self.key, area), self.auto_reel.get(),
                                          self.detect_stop, self.matches)

    def poll_hotkeys(self):
        pressed = set(self.hotkeys.poll())
        if "emergency" in pressed:
            self.detect_stop.set()
            self.capture_stop.set()
        elif "toggle" in pressed:
            self.toggle_detection()
        if self.detection is not None:
            if self.detection.done():
                error = self.detection.exception()
                self.detection = None
                self.detect_label.set("Start detection")
                self.update_menu()
                if error is not None:
                    self.menu_status.set("Capture failed")
                    self.menu_detail.set(str(error))
            elif self.matches:
                event = self.matches.pop()
                match = event.match
                score = match.score if match else 0
                if not event.watching:
                    self.menu_status.set("Waiting for game")
                    self.menu_detail.set(f"Reel clicks sent · {event.reels}" if event.reels else
                                         "Focus the game and return to the fishing view.")
                else:
                    if event.bite:
                        self.menu_status.set("Bite detected")
                    elif event.bubbles:
                        self.menu_status.set("Bubbles approaching")
                    else:
                        self.menu_status.set("Bobber found" if score >= self.threshold.get() else "Searching")
                    detail = f"Match {score:.3f} · minimum {self.threshold.get():.3f}"
                    if event.reels:
                        detail += f"\nReel clicks sent · {event.reels}"
                    self.menu_detail.set(detail)
        self.hotkey_timer = self.root.after(50, self.poll_hotkeys)

    @property
    def key(self):
        return EDITIONS[self.edition.get()]

    def switch_edition(self, _=None):
        self.detect_stop.set()
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
            matcher = Matcher(path)
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
        self.load_template(LOCAL / f"{self.key}-bobber.png")

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

    def set_scene(self, scene, monitor_area=None, region=None):
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
        if self.scene is not None and self.matcher is not None and (self.region or self.monitor_area is None):
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
        x1, y1, x2, y2 = self.region
        if x2 - x1 > 640 or y2 - y1 > 480:
            self.status.set("Select a smaller region, up to 640 × 480 pixels.")
            return
        area = dict(left=self.monitor_area["left"] + x1, top=self.monitor_area["top"] + y1,
                    width=x2 - x1, height=y2 - y1)
        self.run_capture(area, self.matcher, self.set_replay)

    def run_capture(self, area, matcher, on_done):
        if self.busy:
            return
        self.busy = True
        self.capture_stop.clear()
        self.root.withdraw()
        future = self.pool.submit(capture, area, matcher, self.capture_stop, self.threshold.get())

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

    def close(self):
        self.capture_stop.set()
        self.detect_stop.set()
        self.seed_map.close()
        self.save_settings()
        self.root.after_cancel(self.hotkey_timer)
        self.hotkeys.close()
        self.pool.shutdown(wait=False)
        self.root.destroy()


if __name__ == "__main__":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Seedside.Desktop")
    root = tk.Tk()
    Calibration(root)
    root.mainloop()
