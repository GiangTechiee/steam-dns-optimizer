from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from hashlib import sha256
from pathlib import Path
from time import monotonic, sleep

from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class AppInstance:
    """One tray owner per Windows user profile and interactive session."""

    def __init__(self, directory: Path):
        session = wintypes.DWORD()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        directory.mkdir(parents=True, exist_ok=True)
        path = directory.resolve() / f"window-{session.value}.lock"
        self._lock = QLockFile(str(path))
        self._lock.setStaleLockTime(0)
        profile_key = sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:32]
        self._name = f"SteamDNSOptimizer.Window.{profile_key}"
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def start(self, *, tray: bool = False, handoff_from: int = 0) -> bool:
        """Return True only to the process allowed to construct the main window."""
        deadline = monotonic() + 20
        while monotonic() < deadline:
            # On Windows two QLocalServers can listen to the same named pipe.
            if self._lock.tryLock(0):
                if self._server.listen(self._name):
                    return True
                self._lock.unlock()
                raise OSError(f"Không thể mở kênh kích hoạt ứng dụng. {self._server.errorString()}")
            if self._lock.error() != QLockFile.LockError.LockFailedError:
                raise OSError(f"Không thể mở khóa phiên tại {self._lock.fileName()}")
            owner_pid = self._lock.getLockInfo()[0]
            if owner_pid and owner_pid != handoff_from:
                if tray or self._show_existing(owner_pid, deadline):
                    return False
            # The owner may be starting its listener, finishing a job, or handing off after UAC.
            sleep(0.05)
        raise OSError("Phiên Steam DNS Optimizer đang bận hoặc đang thoát. Hãy mở lại sau ít giây.")

    def _show_existing(self, owner_pid: int, deadline: float) -> bool:
        client = QLocalSocket()
        try:
            # Transfer the launching process's foreground permission to the existing window.
            ctypes.windll.user32.AllowSetForegroundWindow(owner_pid)
            client.connectToServer(self._name)
            if not client.waitForConnected(150):
                return False
            timeout = max(1, round((deadline - monotonic()) * 1000))
            if not client.bytesAvailable() and not client.waitForReadyRead(timeout):
                return False
            return bytes(client.read(1)) == b"1"
        finally:
            client.abort()

    def on_activation(self, show_window: Callable[[], None]) -> None:
        def accept_pending() -> None:
            while self._server.hasPendingConnections():
                peer = self._server.nextPendingConnection()
                peer.disconnected.connect(peer.deleteLater)
                show_window()
                peer.write(b"1")
                peer.disconnectFromServer()

        # Clients can arrive while Fluent is still building the primary window.
        self._server.newConnection.connect(accept_pending)
        accept_pending()

    def stop_listening(self) -> None:
        self._server.close()

    def close(self) -> None:
        self.stop_listening()
        if self._lock.isLocked():
            self._lock.unlock()
