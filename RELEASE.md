# Bobber Buddy v1.0.1

## Release notes

- First public Windows download. Extract the entire x64 ZIP, then open **Bobber Buddy.exe**.
- Update checks and download links use the renamed [BobberBuddy repository](https://github.com/scrmarsh/BobberBuddy).
- Bobber Buddy branding, an embedded Windows icon and version, and a portable x64 executable.
- Fishing states beneath the crosshair with optional transition notifications. The existing detector and two-stage rod verification still control fishing.
- Persistent minimap settings, in-game and desktop views, and remembered desktop window geometry.
- World-scoped waypoints and compass GPS with straight-line distances, arrival detection, and a map route.
- Asynchronous checks for the latest stable GitHub release and optional SHA-256-verified background downloads.

## Build and review

Run `python -m pip install -r requirements-build.txt`, `python -m unittest discover -s tests`, and `python build_release.py` from a Windows x64 checkout. The build creates the portable bundle, ZIP, and SHA-256 file in `dist`. The GitHub Actions workflow builds artifacts for review.

GitHub Releases hosts the portable ZIP and its SHA-256 checksum. For each release, use a tag matching the app version (for example, `v1.0.1`) and attach the assets built from that revision.

## Architecture and known limits

- Tkinter owns settings, overlays, and control state. OpenCV and MSS handle bobber, hotbar, and held-rod detection. A separate WebView2 process hosts Chunk Base and uses Windows OCR for shared coordinates.
- The portable package includes a starter bobber template. Calibrate with the installed resource pack and capture a custom template if needed.
- Navigation follows the configured dimension and uses a straight line. The in-game overlay is click-through. Use the separate map for keyboard and pointer interaction. Escape-menu expansion and navigation from Chunk Base search results require further integration.
- Verified downloads are saved locally. This release uses an unsigned portable executable and updates are installed manually.
- Before publication, exercise the package on clean Windows 10 and 11, test scaling and a disconnected second display, verify fishing and status transitions in a fresh session, check waypoint persistence, and verify map geometry and live navigation through a teleport.
