import importlib.util
import json
import re
import subprocess
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import urlencode

import mss
from app_info import APP_NAME, DATA_DIR, FROZEN
from navigation import world_key


MAP_URL = "https://www.chunkbase.com/apps/seed-map"
MAP_ZOOM_COMMANDS = {"zoom_in": "Zoom in", "zoom_out": "Zoom out"}
VERSIONS = {
    "bedrock": {
        "26.60–26.65": "bedrock_26_60",
        "26.50–26.52": "bedrock_26_50",
        "26.30–26.45": "bedrock_26_30",
        "26.0–26.23": "bedrock_26_0",
        "1.21.120–1.21.132": "bedrock_1_21_120",
        "1.21.111–1.21.114": "bedrock_1_21_110",
        "1.21.90–1.21.101": "bedrock_1_21_90",
        "1.21.60–1.21.84": "bedrock_1_21_60",
        "1.21.50": "bedrock_1_21_50",
        "1.21–1.21.40": "bedrock_1_21",
        "1.20.60–1.20.81": "bedrock_1_20_60",
        "1.20–1.20.51": "bedrock_1_20",
        "1.19": "bedrock_1_19",
        "1.18": "bedrock_1_18",
    },
    "java": {
        "26.3": "java_26_3",
        "26.2": "java_26_2",
        "26.1": "java_26_1",
        "1.21.9–1.21.11": "java_1_21_9",
        "1.21.6–1.21.8": "java_1_21_6",
        "1.21.5": "java_1_21_5",
        "1.21.4": "java_1_21_4",
        "1.21.2–1.21.3": "java_1_21_2",
        "1.21–1.21.1": "java_1_21",
        "1.20": "java_1_20",
        "1.19.3–1.19.4": "java_1_19_3",
        "1.19–1.19.2": "java_1_19",
        "1.18": "java_1_18",
    },
}
DIMENSIONS = {"Overworld": "overworld", "Nether": "nether", "End": "end"}
MAP_MODES = {"In-Game Overlay": "overlay", "Separate Window": "window", "Both": "both",
             "Second display · full screen": "display"}
FULLSCREEN_MODES = {"display"}


def world_seed(value):
    value = value.strip()
    if not value:
        raise ValueError("Enter your world's seed.")
    if re.fullmatch(r"[+-]?\d+", value, flags=re.ASCII):
        decimal = int(value)
        if not -(2**63) <= decimal < 2**63:
            raise ValueError("Enter a signed 64-bit seed.")
        return str(decimal)
    if len(value) > 128:
        raise ValueError("Enter a seed of up to 128 characters.")
    return value


def coordinate(value):
    value = str(value).strip()
    if not re.fullmatch(r"[+-]?\d{1,8}", value, flags=re.ASCII) or abs(int(value)) > 30_000_000:
        raise ValueError("X and Z must be whole block coordinates.")
    return int(value)


def map_url(config, *, overlay=False):
    center = dict(x=config["x"], z=config["z"]) if config["x"] is not None else {}
    state = dict(seed=config["seed"], platform=VERSIONS[config["edition"]][config["version"]],
                 dimension=DIMENSIONS[config["dimension"]], zoom=0.42 if overlay else 0.55, **center)
    return f"{MAP_URL}#{urlencode(state)}"


def display_key(display):
    return display.get("unique_id") or f"{display.get('name', 'Display')}@{display['left']},{display['top']}"


def default_display(displays, game_display, saved_key):
    if not displays:
        raise ValueError("Windows did not report an available display.")
    saved = next((display for display in displays if display_key(display) == saved_key), None)
    if saved is not None:
        return saved
    if game_display is None:
        game_display = next((display for display in displays if display.get("is_primary")), displays[0])
    game_key = display_key(game_display)
    return next((display for display in displays if display_key(display) != game_key), displays[0])


def map_displays():
    try:
        with mss.MSS() as screen:
            return list(screen.monitors[1:])
    except (OSError, mss.ScreenShotError):
        return []


