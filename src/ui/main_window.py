from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QResizeEvent
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
from qfluentwidgets import (
    Action, FluentIcon as FIF, FluentWindow, InfoBar, InfoBarPosition,
    NavigationDisplayMode, NavigationItemPosition,
)

from src.app_info import APP_ICON, APP_NAME
from src.core.models import DnsProvider, Grade
from src.core.providers import ProviderStore
from src.services.windows_net import WindowsNetwork, is_admin, list_adapters, restart_elevated
from src.ui.components.common import COMPACT_BELOW, WIDE_FROM, confirm
from src.ui.pages.about_page import AboutPage
from src.ui.pages.backup_logs_page import BackupLogsPage
from src.ui.pages.custom_dns_page import CustomDnsPage
from src.ui.pages.scanner_page import ScannerPage
from src.workers.tasks import ChangeDnsTask, CurrentConnectionTask, ScanTask, Task


class MainWindow(FluentWindow):
    quitStarted = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.setMicaEffectEnabled(sys.getwindowsversion().build >= 22000)
        self.navigationInterface.setExpandWidth(216)
        self.navigationInterface.setMinimumExpandWidth(WIDE_FROM)
        self._compact_navigation: bool | None = None
        self.network = WindowsNetwork()
        self.admin = is_admin()
        self.pool = QThreadPool(self)
        self.job: Task | None = None
        self.job_kind = ""
        self.quitting = False
        self.scanner = ScannerPage(self.admin)
        self.custom = CustomDnsPage(ProviderStore())
        self.logs = BackupLogsPage(self.network)
        self.about = AboutPage()
        self.addSubInterface(self.scanner, FIF.SPEED_HIGH, "Quét & Tối ưu DNS", NavigationItemPosition.TOP, isTransparent=True)
        self.addSubInterface(self.custom, FIF.GLOBE, "Danh sách DNS", NavigationItemPosition.TOP, isTransparent=True)
        self.addSubInterface(self.logs, FIF.DOCUMENT, "Sao lưu & Nhật ký", NavigationItemPosition.BOTTOM, isTransparent=True)
        self.addSubInterface(self.about, FIF.INFO, "Giới thiệu", NavigationItemPosition.BOTTOM, isTransparent=True)
        self._create_tray()
        self.custom.changed.connect(self.scope_changed)
        self.logs.changed.connect(self.sync_controls)
        self.scanner.scopeChanged.connect(self.scope_changed)
        self.scanner.refreshRequested.connect(self.refresh)
        self.scanner.scanRequested.connect(self.start_scan)
        self.scanner.currentRequested.connect(self.start_current)
        self.scanner.elevateRequested.connect(self.elevate)
        self.scanner.changeRequested.connect(self.request_change)
        self.custom.reload()
        self.logs.reload()
        self.sync_controls()
        # Mica initializes the native handle with the frameless window's initial geometry.
        self.setMinimumSize(640, 520)
        self.resize(980, 690)
        self.move(self.screen().availableGeometry().center() - self.rect().center())
        QTimer.singleShot(0, self.refresh)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "scanner"):
            return
        width = event.size().width()
        self.scanner.adapt_to_width(width)
        compact = width < COMPACT_BELOW
        if compact == self._compact_navigation:
            return
        self._compact_navigation = compact
        nav = self.navigationInterface
        nav.setMenuButtonVisible(not compact)
        if compact and nav.panel.displayMode != NavigationDisplayMode.COMPACT:
            # Fluent ignores collapse() during an unfinished expansion.
            nav.panel.expandAni.stop()
            nav.panel.collapse()

    def _create_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(APP_NAME)
        self.tray_menu = QMenu(self)
        open_action = Action(FIF.SPEED_HIGH, "Mở cửa sổ chính", self)
        self.quick_action = Action(FIF.PLAY, "Áp dụng nhanh DNS tối ưu gần nhất", self)
        self.dhcp_action = Action(FIF.RETURN, "Đặt lại về DHCP", self)
        quit_action = Action(FIF.POWER_BUTTON, "Thoát hoàn toàn (Exit)", self)
        for action in (open_action, self.quick_action, self.dhcp_action):
            self.tray_menu.addAction(action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(quit_action)
        self.tray.setContextMenu(self.tray_menu)
        open_action.triggered.connect(self.open_window)
        self.quick_action.triggered.connect(lambda: self.request_change("apply", self.scanner.best))
        self.dhcp_action.triggered.connect(lambda: self.request_change("dhcp", None))
        quit_action.triggered.connect(self.request_quit)
        self.tray.activated.connect(self._tray_activated)
        if self.tray.isSystemTrayAvailable():
            self.tray.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()

    @Slot()
    def open_window(self) -> None:
        if self.quitting:
            return
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        target = QApplication.activeModalWidget() or self
        target.raise_()
        target.activateWindow()

    def _notify(self, kind: str, title: str, content: str = "") -> None:
        if not self.isVisible() and self.tray.isVisible():
            self.tray.showMessage(title, content, self.windowIcon(), 3500)
        else:
            maker = {"success": InfoBar.success, "warning": InfoBar.warning, "error": InfoBar.error}[kind]
            maker(title, content, duration=4200, position=InfoBarPosition.TOP_RIGHT, parent=self)

    def enabled_providers(self) -> tuple[DnsProvider, ...]:
        return tuple(p for p in self.custom.providers if p.key not in self.custom.disabled
                     and (not self.scanner.ipv6 or p.ipv6_primary))

    @Slot()
    def scope_changed(self) -> None:
        self.scanner.clear_report()
        self.sync_controls()

    @Slot()
    def sync_controls(self) -> None:
        busy = self.job is not None
        adapter = self.scanner.adapter
        has_backup = bool(adapter and adapter.guid in self.logs.records)
        self.scanner.set_controls(busy, len(self.enabled_providers()), has_backup)
        self.custom.table.setEnabled(not busy)
        self.custom.add_button.setEnabled(not busy)
        self.logs.delete_button.setEnabled(not busy and self.logs.adapters.currentData() in self.logs.records)
        writable = self.admin and not busy and adapter is not None
        best = self.scanner.best
        self.quick_action.setEnabled(bool(writable and best and not adapter.uses(best)))
        self.dhcp_action.setEnabled(writable)

    def start_task(self, kind: str, task: Task) -> None:
        if self.job is not None or self.quitting:
            return
        self.job = task
        self.job_kind = kind
        queued = Qt.ConnectionType.QueuedConnection
        task.signals.log_line.connect(self.logs.append_log, queued)
        task.signals.progress.connect(self.scanner.set_progress, queued)
        task.signals.completed.connect(self.task_completed, queued)
        task.signals.failed.connect(self.task_failed, queued)
        self.sync_controls()
        self.pool.start(task)

    @Slot()
    def refresh(self) -> None:
        self.start_task("inventory", Task(list_adapters))

    @Slot()
    def start_scan(self) -> None:
        providers = self.enabled_providers()
        adapter = self.scanner.adapter
        if self.job is None and adapter and providers:
            self.scanner.clear_report()
            self.scanner.set_progress(0, len(providers))
            version = 6 if self.scanner.ipv6 else 4
            self.logs.append_log(f"Quét {len(providers)} DNS qua IPv{version} trên {adapter.name}.")
            self.start_task("scan", ScanTask(providers, adapter, self.scanner.ipv6))

    @Slot()
    def start_current(self) -> None:
        adapter = self.scanner.adapter
        if self.job is None and adapter:
            self.scanner.status.setText("Đang kiểm tra…")
            self.scanner.status_detail.setText("Đang kiểm tra DNS Windows và HTTPS tới Steam.")
            self.scanner.status_detail.setToolTip(adapter.name)
            self.start_task("current", CurrentConnectionTask(adapter))

    @Slot(str, object)
    def request_change(self, action: str, provider: DnsProvider | None) -> None:
        adapter = self.scanner.adapter
        if self.job is not None or not adapter or self.quitting:
            return
        self.open_window()
        if not self.admin:
            self._notify("warning", "Cần quyền Admin để đổi DNS", "Dùng nút nâng quyền ở thẻ card mạng.")
            return
        if action == "apply" and provider:
            title, verb = f"Áp dụng {provider.name}?", "Áp dụng"
            v6 = ", ".join(provider.ipv6) if provider.ipv6 else "Giữ cấu hình IPv6 hiện tại"
            body = f"IPv4  {', '.join(provider.ipv4)}\nIPv6  {v6}"
        elif action == "restore":
            record = self.logs.records.get(adapter.guid)
            if record is None:
                self._notify("warning", "Chưa có bản sao cho card này")
                return
            title, verb = "Khôi phục DNS gốc?", "Khôi phục"
            body = f"IPv4  {record.ipv4.description}\nIPv6  {record.ipv6.description}"
        elif action == "dhcp":
            title, verb = "Đặt lại DNS về tự động?", "Đặt lại"
            body = "Windows sẽ tự nhận máy chủ DNS cho IPv4 và IPv6 đang bật."
        else:
            return
        content = f"Card mạng: {adapter.name}\n{body}"
        if adapter.guid not in self.logs.records:
            content += "\n\nCấu hình hiện tại sẽ được sao lưu trước khi ghi."
        if action in ("restore", "dhcp"):
            content += "\n\nSau khi xác nhận đủ cấu hình, ứng dụng sẽ kết thúc chu kỳ sao lưu của card này."
        if confirm(self, title, content, verb):
            self.logs.append_log(f"{verb} DNS · {adapter.name}")
            self.start_task(action, ChangeDnsTask(self.network, action, adapter.guid, provider))

    @Slot(object)
    def task_completed(self, result: object) -> None:
        kind = self.job_kind
        self.job = None
        if self.quitting:
            QApplication.quit()
            return
        if kind == "inventory":
            self.scanner.adapters = result
            self.scanner.populate_adapters()
        elif kind == "scan":
            self.scanner.show_report(result)
            passed = sum(r.grade == Grade.PASS for r in result.results)
            message = f"{passed}/{len(result.results)} nhà cung cấp đạt cả 5 miền · {result.seconds:.2f} s"
            self.logs.append_log(message)
            self._notify("success" if passed else "warning", "Đã quét xong", message)
        elif kind == "current":
            self.scanner.show_current(result)
        else:
            updated = result.adapter
            self.scanner.adapters = tuple(updated if a.guid == updated.guid else a for a in self.scanner.adapters)
            self.scanner.show_addresses()
            self.logs.reload()
            ok = result.store_check.https_ok
            self.scanner.status.setText("Truy cập được Store" if ok else "Store chưa kết nối")
            self.scanner.status_detail.setText("Đã kiểm tra Steam Store sau khi cập nhật DNS.")
            self.scanner.status_detail.setToolTip(result.store_check.error)
            message = "Steam Store kết nối thành công." if ok else result.store_check.error
            if result.ipv6_skipped:
                message += " Đã bỏ qua IPv6 vì giao thức đang tắt trên card."
            self._notify("success" if ok and not result.ipv6_skipped else "warning", "Đã cập nhật DNS", message)
        self.sync_controls()

    @Slot(str)
    def task_failed(self, message: str) -> None:
        kind = self.job_kind
        self.job = None
        self.logs.append_log("Lỗi · " + message)
        if self.quitting:
            QApplication.quit()
            return
        if kind == "inventory":
            self.scanner.adapters = ()
            self.scanner.populate_adapters()
        elif kind == "scan":
            self.scanner.clear_report()
        elif kind == "current":
            self.scanner.status.setText("Kiểm tra lỗi")
            self.scanner.status_detail.setText("Kiểm tra kết nối Steam chưa hoàn tất.")
            self.scanner.status_detail.setToolTip(message)
        else:
            self.logs.reload()
            self.refresh()
        self.sync_controls()
        self._notify("error", "Không hoàn tất thao tác", message.splitlines()[0][:180])

    @Slot()
    def elevate(self) -> None:
        if self.job is None:
            try:
                elevated = restart_elevated()
            except OSError as exc:
                self._notify("warning", "Chưa nâng quyền", str(exc))
            else:
                if elevated:
                    self.request_quit()
                else:
                    self._notify("warning", "Chưa nâng quyền", "Bạn có thể tiếp tục quét với quyền hiện tại.")

    @Slot()
    def request_quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.quitStarted.emit()
        self.tray_menu.close()
        self.tray.hide()
        self.hide()
        if isinstance(self.job, ScanTask):
            self.job.cancel()
            QApplication.quit()
        elif self.job is None:
            QApplication.quit()

    def finish_workers(self) -> None:
        # Qt can also leave its event loop through a system shutdown.
        if isinstance(self.job, ScanTask):
            self.job.cancel()
        self.pool.waitForDone()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.Type.WindowStateChange and self.isMinimized()
                and hasattr(self, "tray") and self.tray.isVisible()):
            QTimer.singleShot(0, self._hide_if_minimized)

    def _hide_if_minimized(self) -> None:
        if self.isMinimized() and self.tray.isVisible():
            self.hide()

    def closeEvent(self, event) -> None:
        if self.tray.isVisible() and not self.quitting:
            event.ignore()
            self.hide()
        else:
            event.accept()
            self.request_quit()
