import subprocess
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import QApplication

from src.core import scanner
from src.core.models import DomainTestDetail
from src.core.providers import BUILTINS
from src.services.windows_net import WindowsNetwork
from src.services import windows_net
from src.workers.tasks import ChangeDnsTask, CurrentConnectionTask, ScanTask


def observe(task):
    listeners = {}
    for name in ("completed", "failed", "progress", "log_line"):
        listener = Mock()
        getattr(task.signals, name).connect(listener, Qt.ConnectionType.DirectConnection)
        listeners[name] = listener
    return listeners


@pytest.mark.parametrize("inflight_error", [False, True])
def test_cancel_running_scan_stops_queued_probes_and_suppresses_late_signals(qt_app, adapter, monkeypatch, inflight_error):
    entered, release = Event(), Event()
    monkeypatch.setattr(scanner, "MAX_PROBES", 1)

    def delayed(domain, nic, context, server, label, log, **kwargs):
        log("before cancel")
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test fixture timed out")
        log("after cancel")
        if inflight_error:
            raise ConnectionResetError("after cancel")
        return DomainTestDetail(domain, True, True, 1, 2, "8.8.8.8")

    probe = Mock(side_effect=delayed)
    monkeypatch.setattr(scanner, "probe_domain", probe)
    task = ScanTask(BUILTINS, adapter, False)
    signals = observe(task)
    pool = QThreadPool()
    pool.start(task)
    try:
        assert entered.wait(3)
        signals["log_line"].assert_called_once_with("before cancel")
        task.cancel()
        task.cancel()
    finally:
        release.set()
        assert pool.waitForDone(5000)
    assert probe.call_count == 1
    assert signals["log_line"].call_count == 1
    for name in ("completed", "failed", "progress"):
        signals[name].assert_not_called()


def test_cancel_before_start_does_no_work(qt_app, adapter):
    task = ScanTask(BUILTINS, adapter, False)
    signals = observe(task)
    task.cancel()
    with patch.object(scanner.ssl, "create_default_context") as tls:
        task.run()
        tls.assert_not_called()
    for listener in signals.values():
        listener.assert_not_called()


def test_exit_cancels_scan_but_waits_for_active_configuration_write(qt_app, adapter, tmp_path):
    from src.ui.main_window import MainWindow

    scan = ScanTask(BUILTINS, adapter, False)
    window = SimpleNamespace(quitting=False, job=scan, tray=Mock(), tray_menu=Mock(),
                             quitStarted=Mock(), hide=Mock())
    with patch.object(scan, "cancel", wraps=scan.cancel) as cancel, patch.object(QApplication, "quit") as quit_app:
        MainWindow.request_quit(window)
        assert window.quitting
        cancel.assert_called_once()
        quit_app.assert_called_once()
        quit_app.reset_mock()
        window.quitting = False
        window.job = ChangeDnsTask(WindowsNetwork(tmp_path), "dhcp", adapter.guid, None)
        MainWindow.request_quit(window)
        quit_app.assert_not_called()


def test_event_loop_exit_drains_pool_even_without_tray_quit(qt_app, adapter):
    from src.ui.main_window import MainWindow

    task = ScanTask(BUILTINS, adapter, False)
    ordering = Mock()
    window = SimpleNamespace(job=task, pool=ordering.pool)
    with patch.object(task, "cancel", side_effect=ordering.cancel):
        MainWindow.finish_workers(window)
    assert [call[0] for call in ordering.mock_calls] == ["cancel", "pool.waitForDone"]


@pytest.mark.parametrize("hold_at", ["write", "postcheck"])
@pytest.mark.parametrize("same_profile", [True, False])
def test_apply_and_reset_cannot_overlap_even_across_service_instances(qt_app, tmp_path, adapter, network_machine,
                                                                    hold_at, same_profile):
    machine = network_machine
    entered, release, second_done = Event(), Event(), Event()
    callback = machine.writer if hold_at == "write" else machine.check
    original = callback.side_effect or (lambda *args: callback.return_value)

    def hold_first(*args):
        if not entered.is_set():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test fixture timed out")
        return original(*args)

    callback.side_effect = hold_first
    first = ChangeDnsTask(machine.service, "apply", adapter.guid, BUILTINS[0])
    second = ChangeDnsTask(WindowsNetwork(tmp_path if same_profile else tmp_path / "other-profile"), "dhcp", adapter.guid, None)
    left, right = observe(first), observe(second)
    for signal in (second.signals.failed, second.signals.completed):
        signal.connect(lambda result: second_done.set(), Qt.ConnectionType.DirectConnection)
    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    pool.start(first)
    try:
        assert entered.wait(3)
        assert machine.service.backups()[adapter.guid].ipv4 == adapter.settings_v4
        pool.start(second)
        assert second_done.wait(3)
        assert right["failed"].call_count == 1 and "đang cập nhật" in right["failed"].call_args.args[0]
        right["completed"].assert_not_called()
        assert machine.writer.call_count == 1
    finally:
        release.set()
        assert pool.waitForDone(5000)
    left["completed"].assert_called_once()
    left["failed"].assert_not_called()
    assert machine.adapter.dns_v4 == BUILTINS[0].ipv4
    assert machine.service.backups()[adapter.guid].ipv4 == adapter.settings_v4


@pytest.mark.parametrize("fails", [True, False])
def test_quick_check_delivers_exactly_one_terminal_signal(qt_app, adapter, fails):
    result = (DomainTestDetail("store.steampowered.com", True, True, 2, 3, "8.8.8.8"),)
    with patch("src.workers.tasks.check_current", side_effect=OSError("offline") if fails else None,
               return_value=result):
        task = CurrentConnectionTask(adapter)
        signals = observe(task)
        task.run()
    if fails:
        signals["completed"].assert_not_called()
        signals["failed"].assert_called_once_with("offline")
    else:
        signals["completed"].assert_called_once_with(result)
        signals["failed"].assert_not_called()


def test_cancel_during_resolution_prevents_tcp_handshake(qt_app, adapter, monkeypatch):
    entered, release = Event(), Event()
    monkeypatch.setattr(scanner, "MAX_PROBES", 1)

    def delayed_dns(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test fixture timed out")
        return [SimpleNamespace(address="8.8.8.8")]

    resolver = Mock()
    resolver.resolve.side_effect = delayed_dns
    with patch.object(scanner.dns.resolver, "Resolver", return_value=resolver), \
         patch.object(scanner.socket, "create_connection") as tcp:
        task = ScanTask(BUILTINS, adapter, False)
        signals = observe(task)
        pool = QThreadPool()
        pool.start(task)
        try:
            assert entered.wait(3)
            task.cancel()
        finally:
            release.set()
            assert pool.waitForDone(5000)
        resolver.resolve.assert_called_once()
        tcp.assert_not_called()
        for listener in signals.values():
            listener.assert_not_called()


def test_write_mutex_is_shared_between_processes_and_released_after_exit():
    code = """
import sys
from src.services import windows_net as net
net.WRITE_MUTEX_NAME = sys.argv[1]
try:
    with net._network_write_lock():
        print('acquired')
except RuntimeError:
    print('busy')
"""
    def try_child():
        result = subprocess.run([sys.executable, "-c", code, windows_net.WRITE_MUTEX_NAME],
                                cwd=Path(windows_net.__file__).resolve().parents[2], capture_output=True,
                                text=True, timeout=10, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return result.stdout.strip()

    with windows_net._network_write_lock():
        assert try_child() == "busy"
    assert try_child() == "acquired"
