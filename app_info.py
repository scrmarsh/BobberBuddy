import os
import sys
from pathlib import Path


APP_NAME = "Bobber Buddy"
VERSION = "1.0.1"
RELEASE_REPOSITORY = "scrmarsh/BobberBuddy"
ROOT = Path(__file__).resolve().parent
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = Path(os.environ["LOCALAPPDATA"]) / "Bobber Buddy" if FROZEN else ROOT / ".local"
ASSETS = ROOT / "assets"
