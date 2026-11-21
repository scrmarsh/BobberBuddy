# Seedside

Seedside is an unofficial Windows companion for Minecraft Java and Bedrock. It includes a live seed map and **BobberBuddy**, a fishing detector with an optional auto-reel mode.

## Open the tool

Double-click `seedside.cmd`. For a fresh checkout, run the following in PowerShell first.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe fetch_references.py
```

## BobberBuddy

Select your edition and use **Change** to choose a bobber template. After saving a region in **Calibrate**, cast in Minecraft and press **F8** to start or stop detection. **F9** stops detection or cancels a calibration capture. Use **Rebind** beside either action to save a new key from F1 through F11.

The menu art is decorative. Detection uses your selected template in the saved screen region.

### Reel in on a bite

1. In **Calibrate**, record a sample with the bobber floating and leave it out until you see a bite. The replay marks **Bubbles** as a wake approaches and **Bite** when the detector confirms a dip. Leave some room around the float in the search box so the approaching particles stay in view.
2. Go back to the main menu and enable **Auto-reel on bite**. Cast manually, then press **F8** in Minecraft. BobberBuddy sends one right-click on a confirmed bite while the selected edition is in focus and in its gameplay view.
3. Cast again when you are ready. **F9** stops detection and reeling. Auto-reel starts off each time BobberBuddy opens.

An approaching wake helps confirm a bite when the bobber disappears under the water. A visible downward dip can trigger without a wake. Bubbles alone never send input.

## Seed mini-map

Install the optional Windows map dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements-map.txt`. The mini-map uses Microsoft Edge WebView2 Runtime.

1. Select **Seed map** in Seedside. Enter the world's seed and choose its edition, version, and current dimension.
2. In Bedrock, turn on **Show Coordinates**. In Java, open the F3 screen and leave the XYZ line visible. Leave **Follow coordinates shown in Minecraft** checked to center the mini-map on your current position. You can also turn it off and enter X/Z to use a fixed center.
3. Choose **Second display · full screen**, **In-game overlay · bottom right**, or **Both views**. The full-screen map defaults to a different display from the one selected in fishing calibration. The bottom-right overlay is click-through and appears when Minecraft has focus. Use windowed or borderless Minecraft for the overlay.
4. Click **Open map** and return to Minecraft. The selected views follow the same X/Z coordinates. Close them from **Seed map → Close map views**, or use Alt+F4 on the full-screen map.

Coordinate recognition runs on the computer using Windows OCR. The online [Chunk Base seed map](https://www.chunkbase.com/apps/seed-map) uses the entered seed and horizontal coordinates to predict world generation. The map needs internet access and predicts terrain and structures from the chosen game version. It does not read your save file or show builds, terrain edits, or other changes you made in-game.

## Calibrate a fishing spot

1. Select your edition and click **Calibrate**. **Online example** opens a gameplay screenshot with a search region around the float. Green marks a candidate that passes the minimum score. Orange marks the best candidate below the minimum.
2. Put Minecraft in windowed or borderless mode and cast manually. In BobberBuddy, select the game's monitor and click **Capture screen (4s)**. Return to Minecraft before the capture.
3. Drag a rectangle around the water where the bobber landed. Use a small region, ideally around 150 × 150 pixels if you plan to keep the camera still. If the online template scores poorly, select the float tightly, click **Use region as template**, then draw the larger search region again.
4. Click **Sample region (12s)** and return to Minecraft. Keep the camera still. Leave the bobber floating for several seconds, then reel in manually and leave the same water empty.
5. Scrub **Replay frame** and move **Minimum match score**. Look for a cutoff that passes frames with the real bobber and rejects frames after reeling in. Repeat with a different cast, lighting, or resource pack to check that your cutoff holds.

BobberBuddy remembers the last edition and monitor, plus each edition's template, minimum score and live search region. Capture the game again on the same monitor to reuse the region. **Open template** accepts a tightly cropped image and supports an alpha mask. **Use online template** restores the screenshot-derived reference for the selected edition.

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

- The saved ocean calibration uses an in-game bobber template and a 0.820 minimum match score. A live replay showed the wake reaching the float as it dipped. A separate live detector run reported a bubble-assisted bite with the Minecraft gameplay window in focus.
- The first auto-reel trial left the line in the water. BobberBuddy now holds the right mouse button for 55 milliseconds and reports how many reel clicks it sent. The revised click needs to be verified against a real catch.
- Testing is paused with auto-reel off. Resume by watching the next manual fishing demonstration and comparing the bubble trail, float movement, and right-click timing.
