from __future__ import annotations

from collections.abc import Callable
from functools import partial
from threading import Event, RLock

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from src.core.models import DnsProvider, NetworkAdapterInfo
from src.core.scanner import benchmark, check_current
from src.services.windows_net import WindowsNetwork


class TaskSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    log_line = Signal(str)


class Task(QRunnable):
    def __init__(self, operation: Callable, *args):
        super().__init__()
        self.signals = TaskSignals()
        self.operation = operation
        self.args = args

    def _execute(self):
        return self.operation(*self.args, log=self.signals.log_line.emit)

    def _publish(self, signal, *values) -> None:
        signal.emit(*values)

    @Slot()
    def run(self) -> None:
        try:
            result = self._execute()
        except Exception as exc:
            self._publish(self.signals.failed, str(exc) or type(exc).__name__)
        else:
            self._publish(self.signals.completed, result)


class ScanTask(Task):
    def __init__(self, providers: tuple[DnsProvider, ...], adapter: NetworkAdapterInfo, ipv6: bool):
        super().__init__(benchmark, providers, adapter, ipv6)
        self._cancelled = Event()
        self._signal_lock = RLock()

    def _execute(self):
        return self.operation(*self.args, cancel_event=self._cancelled,
                              log=partial(self._publish, self.signals.log_line),
                              progress=partial(self._publish, self.signals.progress))

    def cancel(self) -> None:
        with self._signal_lock:
            self._cancelled.set()

    def _publish(self, signal, *values) -> None:
        # Synchronize the check and emit with cancel(), including callbacks from the probe pool.
        with self._signal_lock:
            if not self._cancelled.is_set():
                signal.emit(*values)


class CurrentConnectionTask(Task):
    def __init__(self, adapter: NetworkAdapterInfo):
        super().__init__(check_current, adapter)


class ChangeDnsTask(Task):
    def __init__(self, service: WindowsNetwork, action: str, guid: str, provider: DnsProvider | None):
        super().__init__(service.change, action, guid, provider)