class SeedMap:
    def __init__(self, root, settings, save, monitor):
        self.root = root
        self.settings = settings
        self.save = save
        self.monitor = monitor
        self.process = None
        self.mode = None
        self.log = DATA_DIR / "seed-map.log"

    def open_startup_overlay(self):
        stored = self.settings.get("seed_map", {})
        if not stored.get("seed") or not stored.get("enabled", stored.get("startup_overlay", True)):
            return
        displays = map_displays()
        display = default_display(displays, self.monitor(), stored.get("display")) if displays else None
        if self.process is None:
            mode = stored.get("mode", "overlay")
            if mode == "display" and len(displays) < 2:
                mode = "window"
            self.launch(dict(stored, mode=mode), display, report_error=False)

    def open_window(self, edition):
        stored = self.settings.get("seed_map", {})
        if not stored.get("seed"):
            self.open_settings(edition)
            return
        if self.is_open() and self.mode != "overlay":
            self.send("show_window")
            return
        mode = "both" if self.is_open() else "window"
        config = dict(stored, enabled=True, mode=mode)
        try:
            self.launch(config, None)
            self.settings["seed_map"] = config
            self.save()
        except (OSError, ValueError, KeyError):
            self.open_settings(edition, initial_error="The map window could not start. Check the map settings or open it in your browser.")

    def open_waypoints(self):
        config = self.settings.get("seed_map", {})
        if not config.get("seed"):
            self.open_settings("Bedrock Edition")
            return
        key = json.dumps(world_key(config), ensure_ascii=False)
        routes = self.settings.setdefault("navigation", {})
        route = routes.setdefault(key, {"waypoints": {}, "radius": 8})
        points = route["waypoints"]
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} Waypoints")
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        name, x, z = (tk.StringVar() for _ in range(3))
        radius = tk.StringVar(value=route.get("radius", 8))
        status = tk.StringVar()
        for caption, value in (("Name", name), ("X", x), ("Z", z), ("Arrival radius", radius)):
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=caption, width=13).pack(side="left")
            ttk.Entry(row, textvariable=value).pack(side="left", fill="x", expand=True)
        chooser = ttk.Combobox(panel, values=sorted(points), state="readonly")
        chooser.pack(fill="x", pady=8)

        def select(_=None):
            if chooser.get() in points:
                name.set(chooser.get())
                px, pz = points[chooser.get()]
                x.set(px)
                z.set(pz)

        chooser.bind("<<ComboboxSelected>>", select)

        def change(action):
            try:
                if action == "current":
                    fix = json.loads((DATA_DIR / "player-position.json").read_text(encoding="utf-8"))
                    if tuple(fix["world"]) != world_key(config) or time.time() - fix["observed"] > 10:
                        raise ValueError("Wait for the map to read coordinates in this world.")
                    x.set(fix["position"][0])
                    z.set(fix["position"][1])
                    return
                if action == "clear":
                    route.pop("destination", None)
                elif action == "delete":
                    points.pop(chooser.get() or name.get(), None)
                    chooser.set("")
                else:
                    position = [coordinate(x.get()), coordinate(z.get())]
                    distance = coordinate(radius.get())
                    if not 1 <= distance <= 1000:
                        raise ValueError("Set an arrival radius from 1 to 1,000 blocks.")
                    route["radius"] = distance
                    if action == "save":
                        label = name.get().strip()
                        if not label or len(label) > 64:
                            raise ValueError("Enter a waypoint name of up to 64 characters.")
                        if chooser.get() and chooser.get() != label:
                            points.pop(chooser.get(), None)
                        points[label] = position
                        chooser.set(label)
                    else:
                        route["destination"] = dict(position=position, name=name.get().strip())
                chooser.configure(values=sorted(points))
                self.save()
                self.send("navigation " + json.dumps(route))
                status.set("Saved.")
            except (OSError, ValueError, KeyError) as error:
                status.set(str(error))

        for title, action in (("Use current coordinates", "current"), ("Save waypoint", "save"),
                              ("Delete selected waypoint", "delete"), ("Start navigation", "start"),
                              ("Cancel navigation", "clear")):
            ttk.Button(panel, text=title, command=lambda value=action: change(value)).pack(fill="x", pady=2)
        ttk.Label(panel, textvariable=status, wraplength=300).pack(fill="x")

    def open_settings(self, edition, initial_error=""):
        displays = map_displays()
        dialog = tk.Toplevel(self.root)
        dialog.title(f"{APP_NAME} Minimap")
        dialog.transient(self.root)
        panel = ttk.Frame(dialog, padding=20)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        stored = self.settings.get("seed_map", {})
        game_display = self.monitor()
        display_labels = {
            f"{index} · {display.get('name') or 'Display'} · {display['width']}×{display['height']}"
            + (" (game display)" if game_display and display_key(display) == display_key(game_display) else ""): display
            for index, display in enumerate(displays, 1)
        }
        editions = {"Bedrock Edition": "bedrock", "Java Edition": "java"}
        saved_edition = next((label for label, key in editions.items() if key == stored.get("edition")),
                             edition if edition in editions else "Bedrock Edition")
        selection = tk.StringVar(value=saved_edition)
        seed = tk.StringVar(value=stored.get("seed", ""))
        version = tk.StringVar()
        dimension = tk.StringVar(value=stored.get("dimension", "Overworld"))
        x = tk.StringVar(value="" if stored.get("x") is None else stored["x"])
        z = tk.StringVar(value="" if stored.get("z") is None else stored["z"])
        follow = tk.BooleanVar(value=stored.get("follow", True))
        enabled = tk.BooleanVar(value=stored.get("enabled", stored.get("startup_overlay", True)))
        mode_choices = [label for label, key in MAP_MODES.items()
                        if len(displays) > 1 or key not in FULLSCREEN_MODES]
        mode = tk.StringVar(value=next((label for label in mode_choices
                                        if MAP_MODES[label] == stored.get("mode")), mode_choices[0]))
        display_name = tk.StringVar()
        error = tk.StringVar(value=initial_error)
        if displays:
            initial = default_display(displays, game_display, stored.get("display"))
            display_name.set(next(label for label, display in display_labels.items() if display is initial))

        def place(row, label, widget):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            widget.grid(row=row, column=1, sticky="ew", pady=4)

        seed_entry = ttk.Entry(panel, textvariable=seed, width=29)
        place(0, "World seed", seed_entry)
        edition_picker = ttk.Combobox(panel, textvariable=selection, values=list(editions), state="readonly")
        place(1, "Edition", edition_picker)
        versions = ttk.Combobox(panel, textvariable=version, state="readonly")
        place(2, "Game version", versions)

        def refresh_versions(_=None):
            choices = list(VERSIONS[editions[selection.get()]])
            versions.configure(values=choices)
            remembered = stored.get("version") if editions[selection.get()] == stored.get("edition") else None
            version.set(remembered if remembered in choices else choices[0])

        edition_picker.bind("<<ComboboxSelected>>", refresh_versions)
        refresh_versions()
        place(3, "Dimension", ttk.Combobox(panel, textvariable=dimension, values=list(DIMENSIONS), state="readonly"))
        coordinates = ttk.Frame(panel)
        ttk.Label(coordinates, text="X").pack(side="left")
        ttk.Entry(coordinates, textvariable=x, width=10).pack(side="left", padx=(4, 10))
        ttk.Label(coordinates, text="Z").pack(side="left")
        ttk.Entry(coordinates, textvariable=z, width=10).pack(side="left", padx=(4, 0))
        mode_picker = ttk.Combobox(panel, values=mode_choices, textvariable=mode, state="readonly")
        place(4, "Map view", mode_picker)
        display_picker = ttk.Combobox(panel, values=list(display_labels), state="readonly", textvariable=display_name)
        place(5, "Full-screen display", display_picker)

        def refresh_display(_=None):
            display_picker.configure(state="readonly" if MAP_MODES[mode.get()] in FULLSCREEN_MODES else "disabled")

        mode_picker.bind("<<ComboboxSelected>>", refresh_display)
        refresh_display()
        place(6, "Map center", coordinates)
        ttk.Checkbutton(panel, text="Follow coordinates shown in Minecraft", variable=follow).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 4))
        ttk.Checkbutton(panel, text="Minimap enabled", variable=enabled).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(panel, text="Show Coordinates in Bedrock · F3 in Java", wraplength=335).grid(
            row=9, column=0, columnspan=2, sticky="w")
        ttk.Label(panel, text="The online Chunk Base map uses your seed and X/Z.", wraplength=335).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(panel, textvariable=error, foreground="#ffbb91", wraplength=335).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(5, 0))

        def map_config(display):
            center = ((coordinate(x.get()), coordinate(z.get()))
                      if x.get().strip() or z.get().strip() or not follow.get() else (None, None))
            return dict(seed=world_seed(seed.get()), edition=editions[selection.get()], version=version.get(),
                        dimension=dimension.get(), x=center[0], z=center[1], follow=follow.get(),
                        display=display_key(display) if display else None,
                        mode=MAP_MODES[mode.get()], enabled=enabled.get())

        def launch():
            try:
                display = display_labels.get(display_name.get())
                config = map_config(display)
                if config["mode"] in FULLSCREEN_MODES:
                    if display is None:
                        raise ValueError("Choose a display for the full-screen map.")
                    if game_display and display_key(display) == display_key(game_display):
                        raise ValueError("Choose a different display for the full-screen map.")
                if enabled.get():
                    self.launch(config, display)
                else:
                    self.close()
                self.settings["seed_map"] = config
                self.save()
            except (OSError, ValueError, KeyError) as exc:
                error.set(str(exc))
                return
            dialog.destroy()

        def open_browser():
            try:
                if not webbrowser.open(map_url(map_config(None))):
                    error.set("Windows could not open the default browser.")
            except (ValueError, KeyError, OSError) as exc:
                error.set(str(exc))

        actions = ttk.Frame(panel)
        actions.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Apply map settings", style="Accent.TButton", command=launch).pack(
            side="right", fill="x", expand=True)
        ttk.Button(actions, text="Open in browser", command=open_browser).pack(side="left", padx=(0, 8))
        if self.process is not None and self.process.poll() is None:
            def close_maps():
                self.close()
                dialog.destroy()

            ttk.Button(actions, text="Close map views", command=close_maps).pack(side="left", padx=(0, 8))
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.grab_set()
        seed_entry.focus_set()

    def launch(self, config, display, *, report_error=True):
        if importlib.util.find_spec("webview") is None:
            raise ValueError("The embedded map dependencies are unavailable. Try Open in browser.")
        url = map_url(config)
        self.close()
        script = Path(__file__).with_name("seed_map_view.py")
        command = [sys.executable, "--map-worker" if FROZEN else str(script)]
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("wb") as diagnostics:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=diagnostics, stderr=diagnostics,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            )
        navigation = self.settings.get("navigation", {}).get(json.dumps(world_key(config), ensure_ascii=False), {})
        try:
            self.process.stdin.write(json.dumps(dict(config, target_display=display, navigation=navigation)) + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            self.close()
            raise OSError("The embedded map could not start. Try Open in browser.") from exc
        self.mode = config["mode"]
        if report_error:
            self.root.after(700, self.check_worker, self.process, url, 12)

    def check_worker(self, process, url, remaining):
        if process is not self.process:
            return
        result = process.poll()
        if result is None and remaining:
            self.root.after(700, self.check_worker, process, url, remaining - 1)
        elif result:
            self.close()
            if messagebox.askyesno("Minimap unavailable",
                                   "The embedded map could not start. Check that Microsoft Edge WebView2 Runtime "
                                   "is installed. Open the seed map in your browser instead?",
                                   parent=self.root):
                webbrowser.open(url)

    def close(self):
        if self.process is not None:
            process = self.process
            self.process = None
            try:
                process.stdin.close()
            except OSError:
                pass

    def zoom(self, action):
        self.send(action)

    def send(self, action):
        if not self.is_open():
            return
        try:
            self.process.stdin.write(action + "\n")
            self.process.stdin.flush()
        except OSError:
            pass

    def is_open(self):
        return self.process is not None and self.process.poll() is None
