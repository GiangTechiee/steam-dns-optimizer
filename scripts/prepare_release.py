"""Prepare PyInstaller's Windows version resource and redistributable notices."""

from importlib.metadata import distribution
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)

from src.app_info import APP_NAME, RELEASED_AT, VERSION


def write_version_info(path: Path) -> None:
    stamp = RELEASED_AT
    numeric = (stamp.year, stamp.month, stamp.day, stamp.hour * 100 + stamp.minute)
    strings = {
        "CompanyName": "GiangTechiee", "FileDescription": APP_NAME, "FileVersion": VERSION,
        "InternalName": "SteamDNSOptimizer", "OriginalFilename": "SteamDNSOptimizer.exe",
        "ProductName": APP_NAME, "ProductVersion": VERSION,
    }
    table = StringTable("040904B0", [StringStruct(key, value) for key, value in strings.items()])
    info = VSVersionInfo(FixedFileInfo(filevers=numeric, prodvers=numeric, mask=0x3F,
                                       flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
                         [StringFileInfo([table]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])
    path.write_text(str(info), encoding="utf-8")


def write_notices(root: Path, archive: ZipFile) -> None:
    sources = (root / "LICENSE", root / "THIRD_PARTY.md", root / "docs/build-constraints.txt",
               *(root / "licenses").glob("*.txt"))
    for source in sources:
        archive.write(source, source.relative_to(root))
    archive.write(Path(sys.base_prefix) / "LICENSE.txt", "CPython-LICENSE.txt")
    # Preserve the upstream directory structure, including licenses for bundled native libraries.
    libraries = ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "PySide6-Fluent-Widgets",
                 "PySideSix-Frameless-Window", "dnspython", "darkdetect", "numpy", "scipy", "pillow",
                 "pywin32", "pyinstaller", "packaging")
    for name in libraries:
        dist = distribution(name)
        for file in dist.files or ():
            if len(file.parts) < 2 or not file.parts[0].endswith(".dist-info"):
                continue
            relative = Path(*file.parts[1:])
            if relative.parts[0].lower() != "licenses" and not relative.name.lower().startswith(
                ("license", "copying", "notice")
            ):
                continue
            archive.write(dist.locate_file(file), Path(name) / relative)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    write_version_info(out / "version.txt")
    with ZipFile(out / "ThirdPartyNotices.zip", "w", compression=ZIP_DEFLATED) as notices:
        write_notices(root, notices)
