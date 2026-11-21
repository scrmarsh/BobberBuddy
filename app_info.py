import os
import sys
from pathlib import Path


APP_NAME = "Bobber Buddy"
VERSION = "1.0.3"
UPDATER_NAME = "Bobber Buddy Updater"
RELEASE_REPOSITORY = "scrmarsh/BobberBuddy"
ROOT = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)
ASSETS = ROOT / "assets"

if FROZEN:
    bundle = Path(sys.executable).parent
    DATA_DIR = (bundle / "profile" if (bundle / "portable.flag").is_file()
                else Path(os.environ["LOCALAPPDATA"]) / APP_NAME)
else:
    DATA_DIR = ROOT / ".local"
