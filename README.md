# Bobber Buddy

Bobber Buddy is an unofficial Windows fishing companion for Minecraft Java and Bedrock. It includes a live seed minimap.

## Install on Windows

Download the Windows x64 ZIP from [GitHub Releases](https://github.com/scrmarsh/BobberBuddy/releases/latest), extract it, and open **Bobber Buddy.exe**. Keep the executable and its supporting files together. No Python installation is needed. The package targets Windows 10 and 11. The map requires Microsoft Edge WebView2 Runtime, Windows OCR language support, and an internet connection. Fishing works offline after calibration.

Packaged settings, custom reference images, waypoints, and session logs live in `%LOCALAPPDATA%\Bobber Buddy`. Source checkouts use `.local`.

## Run from source

Double-click `bobberbuddy.cmd`. For a fresh checkout, run the following in PowerShell first.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe fetch_references.py
```

## Automatic fishing

Select your edition and use **Change** to choose a bobber template. After saving a region in **Calibrate**, use **F10** to toggle automatic fishing. **F9** stops detection or cancels a calibration capture. Use **Rebind** beside either action to save a new key from F1 through F11.

The menu art is decorative. Detection uses your selected template in the saved screen region.

Disable automatic fishing, then click **Test bobber** to check the saved region, match score, bubbles, and bites while you fish manually. Test mode sends no clicks. Click **Stop test**, use the detection hotkey, or press the emergency-stop hotkey to end the test.

### Reel in on a bite

1. In **Calibrate**, record a sample with the bobber floating and leave it out until you see a bite. The replay marks **Bubbles** as a wake approaches and **Bite** when the detector confirms a dip. Leave some room around the float in the search box so the approaching particles stay in view.
2. In **Calibrate**, choose **Learn rod (4s)** and return to Minecraft with the rod selected, the hotbar visible, and the rod showing in first person. In the captured image, drag a tight box around the reel and handle. Bobber Buddy checks both the selected hotbar icon and the held rod before casting, reeling, or recasting.
3. Bobber Buddy scans automatically on launch once calibration is ready. It tracks an existing cast or casts once after a short clear-water check. Fishing pauses when Minecraft is paused, minimized, or out of focus, or when you switch away from the rod. **F10** toggles automatic fishing and **F9** stops it. The status popup fades in below the crosshair on launch, toggle, and close.

An approaching wake helps confirm a bite when the bobber disappears under the water. A visible downward dip can trigger without a wake. Bubbles alone never send input. On Bedrock, detection scans a small margin above and beside the selected region. **Bobber locked** means it has tracked the float across several frames.

Each app session shows a running estimated catch total and appends one record to `.local/fishing-sessions.jsonl` when you close it. The estimate comes from successful reel inputs on detected bites. The record also includes the session's time, detected bites, reel clicks, initial casts, and recasts.

## Seed mini-map

Install the optional Windows map dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements-map.txt`. The mini-map uses Microsoft Edge WebView2 Runtime.

1. Select **Minimap** in Bobber Buddy. Enter the world's seed and choose its edition, version, and current dimension.
2. In Bedrock, turn on **Show Coordinates**. In Java, open the F3 screen and leave the XYZ line visible. Leave **Follow coordinates shown in Minecraft** checked to center the mini-map on your current position. You can also turn it off and enter X/Z to use a fixed center.
3. Choose **In-Game Overlay**, **Separate Window**, or **Both**. **Second display · full screen** is also available with a second monitor. Use windowed or borderless Minecraft for the overlay. The separate window is movable and resizable and remembers its placement.
4. Select **Minimap enabled** and click **Apply map settings**. The selected views follow the same X/Z coordinates. Clear **Minimap enabled** and apply to turn both views off, or use **Open Map Window** on the main screen to open a desktop map.

Open **Settings → Map & Navigation → Waypoints and destination** to save or edit named coordinates, start navigation, or cancel it. Alt-click a point on the north-up desktop map to navigate to it. The GPS gives a compass bearing and straight-line distance. Set the matching edition, seed, and dimension before saving waypoints or navigating.

Use **=** to zoom in and **-** to zoom out while playing. Zoom shortcuts pause when you open Minecraft chat or the command window with **T** or **/** and resume when you return to gameplay. Both shortcuts can be changed under **Hotkeys** on the main screen.

Coordinate recognition runs on the computer using Windows OCR. The online [Chunk Base seed map](https://www.chunkbase.com/apps/seed-map) uses the entered seed and horizontal coordinates to predict world generation. The map needs internet access and predicts terrain and structures from the chosen game version. It does not read your save file or show builds, terrain edits, or other changes you made in-game.

## Calibrate a fishing spot

1. Select your edition and click **Calibrate**. **Online example** opens a gameplay screenshot with a search region around the float. Green marks a candidate that passes the minimum score. Orange marks the best candidate below the minimum.
2. Put Minecraft in windowed or borderless mode and cast manually. In Bobber Buddy, select the game's monitor and click **Capture screen (4s)**. Return to Minecraft before the capture.
3. Drag a rectangle around the water where the bobber landed. Use a small region, ideally around 150 × 150 pixels if you plan to keep the camera still. If the online template scores poorly, select the float tightly, click **Use region as template**, then draw the larger search region again.
4. Click **Sample region (12s)** and return to Minecraft. Keep the camera still. Leave the bobber floating for several seconds, then reel in manually and leave the same water empty.
5. Scrub **Replay frame** and move **Minimum match score**. Look for a cutoff that passes frames with the real bobber and rejects frames after reeling in. Repeat with a different cast, lighting, or resource pack to check that your cutoff holds.

Bobber Buddy remembers the last edition and monitor, plus each edition's template, minimum score and live search region. Capture the game again on the same monitor to reuse the region. **Open template** accepts a tightly cropped image and supports an alpha mask. **Use online template** restores the screenshot-derived reference for the selected edition.

## References and offline checks

The fetch script keeps screenshots and the cropped reference PNGs in the local `.local` folder. The crops use source pixels with a transparent mask around the visible float. Gameplay and screenshots belong to their respective rights holders.

- Java gameplay frames were captured for Player Assist’s guides. The template uses the clean bobber from the cat guide. The fishing guide provides a separate frame with splash particles. [Java template source](https://playerassist.com/tame-cat-minecraft/) · [Java test source](https://playerassist.com/use-fishing-rod-minecraft/)
- Bedrock uses a mobile gameplay frame for the template and a separate controller gameplay frame as the test. [Bedrock template source](https://ameblo.jp/zu-xxxxx13/entry-12624117283.html) · [Bedrock test source](https://note.com/rbaconv/n/nac7fc70bb494)

| Edition | Bobber region | Empty water region | Starting minimum |
| --- | ---: | ---: | ---: |
| Java | 0.851 | 0.783 | 0.820 |
| Bedrock | 0.800 | 0.732 | 0.770 |

These values come from one separate screenshot per edition. The full-frame Bedrock search scored 0.834 on a false match outside the water region, so select a search region before using the starting cutoff. The values are similarity scores. The bite logic was checked with controlled sequences composited on a recorded Bedrock frame. Timing can vary with the camera angle, particles, and resource pack.

## Live Bedrock test notes

- The saved ocean calibration uses an in-game bobber template with a 0.820 minimum match score. In the live replay, the detector recognized the wake and fired on the first submerged frame.
- A live Bedrock run completed five reel-and-recast cycles. The footage showed fish and a lily pad. Catch totals in the automatic session log are estimates because the detector measures bites and reel inputs.
