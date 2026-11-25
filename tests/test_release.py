import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QIcon

from src.app_info import APP_ICON, APP_NAME, ASSETS, RELEASED_AT, RELEASE_URL, REPOSITORY_URL, TAG, VERSION
from src.ui.pages.about_page import AboutPage


@pytest.mark.parametrize("asset", [ASSETS / "logo.png", APP_ICON], ids=["logo", "windows-icon"])
def test_logo_has_no_transparent_holes(asset):
    with Image.open(asset) as artwork:
        if artwork.format == "ICO":
            sizes = artwork.ico.sizes()
            assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes
            frames = [artwork.ico.getimage(size) for size in sorted(sizes)]
        else:
            frames = [artwork]
        for frame in frames:
            # Cover the face around the old needle while allowing rounded outer corners.
            alpha = frame.convert("RGBA").getchannel("A")
            width, height = frame.size
            mx, my = width // 8, height // 8
            face = alpha.crop((mx, my, width - mx, height - my))
            assert face.getextrema() == (255, 255), f"Translucent face at {frame.size}"


def test_version_matches_vietnam_release_time_and_command_line():
    assert RELEASED_AT.utcoffset().total_seconds() == 7 * 3600
    assert VERSION == RELEASED_AT.strftime("%H%M%d%m%y")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "main.py", "--version"], cwd=root, check=True,
                            text=True, capture_output=True, timeout=8)
    assert result.stdout.strip() == f"{APP_NAME} {VERSION}"


def test_about_presents_the_version_and_opens_its_github_links(qt_app, monkeypatch):
    page = AboutPage()
    assert TAG in page.version_label.text()
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    for button in (page.repo_link, page.release_link):
        button.click()
    assert opened == [REPOSITORY_URL, f"{REPOSITORY_URL}/releases/tag/{TAG}"]
    assert page.release_link.getUrl() == QUrl(RELEASE_URL)
    assert (ASSETS / "logo.png").is_file()
    assert not QIcon(str(APP_ICON)).pixmap(16, 16).isNull()
