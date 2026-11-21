# Bobber Buddy v1.0.4

## Release notes

- One fishing notification appears below the crosshair. Bundled Bedrock references improve detection in windowed gameplay, and automatic recast waits through brief interruptions in hotbar detection.
- Fixed an embedded minimap crash during route rendering after a live coordinate update.
- Automatic casting waits for a verified rod, stationary gameplay, and visible water. The fishing indicator fades after five seconds of inactivity.
- The optional minimap accepts text seeds such as `IV` and reports the launch log if its embedded window fails.
- Verified Windows updates install and restart the app when idle, with rollback if the new version fails to open. Upgrading from v1.0.2 requires extracting the new ZIP once.

- Bedrock auto-fishing starts on a clean profile with bundled rod and bobber references. The hotbar matcher ignores the durability meter for both bundled and learned rods.
- Vibrant Visuals support adds three small bobber crops, improves detection of shaded and reflective water, and recognizes a splash when a locked bobber submerges.
- The optional minimap saves named seeds, exposes filters during the Escape menu, and offers a browser fallback if its embedded view fails to start.
- Extract the Windows x64 ZIP and open **Bobber Buddy.exe**. [Release and SHA-256 download](https://github.com/scrmarsh/BobberBuddy/releases/tag/v1.0.4)

## Build and review

Run `python -m pip install -r requirements-build.txt`, `python -m unittest discover -s tests`, and `python build_release.py` from a Windows x64 checkout. The build creates the portable bundle, ZIP, and SHA-256 file in `dist`. The GitHub Actions workflow builds artifacts for review.

GitHub Releases hosts the portable ZIP and its SHA-256 checksum. For each release, use a tag matching the app version and attach the assets built from that revision.

## Architecture and known limits

- Tkinter owns settings, overlays, and control state. OpenCV and MSS handle bobber, hotbar, and held-rod detection. A separate WebView2 process hosts Chunk Base and uses Windows OCR for shared coordinates.
- Java Edition requires rod calibration. Capture a custom template or learn the rod if a resource pack or enchantment changes its appearance.
- Navigation follows the configured dimension and uses a straight line. The in-game map is click-through during gameplay and accepts pointer input in the pause menu. Use the separate map for keyboard navigation.
- The portable executable is unsigned. Update downloads are verified against the SHA-256 digest provided by GitHub.
- Validation covered 29 automated tests and a live windowed Bedrock run with 19 reels, 18 scheduled recasts, and one recovery cast. The 100-catch streak and laptop compatibility remain unverified.
