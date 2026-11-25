import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from src.services import windows_net
from src.services.app_instance import AppInstance
from src.ui import main_window
from src.workers.tasks import ScanTask, Task


ROOT = Path(__file__).resolve().parents[1]
# Each interpreter runs its own Qt event loop, lock and named pipe. Only the owner
# publishes a ready file. A stop file lets tests exercise a normal draining shutdown.
CHILD = """
import os, sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer
from src.services.app_instance import AppInstance

app = QCoreApplication([])
directory, mode, predecessor = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
gate = AppInstance(directory)
if not gate.start(tray=mode == 'tray', handoff_from=predecessor):
    sys.exit(0)
if mode == 'crash':
    os._exit(17)
pid = os.getpid()
def activated():
    with (directory / 'activations').open('a') as output:
        print(pid, file=output)
def check_stop():
    if (directory / f'stop-{pid}').exists():
        gate.stop_listening()
        app.quit()
# Let other launches connect before the window has finished initializing.
QTimer.singleShot(200, lambda: gate.on_activation(activated))
timer = QTimer()
timer.timeout.connect(check_stop)
timer.start(20)
QTimer.singleShot(20000, app.quit)
(directory / f'ready-{pid}').touch()
try:
    app.exec()
finally:
    gate.close()
"""


@pytest.fixture
def launch(tmp_path):
    children = []

    def start(mode="open", predecessor=0):
        child = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path), mode, str(predecessor)],
                                 cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
        children.append(child)
        return child

    yield start
    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)


def wait_until(condition):
    deadline = monotonic() + 8
    while not condition():
        if monotonic() >= deadline:
            pytest.fail("Timed out waiting for the local instance")
        sleep(0.025)


def test_concurrent_launches_activate_one_owner_and_tray_relaunch_stays_quiet(launch, tmp_path):
    processes = [launch() for _ in range(5)]
    wait_until(lambda: sum(p.poll() is None for p in processes) == 1)
    owner, = tmp_path.glob("ready-*")
    pid = int(owner.name.removeprefix("ready-"))
    assert all(p.returncode in (0, None) for p in processes)
    assert launch("tray").wait(timeout=5) == 0
    assert (tmp_path / "activations").read_text().splitlines() == [str(pid)] * 4
    (tmp_path / f"stop-{pid}").touch()
    assert all(p.wait(timeout=5) == 0 for p in processes)


def test_handoff_waits_for_its_predecessor_and_then_owns_the_tray(launch, tmp_path):
    first = launch()
    wait_until(lambda: bool(list(tmp_path.glob("ready-*"))))
    first_pid = int(next(tmp_path.glob("ready-*")).name.removeprefix("ready-"))
    second = launch(predecessor=first_pid)
    # A normal competing launch still finds the old primary during elevation.
    assert launch().wait(timeout=5) == 0
    assert (tmp_path / "activations").read_text().splitlines() == [str(first_pid)]
    assert second.poll() is None
    (tmp_path / f"stop-{first_pid}").touch()
    assert first.wait(timeout=5) == 0
    wait_until(lambda: len(list(tmp_path.glob("ready-*"))) == 2)
    second_pid, = {int(f.name.removeprefix("ready-")) for f in tmp_path.glob("ready-*")} - {first_pid}
    assert launch().wait(timeout=5) == 0
    assert (tmp_path / "activations").read_text().splitlines() == [str(first_pid), str(second_pid)]
    (tmp_path / f"stop-{second_pid}").touch()
    assert second.wait(timeout=5) == 0


def test_crashed_owner_does_not_prevent_restart(launch, tmp_path, qt_app):
    assert launch("crash").wait(timeout=5) == 17
    replacement = AppInstance(tmp_path)
    try:
        assert replacement.start()
    finally:
        replacement.close()


@pytest.fixture
def tray_window(monkeypatch, qt_app):
    monkeypatch.setattr(main_window.MainWindow, "refresh", lambda self: None)
    monkeypatch.setattr(main_window, "is_admin", lambda: False)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    win = main_window.MainWindow()
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.show()
    qt_app.processEvents()
    yield win
    win.tray.hide()
    win.hide()
    win.deleteLater()
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("busy", ["idle", "scan", "write", "write-error"])
def test_exit_from_hidden_window_removes_icon_and_finishes_work(tray_window, monkeypatch, busy):
    win = tray_window
    quit_app = Mock()
    monkeypatch.setattr(QApplication, "quit", quit_app)
    win.close()
    assert not win.isVisible() and win.tray.isVisible()
    win.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert win.isVisible()
    win.close()
    if busy == "scan":
        win.job = Mock(spec=ScanTask)
    elif busy.startswith("write"):
        win.job = Mock(spec=Task)
        win.job_kind = "apply"
    exiting = QSignalSpy(win.quitStarted)
    menu = win.tray.contextMenu()
    exit_action, = [a for a in menu.actions() if "Exit" in a.text()]
    exit_action.trigger()
    win.request_quit()
    win.open_window()
    assert exiting.count() == 1 and win.quitting
    assert not win.isVisible() and not win.tray.isVisible()
    if busy == "scan":
        win.job.cancel.assert_called_once_with()
    if busy.startswith("write"):
        quit_app.assert_not_called()
        if busy == "write-error":
            win.task_failed("mock failure")
        else:
            win.task_completed(object())
    quit_app.assert_called_once_with()


@pytest.mark.parametrize("shell_result", [5, 33])
def test_frozen_elevation_uses_fresh_bundle_and_parent_pid(monkeypatch, shell_result):
    def execute(hwnd, verb, exe, args, cwd, show):
        assert verb == "runas" and args == f"--handoff-from {os.getpid()}"
        assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        return shell_result

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", [sys.executable, "--tray"])
    monkeypatch.setenv("PYINSTALLER_RESET_ENVIRONMENT", "0")
    monkeypatch.setattr(windows_net.ctypes.windll.shell32, "ShellExecuteW", Mock(side_effect=execute))
    assert windows_net.restart_elevated() is (shell_result > 32)
    assert os.environ["PYINSTALLER_RESET_ENVIRONMENT"] == "0"
