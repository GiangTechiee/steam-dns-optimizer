from datetime import datetime, timedelta, timezone
from pathlib import Path


APP_NAME = "Steam DNS Optimizer"
RELEASED_AT = datetime(2026, 11, 25, 13, 27, tzinfo=timezone(timedelta(hours=7)))
VERSION = RELEASED_AT.strftime("%H%M%d%m%y")
TAG = f"v{VERSION}"
REPOSITORY_URL = "https://github.com/GiangTechiee/steam-dns-optimizer"
RELEASE_URL = f"{REPOSITORY_URL}/releases/tag/{TAG}"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
APP_ICON = ASSETS / "steam-dns-optimizer.ico"
