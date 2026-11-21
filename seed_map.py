import importlib.util
import json
import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from urllib.parse import urlencode

import mss


MAP_URL = "https://www.chunkbase.com/apps/seed-map"
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
MAP_MODES = {"Second display · full screen": "display", "In-game overlay · bottom right": "overlay",
             "Both views": "both"}


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


def map_url(config):
    center = dict(x=config["x"], z=config["z"]) if config["x"] is not None else {}
    state = dict(seed=config["seed"], platform=VERSIONS[config["edition"]][config["version"]],
                 dimension=DIMENSIONS[config["dimension"]], zoom=0.55, **center)
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


class SeedMap:
    def __init__(self, root, settings, save, monitor):
        self.root = root
        self.settings = settings
        self.save = save
        self.monitor = monitor
        self.process = None

    def open_settings(self, edition):
        with mss.MSS() as screen:
            displays = list(screen.monitors[1:])
        dialog = tk.Toplevel(self.root)
        dialog.title("Seedside Seed Map")
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
        saved_edition = next((label for label, key in editions.items() if key == stored.get("edition")), edition)
        selection = tk.StringVar(value=saved_edition)
        seed = tk.StringVar(value=stored.get("seed", ""))
        version = tk.StringVar()
        dimension = tk.StringVar(value=stored.get("dimension", "Overworld"))
        x = tk.StringVar(value="" if stored.get("x") is None else stored["x"])
        z = tk.StringVar(value="" if stored.get("z") is None else stored["z"])
        follow = tk.BooleanVar(value=stored.get("follow", True))
        mode_choices = list(MAP_MODES) if len(displays) > 1 else [list(MAP_MODES)[1]]
        mode = tk.StringVar(value=next((label for label in mode_choices
                                        if MAP_MODES[label] == stored.get("mode")), mode_choices[0]))
        display_name = tk.StringVar()
        error = tk.StringVar()
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
            display_picker.configure(state="disabled" if MAP_MODES[mode.get()] == "overlay" else "readonly")

        mode_picker.bind("<<ComboboxSelected>>", refresh_display)
        refresh_display()
        place(6, "Map center", coordinates)
        ttk.Checkbutton(panel, text="Follow coordinates shown in Minecraft", variable=follow).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 4))
        ttk.Label(panel, text="Show Coordinates in Bedrock · F3 in Java", wraplength=335).grid(
            row=8, column=0, columnspan=2, sticky="w")
        ttk.Label(panel, text="The online Chunk Base map uses your seed and X/Z.", wraplength=335).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(5, 0))
        ttk.Label(panel, textvariable=error, foreground="#ffbb91", wraplength=335).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=(5, 0))

        def launch():
            try:
                if x.get().strip() or z.get().strip() or not follow.get():
                    center = (coordinate(x.get()), coordinate(z.get()))
                else:
                    center = (None, None)
                display = display_labels.get(display_name.get())
                if display is None:
                    raise ValueError("Choose a display for the mini-map.")
                selected_mode = MAP_MODES[mode.get()]
                if selected_mode != "overlay" and game_display and display_key(display) == display_key(game_display):
                    raise ValueError("Choose a different display for the full-screen map.")
                config = dict(seed=world_seed(seed.get()), edition=editions[selection.get()],
                              version=version.get(), dimension=dimension.get(), x=center[0], z=center[1],
                              follow=follow.get(), display=display_key(display), mode=selected_mode)
                self.launch(config, display)
            except (OSError, ValueError) as exc:
                error.set(str(exc))
                return
            self.settings["seed_map"] = config
            self.save()
            dialog.destroy()

        actions = ttk.Frame(panel)
        actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Open map", style="Accent.TButton", command=launch).pack(
            side="right", fill="x", expand=True)
        if self.process is not None and self.process.poll() is None:
            def close_maps():
                self.close()
                dialog.destroy()

            ttk.Button(actions, text="Close map views", command=close_maps).pack(side="left", padx=(0, 8))
        dialog.bind("<Escape>", lambda _: dialog.destroy())
        dialog.grab_set()
        seed_entry.focus_set()

    def launch(self, config, display):
        if importlib.util.find_spec("webview") is None:
            raise ValueError("Install the optional Seed Map dependencies listed in the README.")
        self.close()
        script = Path(__file__).with_name("seed_map_view.py")
        log = Path(__file__).parent / ".local" / "seed-map.log"
        log.parent.mkdir(exist_ok=True)
        with log.open("wb") as diagnostics:
            self.process = subprocess.Popen(
                [sys.executable, str(script)], stdin=subprocess.PIPE, stdout=diagnostics, stderr=diagnostics,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            )
        self.process.stdin.write(json.dumps(dict(config, target_display=display)) + "\n")
        self.process.stdin.flush()

    def close(self):
        if self.process is not None:
            self.process.stdin.close()
            self.process = None
