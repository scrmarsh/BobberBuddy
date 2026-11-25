# Bobber Buddy v1.0.6

## Release notes

- Wait for camera movement as well as walking to settle before automatic casting. Water checks use the actual crosshair position in the raised search area.
- Recover an uncertain line with one controlled probe after eight seconds, without counting it as a catch or repeatedly clicking without a visual lock.
- Use a fast candidate search with masked verification for Bedrock acquisition, reject gray background decoys, and track a locked float using its acquired reference and scale.
- Add a masked reference for a closer bobber viewed from above. A visible red float takes precedence over ambiguous stowed-rod matches.
- Disable frameless-window dragging on the map, draw the player marker in both views, and refresh centering after dimension changes or canvas replacement.
- Tighten opt-in reward evidence to ignore enchantment shimmer and fading XP; cache HUD captures to reduce bite-frame delay.

- Raise the automatic capture area and allow acquisition along its top edge so distant floats remain fully visible on laptop displays.
- Check that the red float has submerged before treating splash-related confidence loss as a bite.
- A verified stowed hook now permits the initial cast even when water or another float produces a template match.
- Force the compact minimap to fill its square despite the provider's inline height rules, and restore the expanded layout when Minecraft is paused.
- Preserve the full coordinate line during Bedrock OCR instead of truncating it at wide spaces.
- Add opt-in local reward-evidence recording with `BOBBER_BUDDY_AUDIT=1`; normal fishing does not record screenshots.

- One fishing notification appears below the crosshair. Bundled Bedrock references improve detection in windowed gameplay, and automatic recast waits through brief interruptions in hotbar detection.
- Fixed an embedded minimap crash during route rendering after a live coordinate update.
- Automatic casting waits for a verified rod, stationary gameplay, and visible water. The fishing indicator fades after five seconds of inactivity.
- The optional minimap accepts text seeds such as `IV` and reports the launch log if its embedded window fails.
- Verified Windows updates install and restart the app when idle, with rollback if the new version fails to open. Upgrading from v1.0.2 requires extracting the new ZIP once.

- Bedrock auto-fishing starts on a clean profile with bundled rod and bobber references. The hotbar matcher ignores the durability meter for both bundled and learned rods.
- Vibrant Visuals support adds three small bobber crops, improves detection of shaded and reflective water, and recognizes a splash when a locked bobber submerges.
- The optional minimap saves named seeds, exposes filters during the Escape menu, and offers a browser fallback if its embedded view fails to start.
- Extract the Windows x64 ZIP and open **Bobber Buddy.exe**. [Release and SHA-256 download](https://github.com/scrmarsh/BobberBuddy/releases/tag/v1.0.6)

## Build and review

Run `python -m pip install -r requirements-build.txt`, `python -m unittest discover -s tests`, and `python build_release.py` from a Windows x64 checkout. The build creates the portable bundle, ZIP, and SHA-256 file in `dist`. The GitHub Actions workflow builds artifacts for review.

GitHub Releases hosts the portable ZIP and its SHA-256 checksum. For each release, use a tag matching the app version and attach the assets built from that revision.

## Architecture and known limits

- Tkinter owns settings, overlays, and control state. OpenCV and MSS handle bobber, hotbar, and held-rod detection. A separate WebView2 process hosts Chunk Base and uses Windows OCR for shared coordinates.
- Java Edition requires rod calibration. Capture a custom template or learn the rod if a resource pack or enchantment changes its appearance.
- Navigation follows the configured dimension and uses a straight line. The in-game map is click-through during gameplay and accepts pointer input in the pause menu. Use the separate map for keyboard navigation.
- The portable executable is unsigned. Update downloads are verified against the SHA-256 digest provided by GitHub.
- Validation covers 44 automated tests, bounded live Bedrock fishing, and automatic startup at a moved pond position that previously stalled. A saved-frame acquisition benchmark improved from 15.6 seconds to about 1.2 seconds. Live tests across every angle, a completed 200-catch run, and live Nether tracking on the other PC remain unverified.
- Earlier v1.0.5 reward counts used HUD changes that could include enchantment shimmer and should not be treated as verified catch streaks. The v1.0.6 diagnostic counter uses stricter evidence and can undercount when rewards enter inventory or Mending absorbs XP.
