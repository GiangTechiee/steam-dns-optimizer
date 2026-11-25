from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal, Slot
from PySide6.QtGui import QColor, QResizeEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QSizePolicy, QTableWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    ComboBox, FluentIcon as FIF, IconWidget, InfoBadge, InfoBar, InfoBarIcon, InfoBarPosition, PrimaryPushButton,
    ProgressBar, PushButton, ScrollArea, SimpleCardWidget, StrongBodyLabel, SubtitleLabel, SwitchButton, TitleLabel,
    ToolButton, TransparentToolButton,
)

from src.core.models import DnsProvider, DomainTestDetail, Grade, NetworkAdapterInfo, ProviderBenchmarkResult
from src.core.scanner import ScanDiagnosis, ScanReport
from src.services.windows_net import adapter_choices
from src.ui.components.common import (
    COMPACT_BELOW, AdapterComboBox, caption, cell, fit_label, make_table, mono_font, page_layout, text_pair, tip,
)
from src.ui.components.domain_dots_widget import DomainDotsWidget, domain_tooltip


class InlineWarning(InfoBar):
    """An in-layout Fluent warning whose height follows the actual text width."""

    def __init__(self, title: str, message: str, parent: QWidget):
        super().__init__(InfoBarIcon.WARNING, title, message, orient=Qt.Orientation.Vertical,
                         isClosable=False, duration=-1, position=InfoBarPosition.NONE, parent=parent)
        self.hBoxLayout.setContentsMargins(8, 4, 8, 4)
        self.textLayout.setContentsMargins(0, 4, 0, 4)
        self.textLayout.setSpacing(4)

    def _adjustText(self) -> None:
        # Floating InfoBars insert line breaks and call adjustSize() on parent resizes.
        for label, text in ((self.titleLabel, self.title), (self.contentLabel, self.content)):
            label.setWordWrap(True)
            if label.text() != text:
                label.setText(text)


