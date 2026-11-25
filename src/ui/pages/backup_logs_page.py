from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSignalBlocker, Signal, Slot
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF, InfoBar, InfoBarPosition, PlainTextEdit, PushButton,
    SimpleCardWidget, StrongBodyLabel, SubtitleLabel, TitleLabel, TransparentToolButton,
)

from src.core.models import DnsBackupRecord
from src.services.windows_net import WindowsNetwork
from src.ui.components.common import AdapterComboBox, caption, confirm, mono_font, page_layout, tip


class BackupLogsPage(QWidget):
    changed = Signal()

    def __init__(self, network: WindowsNetwork, parent=None):
        super().__init__(parent)
        self.setObjectName("backup-logs")
        self.network = network
        self.records: dict[str, DnsBackupRecord] = {}
        layout = page_layout(self)
        layout.addWidget(TitleLabel("Sao lưu & Nhật ký"))
        card = SimpleCardWidget()
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(StrongBodyLabel("Cấu hình DNS gốc"))
        header.addStretch()
        self.adapters = AdapterComboBox()
        self.adapters.setMinimumWidth(128)
        self.adapters.setMaximumWidth(320)
        self.adapters.setPlaceholderText("Chưa có bản sao lưu")
        self.adapters.setAccessibleName("Card mạng đã sao lưu")
        header.addWidget(self.adapters, 1)
        refresh = TransparentToolButton(FIF.SYNC)
        tip(refresh, "Đọc lại các bản sao lưu")
        refresh.setAccessibleName("Làm mới bản sao lưu")
        header.addWidget(refresh)
        self.delete_button = PushButton(FIF.DELETE, "Xóa bản sao")
        header.addWidget(self.delete_button)
        box.addLayout(header)
        self.detail = caption("", mono=True)
        self.detail.setWordWrap(True)
        self.detail.setToolTip(str(network.path))
        box.addWidget(self.detail)
        layout.addWidget(card)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(SubtitleLabel("Nhật ký kỹ thuật"))
        tools.addStretch()
        copy = PushButton(FIF.COPY, "Sao chép Log")
        clear = PushButton(FIF.DELETE, "Xóa Log")
        tools.addWidget(copy)
        tools.addWidget(clear)
        layout.addLayout(tools)
        self.log_view = PlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(mono_font())
        self.log_view.document().setMaximumBlockCount(4500)
        self.log_view.setPlaceholderText("Nhật ký DNS, HTTPS và PowerShell xuất hiện tại đây.")
        layout.addWidget(self.log_view, 1)
        refresh.clicked.connect(self.reload)
        self.delete_button.clicked.connect(self.delete_selected)
        self.adapters.currentIndexChanged.connect(self.show_record)
        clear.clicked.connect(self.log_view.clear)
        copy.clicked.connect(self.copy_log)

    def reload(self) -> None:
        try:
            self.records = self.network.backups()
        except (ValueError, OSError, RuntimeError) as exc:
            self.records = {}
            InfoBar.error("Bản sao lưu", str(exc), duration=5500, position=InfoBarPosition.TOP_RIGHT, parent=self.window())
        previous = self.adapters.currentData()
        with QSignalBlocker(self.adapters):
            self.adapters.clear()
            for record in sorted(self.records.values(), key=lambda r: r.name.casefold()):
                self.adapters.addItem(record.name, userData=record.guid)
            index = self.adapters.findData(previous)
            self.adapters.setCurrentIndex(max(0, index) if self.records else -1)
        self.show_record()
        self.changed.emit()

    def show_record(self) -> None:
        record = self.records.get(self.adapters.currentData())
        self.delete_button.setEnabled(record is not None)
        if record:
            self.detail.setText(f"{record.saved_at}   ·   {record.guid}\n"
                                f"IPv4  {record.ipv4.description}\nIPv6  {record.ipv6.description}")
        else:
            self.detail.setText("Ứng dụng sẽ lưu DNS gốc của từng card trước lần thay đổi đầu tiên.")

    def delete_selected(self) -> None:
        record = self.records.get(self.adapters.currentData())
        if record and confirm(self, "Xóa bản sao cấu hình gốc?", f"Card {record.name}\n"
                              "Sau khi xóa, bạn sẽ mất cấu hình đã lưu ở lần thay đổi đầu tiên.", "Xóa bản sao"):
            try:
                self.network.delete_backup(record.guid)
            except (ValueError, OSError, RuntimeError) as exc:
                InfoBar.error("Xóa bản sao", str(exc), duration=5500, position=InfoBarPosition.TOP_RIGHT, parent=self.window())
            self.reload()

    @Slot(str)
    def append_log(self, line: str) -> None:
        self.log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {line}")

    def copy_log(self) -> None:
        QApplication.clipboard().setText(self.log_view.toPlainText())
        InfoBar.success("Đã sao chép nhật ký", "", duration=1800, position=InfoBarPosition.TOP_RIGHT, parent=self.window())
