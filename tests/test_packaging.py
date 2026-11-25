import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


EXE = Path(os.environ.get("STEAMDNS_TEST_EXE", Path(__file__).resolve().parents[1] / "dist" / "SteamDNSOptimizer.exe"))


@unittest.skipUnless(EXE.is_file(), "Chạy build.ps1 trước để kiểm tra gói một tệp.")
class FrozenQtTests(unittest.TestCase):
    def test_qtcore_loads_with_the_bundled_windows_libraries(self):
        archive = CArchiveReader(str(EXE))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in archive.toc:
                relative = Path(name)
                if relative.suffix.lower() == ".dll" and relative.parent in (Path("."), Path("PySide6")):
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extract(name))
            # A fresh process reproduces the frozen loader's search directories. In particular
            # a foreign icuuc.dll on the builder's PATH can omit every ICU export Qt imports.
            script = (
                "import ctypes, os, sys\n"
                "from pathlib import Path\n"
                "root = Path(sys.argv[1])\n"
                "paths = [os.add_dll_directory(str(d)) for d in (root, root / 'PySide6')]\n"
                "ctypes.WinDLL(str(root / 'PySide6' / 'Qt6Core.dll'))\n"
            )
            run = subprocess.run([sys.executable, "-I", "-c", script, str(root)], capture_output=True,
                                 text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                                 env={**os.environ, "PATH": str(Path(os.environ["SystemRoot"]) / "System32")})
            self.assertEqual(run.returncode, 0, run.stderr)
