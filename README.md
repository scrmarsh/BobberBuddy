# Bobber Buddy

Bobber Buddy is a Windows auto-fisher for Minecraft Bedrock and Java Edition. It watches the bobber, reels in when it detects a bite, and casts again. Fishing works offline.

**[Download Bobber Buddy for Windows](https://github.com/scrmarsh/BobberBuddy/releases/latest)**

Latest release **v1.0.2** adds first-run Bedrock auto-fishing with bundled references for both standard rendering and Vibrant Visuals.

## What it does

- Detects a bobber dip and uses approaching bubbles as an additional bite cue.
- Checks that the fishing rod is selected in the hotbar and visible in first person before casting, reeling, or recasting.
- Pauses when you switch items or when Minecraft is paused, minimized, or out of focus.
- Shows fishing status beneath the crosshair and logs estimated catches when you close the app. The estimate is based on reel inputs after detected bites.
- Includes a **Test bobber** mode for tuning detection while you fish manually, without sending clicks.

## Install and start fishing

Download the Windows x64 ZIP, extract all its files, and open **Bobber Buddy.exe**. The package targets Windows 10 and 11 and includes Python.

In Bedrock Edition with the default textures, open the app, equip your rod, and aim at open water. Bobber Buddy starts scanning automatically on a clean profile and ignores the rod's durability meter. If it cannot confirm the rod is ready to cast, cast the first line yourself. Use **F10** to toggle auto-fishing or **F9** to stop it immediately.

### Calibrate for Java or custom textures

1. Select your edition. In Minecraft, cast into your fishing spot and keep the camera still. Windowed or borderless mode works best.
2. In **Calibrate**, choose your game monitor and click **Capture screen (4s)**. Return to Minecraft before the capture, then draw a small search region around the bobber and nearby water. If the bundled template struggles, select just the bobber, click **Use region as template**, then redraw the wider search region.
3. In **Calibrate**, click **Learn rod (4s)**. Return to Minecraft with the rod selected and visible in first person. In the captured image, draw a tight box around the reel and handle. Use the same control to capture an enchanted rod if the default reference does not match it.
4. Use **Test bobber** to check tracking, or press **F10** to toggle auto-fishing. **F9** is the emergency stop. Both keys can be changed in the app.

If a spot gives false bites or fails to track the float, use **Sample region (12s)** in **Calibrate** and review the replay. Adjust **Minimum match score** so the real bobber is detected more reliably than empty water. Recalibrate when the saved screen region no longer lines up with your fishing spot.

The Windows package saves settings and session logs under `%LOCALAPPDATA%\Bobber Buddy`.

## Optional seed minimap

Open **Minimap** and enter your seed, edition, game version, and dimension. To follow your position, enable **Show Coordinates** in Bedrock or leave the **F3** XYZ readout visible in Java.

Use **Save seed…** to name a world and keep it in **Saved seeds**. Pick a saved world to restore its seed, edition, version, and dimension, then select **Apply map settings** to switch the map.

The map can appear as an in-game overlay, in a separate window, or both. It supports named waypoints and straight-line compass guidance. It uses Chunk Base, WebView2, Windows OCR, and an internet connection. Use **Open in browser** from the minimap settings if the embedded map is unavailable.

With the in-game overlay enabled, press **Esc** from gameplay to expand the map on the right side of the pause menu. Use **Features** to toggle structures, scroll to the display controls, or drag the map to explore. The map returns to its compact view when you resume.

## Run from source

~~~powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
~~~

Launch **bobberbuddy.cmd**. Capture a bobber template in **Calibrate** or supply your own image. For the optional minimap, install **requirements-map.txt** in the same environment.