class ScannerPage(ScrollArea):
    refreshRequested = Signal()
    elevateRequested = Signal()
    scanRequested = Signal()
    currentRequested = Signal()
    changeRequested = Signal(str, object)
    scopeChanged = Signal()

    def __init__(self, admin: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("dns-scanner")
        self.admin = admin
        self.adapters: tuple[NetworkAdapterInfo, ...] = ()
        self.report: ScanReport | None = None
        self.warning_bar: InfoBar | None = None
        self.apply_buttons: list[tuple[PushButton, ProviderBenchmarkResult]] = []
        self._compact: bool | None = None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = page_layout(body)
        self.setWidget(body)
        self.viewport().setAutoFillBackground(False)
        body.setAutoFillBackground(False)
        layout.addWidget(TitleLabel("Quét & Tối ưu DNS"))
        layout.addWidget(self._adapter_card())
        layout.addWidget(self._actions_card())
        self.warning_area = QVBoxLayout()
        self.warning_area.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.warning_area)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(SubtitleLabel("Xếp hạng DNS"))
        self.ranking_note = caption()
        header.addWidget(self.ranking_note)
        header.addStretch()
        self.transport = ComboBox()
        self.transport.addItems(["DNS qua IPv4", "DNS qua IPv6"])
        self.transport.setAccessibleName("Giao thức truy vấn máy chủ DNS")
        tip(self.transport, "Chọn địa chỉ IPv4 hoặc IPv6 của máy chủ DNS để đo.")
        header.addWidget(self.transport)
        layout.addLayout(header)
        self.table = make_table(("Nhà cung cấp", "Kết quả", "5 miền Steam", "Độ trễ TB", "Thao tác"))
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.table.setMinimumHeight(144)
        for column, width in ((1, 104), (2, 160), (3, 96), (4, 112)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.table, 1)
        self.empty = QWidget(self.table.viewport())
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(8, 8, 8, 8)
        empty_layout.setSpacing(4)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = IconWidget(FIF.SPEED_HIGH)
        icon.setFixedSize(28, 28)
        empty_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_title = StrongBodyLabel("Chưa có kết quả quét")
        empty_layout.addWidget(self.empty_title, 0, Qt.AlignmentFlag.AlignHCenter)
        self.empty_hint = caption("Quét các DNS đã bật để so sánh kết nối tới 5 miền Steam.")
        empty_layout.addWidget(self.empty_hint, 0, Qt.AlignmentFlag.AlignHCenter)
        overlay = QVBoxLayout(self.table.viewport())
        overlay.setContentsMargins(0, 0, 0, 0)
        overlay.addWidget(self.empty)
        self.combo.currentIndexChanged.connect(self._select_adapter)
        self.virtual.checkedChanged.connect(self.populate_adapters)
        self.transport.currentIndexChanged.connect(self.scopeChanged)
        self.adapt_to_width(self.window().width())

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "table"):
            self.adapt_to_width(self.window().width())

    def adapt_to_width(self, width: int) -> None:
        compact = width < COMPACT_BELOW
        if compact == self._compact:
            return
        self._compact = compact
        self.table.setColumnHidden(2, compact)
        for button, tool in ((self.restore_button, self.restore_tool), (self.dhcp_button, self.dhcp_tool)):
            outgoing, incoming = (button, tool) if compact else (tool, button)
            focused = outgoing.hasFocus()
            outgoing.hide()
            incoming.show()
            if focused:
                incoming.setFocus(Qt.FocusReason.OtherFocusReason)

    def _adapter_card(self) -> QWidget:
        card = SimpleCardWidget()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 8, 16, 8)
        box.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(8)
        icon = IconWidget(FIF.WIFI)
        icon.setFixedSize(18, 18)
        top.addWidget(icon)
        top.addWidget(StrongBodyLabel("Card mạng đang dùng"))
        self.combo = AdapterComboBox()
        self.combo.setMinimumWidth(96)
        self.combo.setMaximumWidth(320)
        self.combo.setPlaceholderText("Chọn card mạng")
        self.combo.setAccessibleName("Card mạng")
        top.addWidget(self.combo, 1)
        self.refresh_button = TransparentToolButton(FIF.SYNC)
        self.refresh_button.setAccessibleName("Làm mới card mạng")
        tip(self.refresh_button, "Làm mới card mạng và DNS hiện tại")
        self.refresh_button.clicked.connect(self.refreshRequested)
        top.addWidget(self.refresh_button)
        top.addStretch()
        self.virtual = SwitchButton()
        self.virtual.setOnText("Hiện card ảo")
        self.virtual.setOffText("Hiện card ảo")
        top.addWidget(self.virtual)
        box.addLayout(top)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.dns_label = caption("Đang đọc cấu hình DNS…", mono=True)
        self.dns_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.dns_label.setWordWrap(True)
        bottom.addWidget(self.dns_label, 1)
        badge = InfoBadge.success("Administrator") if self.admin else InfoBadge.warning("Quyền thường")
        tip(badge, "Có quyền đổi DNS" if self.admin else "Có thể quét và kiểm tra kết nối Steam")
        bottom.addWidget(badge)
        self.elevate = PushButton("Khởi động lại với quyền Admin")
        self.elevate.setVisible(not self.admin)
        self.elevate.clicked.connect(self.elevateRequested)
        bottom.addWidget(self.elevate)
        box.addLayout(bottom)
        return card

    def _actions_card(self) -> QWidget:
        card = SimpleCardWidget()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 8, 16, 8)
        box.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(12)
        status = QVBoxLayout()
        status.setSpacing(4)
        status.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.status = SubtitleLabel("Chưa kiểm tra")
        self.status_detail = caption("Kết nối Steam hiện tại")
        self.status_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status_detail.setWordWrap(True)
        status.addWidget(self.status)
        status.addWidget(self.status_detail)
        row.addLayout(status, 1)
        actions = QGridLayout()
        actions.setSpacing(8)
        self.scan_button = PrimaryPushButton(FIF.SEARCH, "Quét && Tìm DNS Tối Ưu")
        self.scan_button.setAccessibleName("Quét & Tìm DNS Tối Ưu")
        self.current_button = PushButton(FIF.PLAY, "Kiểm tra mạng hiện tại")
        self.restore_button = PushButton(FIF.HISTORY, "Khôi phục cũ")
        self.dhcp_button = PushButton(FIF.RETURN, "Về mặc định (DHCP)")
        self.restore_tool = ToolButton(FIF.HISTORY)
        self.dhcp_tool = ToolButton(FIF.RETURN)
        self.scan_button.clicked.connect(self.scanRequested)
        self.current_button.clicked.connect(self.currentRequested)
        actions.addWidget(self.scan_button, 0, 0)
        actions.addWidget(self.current_button, 1, 0)
        for i, (button, tool, action) in enumerate((
            (self.restore_button, self.restore_tool, "restore"), (self.dhcp_button, self.dhcp_tool, "dhcp"),
        )):
            tool.setFixedSize(36, 32)
            tool.setAccessibleName(button.text())
            tool.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            tip(tool, button.text())
            for control in (button, tool):
                control.clicked.connect(lambda checked=False, key=action: self.changeRequested.emit(key, None))
                actions.addWidget(control, i, 1)
        row.addLayout(actions)
        box.addLayout(row)
        self.progress = ProgressBar()
        self.progress.hide()
        box.addWidget(self.progress)
        return card

    @property
    def adapter(self) -> NetworkAdapterInfo | None:
        return next((a for a in self.adapters if a.guid == self.combo.currentData()), None)

    @property
    def ipv6(self) -> bool:
        return self.transport.currentIndex() == 1

    @property
    def best(self) -> DnsProvider | None:
        if self.report and self.report.results and self.report.results[0].grade == Grade.PASS:
            return self.report.results[0].provider
        return None

    def populate_adapters(self) -> None:
        previous = self.combo.currentData()
        primary = next((a for a in self.adapters if a.up and a.route_cost is not None), None)
        visible, selected = adapter_choices(self.adapters, self.virtual.isChecked(), previous)
        with QSignalBlocker(self.combo):
            self.combo.clear()
            for a in visible:
                suffix = " · Internet" if a == primary else ("" if a.up else " · ngắt kết nối")
                self.combo.addItem(a.name + suffix, userData=a.guid)
            self.combo.setCurrentIndex(self.combo.findData(selected) if selected else -1)
        self._select_adapter()

    def _select_adapter(self) -> None:
        self.show_addresses()
        self.status.setText("Chưa kiểm tra")
        self.status_detail.setText("Kết nối Steam hiện tại")
        self.status_detail.setToolTip("")
        self.scopeChanged.emit()

    def show_addresses(self) -> None:
        adapter = self.adapter
        if adapter:
            v4 = ", ".join(adapter.dns_v4) or "—"
            v6 = (", ".join(adapter.dns_v6) or "—") if adapter.ipv6_enabled else "Đã tắt"
            self.dns_label.setText(f"IPv4  {v4}\nIPv6  {v6}")
            self.dns_label.setToolTip(f"{adapter.description}\nIPv4: {v4}\nIPv6: {v6}")
        else:
            self.dns_label.setText("Chọn card mạng. Bật “Hiện card ảo” nếu tuyến Internet đi qua VPN.")
            self.dns_label.setToolTip("")

    def clear_report(self) -> None:
        self.report = None
        self.apply_buttons.clear()
        self.table.setRowCount(0)
        self.empty_title.setText("Chưa có kết quả quét")
        self.empty_hint.setText("Quét các DNS đã bật để so sánh kết nối tới 5 miền Steam.")
        self.empty.show()
        self.ranking_note.clear()
        self.progress.hide()
        if self.warning_bar:
            self.warning_area.removeWidget(self.warning_bar)
            self.warning_bar.hide()
            self.warning_bar.deleteLater()
            self.warning_bar = None

    @Slot(int, int)
    def set_progress(self, count: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(count)
        self.progress.show()
        self.ranking_note.setText(f"{count}/{total} nhà cung cấp")
        self.empty_title.setText("Đang quét DNS…")
        self.empty_hint.setText("Đang phân giải tên miền và kiểm tra HTTPS tới Steam.")

    def show_current(self, details: tuple[DomainTestDetail, ...]) -> None:
        good = sum(d.https_ok for d in details)
        self.status.setText("Kết nối tốt" if good == len(details) else ("Kết nối một phần" if good else "Kết nối lỗi"))
        self.status_detail.setText(f"{good}/{len(details)} miền truy cập được qua DNS Windows.")
        self.status_detail.setToolTip("\n".join(f"{d.domain} · {d.error or 'OK'}" for d in details))

    def show_report(self, report: ScanReport) -> None:
        self.clear_report()
        self.report = report
        self.empty.hide()
        self.ranking_note.setText(f"{len(report.results)} nhà cung cấp  ·  {report.seconds:.2f} s")
        if report.diagnosis == ScanDiagnosis.OFFLINE_OR_DNS_BLOCKED:
            self.warning_bar = InlineWarning("Không phân giải được tên miền Steam",
                "Mọi máy chủ DNS đều không trả về địa chỉ công cộng hợp lệ. Hãy kiểm tra đường mạng và kết nối tới máy chủ DNS.",
                self.warning_area.parentWidget())
        elif report.is_dpi_suspected:
            domains = ", ".join(report.suspected_sni)
            self.warning_bar = InlineWarning("Nghi chặn SNI/DPI", f"Ít nhất 3 DNS phân giải được {domains}, "
                "nhưng mọi kết nối HTTPS đều thất bại. Nếu xác nhận có DPI, đổi DNS riêng lẻ có thể chưa đủ. "
                "Các hướng xử lý gồm local reverse proxy hoặc phân mảnh gói tin.", self.warning_area.parentWidget())
        if self.warning_bar:
            self.warning_area.addWidget(self.warning_bar)
        self.table.setRowCount(len(report.results))
        for row, item in enumerate(report.results):
            self._result_row(row, item)

    def _result_row(self, row: int, item: ProviderBenchmarkResult) -> None:
        provider = item.provider
        first = row == 0 and item.grade == Grade.PASS
        name = fit_label(StrongBodyLabel(provider.name))
        heading = QWidget()
        line = QHBoxLayout(heading)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        line.addWidget(name, 1)
        if first:
            badge = InfoBadge.custom("Tối ưu #1", QColor("#235da9"), QColor("#245c9c"))
            tip(badge, "Khuyên dùng · Độ trễ trung bình thấp nhất trong nhóm đạt 5/5")
            line.addWidget(badge)
        addresses = provider.ipv6 if self.ipv6 else provider.ipv4
        sub = fit_label(caption("  /  ".join(addresses), mono=True))
        self.table.setCellWidget(row, 0, cell(text_pair(heading, sub), True))
        if item.grade == Grade.PASS:
            state = InfoBadge.success("ĐẠT 5/5")
        elif item.grade == Grade.PARTIAL:
            state = InfoBadge.attension("MỘT PHẦN")
        elif not any(d.dns_ok for d in item.details):
            state = InfoBadge.error("LỖI DNS")
        else:
            state = InfoBadge.error("BỊ CHẶN")
        summary = f"{item.passed}/5 miền thành công"
        details = "\n\n".join([summary, *(domain_tooltip(detail) for detail in item.details)])
        result_cell = cell(state)
        state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        tip(result_cell, details)
        result_item = QTableWidgetItem()
        result_item.setToolTip(details)
        result_item.setWhatsThis(details)
        result_item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{state.text()}. {details}")
        self.table.setItem(row, 1, result_item)
        self.table.setCellWidget(row, 1, result_cell)
        self.table.setCellWidget(row, 2, cell(DomainDotsWidget(item.details), True))
        latency = StrongBodyLabel("—" if item.total_ms is None else f"{item.total_ms:.0f} ms")
        latency.setFont(mono_font(10))
        units = caption("DNS + TLS")
        for label in (latency, units):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setCellWidget(row, 3, cell(text_pair(latency, units), True))
        apply = PrimaryPushButton("Áp dụng") if first else PushButton("Áp dụng")
        apply.clicked.connect(lambda checked=False, p=provider: self.changeRequested.emit("apply", p))
        self.apply_buttons.append((apply, item))
        self.table.setCellWidget(row, 4, cell(apply))

    def set_controls(self, busy: bool, enabled_count: int, has_backup: bool) -> None:
        adapter = self.adapter
        online = bool(adapter and adapter.up and (adapter.source_v4 or adapter.source_v6))
        route = bool(adapter and (adapter.source_v6 if self.ipv6 else adapter.source_v4))
        writable = self.admin and adapter is not None and not busy
        self.scan_button.setEnabled(not busy and online and route and enabled_count > 0)
        self.scan_button.setToolTip(f"Quét {enabled_count} nhà cung cấp" if route else "Card mạng thiếu địa chỉ cho giao thức đã chọn.")
        self.current_button.setEnabled(not busy and online)
        for button, tool, available, help_text in (
            (self.restore_button, self.restore_tool, has_backup,
             "Khôi phục DNS gốc đã sao lưu" if has_backup else "Chưa có bản sao lưu cho card này"),
            (self.dhcp_button, self.dhcp_tool, True, "Đặt DNS tự động cho card đang chọn"),
        ):
            if busy:
                help_text = "Đang thực hiện tác vụ khác"
            elif not self.admin:
                help_text = "Cần quyền Admin để đổi DNS"
            elif adapter is None:
                help_text = "Chọn card mạng để đổi DNS"
            for control in (button, tool):
                control.setEnabled(writable and available)
                control.setToolTip(f"{button.text()}\n{help_text}")
                control.setAccessibleDescription(help_text)
        for widget in (self.refresh_button, self.combo, self.virtual, self.transport, self.elevate):
            widget.setEnabled(not busy)
        if self.report is None and not busy:
            self.ranking_note.setText(f"{enabled_count} nhà cung cấp đã bật")
        for button, item in self.apply_buttons:
            used = bool(adapter and adapter.uses(item.provider))
            button.setText("Đang dùng" if used else "Áp dụng")
            button.setEnabled(writable and not used and item.grade != Grade.FAIL)
            button.setToolTip("Cần quyền Admin để đổi DNS" if not self.admin else item.provider.name)
