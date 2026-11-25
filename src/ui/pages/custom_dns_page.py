from __future__ import annotations

from functools import partial

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QHeaderView, QWidget
from qfluentwidgets import (
    BodyLabel, FluentIcon as FIF, InfoBar, InfoBarPosition, LineEdit, MessageBoxBase,
    PrimaryPushButton, StrongBodyLabel, SubtitleLabel, SwitchButton, TitleLabel, TransparentToolButton,
)

from src.core.models import DnsProvider
from src.core.providers import ProviderStore
from src.ui.components.common import caption, cell, confirm, fit_label, make_table, page_layout, text_pair, tip


class AddDnsDialog(MessageBoxBase):
    def __init__(self, names: set[str], parent: QWidget):
        super().__init__(parent)
        self.names = names
        self.viewLayout.addWidget(SubtitleLabel("Thêm DNS tùy chỉnh"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(4)
        self.fields: list[LineEdit] = []
        fields = (("Tên gợi nhớ", "Ví dụ: DNS nhà riêng", 0, 0, 2),
                  ("IPv4 chính", "1.1.1.1", 2, 0, 1), ("IPv4 phụ (tùy chọn)", "1.0.0.1", 2, 1, 1),
                  ("IPv6 chính (tùy chọn)", "2606:4700:4700::1111", 4, 0, 1),
                  ("IPv6 phụ (tùy chọn)", "2606:4700:4700::1001", 4, 1, 1))
        for label, placeholder, row, col, span in fields:
            edit = LineEdit()
            edit.setMaxLength(64)
            edit.setPlaceholderText(placeholder)
            edit.setAccessibleName(label)
            grid.addWidget(BodyLabel(label), row, col, 1, span)
            grid.addWidget(edit, row + 1, col, 1, span)
            self.fields.append(edit)
        self.viewLayout.addLayout(grid)
        self.validation = caption()
        self.validation.setWordWrap(True)
        self.viewLayout.addWidget(self.validation)
        self.yesButton.setText("Lưu DNS")
        self.cancelButton.setText("Hủy")
        self.widget.setMinimumWidth(460)
        for edit in self.fields:
            edit.textChanged.connect(self.validate)
        self.validate()
        self.fields[0].setFocus()

    def values(self) -> tuple[str, ...]:
        return tuple(edit.text().strip() for edit in self.fields)

    def validate(self) -> bool:
        values = self.values()
        message = ""
        if not values[0] or not values[1]:
            message = "Nhập tên gợi nhớ và địa chỉ IPv4 chính."
        elif values[0].casefold() in self.names:
            message = "Tên DNS này đã có trong danh sách."
        else:
            try:
                DnsProvider("preview", *values)
            except ValueError as exc:
                message = str(exc)
        self.validation.setText(message or "Địa chỉ hợp lệ.")
        self.yesButton.setEnabled(not message)
        return not message


class CustomDnsPage(QWidget):
    changed = Signal()

    def __init__(self, store: ProviderStore, parent=None):
        super().__init__(parent)
        self.setObjectName("custom-dns")
        self.store = store
        self.providers: tuple[DnsProvider, ...] = ()
        self.disabled: set[str] = set()
        layout = page_layout(self)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(TitleLabel("Danh sách DNS"))
        header.addStretch()
        self.add_button = PrimaryPushButton(FIF.ADD, "Thêm DNS tùy chỉnh")
        header.addWidget(self.add_button)
        layout.addLayout(header)
        self.summary = caption()
        layout.addWidget(self.summary)
        self.table = make_table(("Nhà cung cấp", "Máy chủ IPv4", "Máy chủ IPv6", "Quét", ""))
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 157), (3, 66), (4, 52)):
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)
        self.add_button.clicked.connect(self.add_provider)

    def _error(self, exc: Exception) -> None:
        InfoBar.error("Danh sách DNS", str(exc), duration=5500, position=InfoBarPosition.TOP_RIGHT, parent=self.window())

    def _summary(self) -> None:
        count = sum(p.key not in self.disabled for p in self.providers)
        self.summary.setText(f"{count}/{len(self.providers)} nhà cung cấp đang bật để quét.")

    def reload(self) -> None:
        try:
            self.providers, self.disabled = self.store.load()
        except (ValueError, OSError, RuntimeError) as exc:
            self._error(exc)
            return
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.providers))
        for row, provider in enumerate(self.providers):
            name = fit_label(StrongBodyLabel(provider.name))
            self.table.setCellWidget(row, 0, cell(text_pair(name, caption("Tùy chỉnh" if provider.is_custom else "Tích hợp")), True))
            for column, addresses in ((1, provider.ipv4), (2, provider.ipv6)):
                first = fit_label(caption(addresses[0] if addresses else "—", mono=True))
                box = first
                if len(addresses) > 1:
                    box = text_pair(first, fit_label(caption(addresses[1], mono=True)))
                self.table.setCellWidget(row, column, cell(box, True))
            toggle = SwitchButton()
            toggle.setOnText("")
            toggle.setOffText("")
            toggle.setAccessibleName("Quét " + provider.name)
            toggle.setChecked(provider.key not in self.disabled)
            toggle.checkedChanged.connect(partial(self.set_enabled, provider.key, toggle))
            self.table.setCellWidget(row, 3, cell(toggle))
            if provider.is_custom:
                delete = TransparentToolButton(FIF.DELETE)
                tip(delete, "Xóa " + provider.name)
                delete.setAccessibleName("Xóa " + provider.name)
                delete.clicked.connect(partial(self.remove_provider, provider))
                self.table.setCellWidget(row, 4, cell(delete))
        self._summary()
        self.changed.emit()

    def set_enabled(self, key: str, toggle: SwitchButton, enabled: bool) -> None:
        try:
            self.store.set_enabled(key, enabled)
        except (ValueError, OSError, RuntimeError) as exc:
            with QSignalBlocker(toggle):
                toggle.setChecked(not enabled)
            self._error(exc)
            return
        if enabled:
            self.disabled.discard(key)
        else:
            self.disabled.add(key)
        self._summary()
        self.changed.emit()

    def add_provider(self) -> None:
        dialog = AddDnsDialog({p.name.casefold() for p in self.providers}, self.window())
        if dialog.exec():
            try:
                self.store.add(*dialog.values())
            except (ValueError, OSError, RuntimeError) as exc:
                self._error(exc)
                return
            self.reload()

    def remove_provider(self, provider: DnsProvider) -> None:
        if confirm(self, "Xóa DNS tùy chỉnh?", provider.name, "Xóa DNS"):
            try:
                self.store.remove(provider.key)
            except (ValueError, OSError, RuntimeError) as exc:
                self._error(exc)
                return
            self.reload()
