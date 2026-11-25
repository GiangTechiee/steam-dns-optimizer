from dataclasses import replace
from unittest.mock import Mock, call

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QFontMetrics
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QHeaderView, QStyle, QStyleOptionButton, QSystemTrayIcon
from qfluentwidgets import NavigationDisplayMode, StrongBodyLabel, ToolButton, ToolTip, ToolTipFilter

from src.core.models import DOMAINS, DnsBackupRecord, DomainTestDetail, ProviderBenchmarkResult
from src.core.providers import BUILTINS
from src.core.scanner import ScanReport
from src.ui import main_window


def settle_layout():
    QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)


@pytest.fixture
def window(qt_app, monkeypatch, adapter):
    monkeypatch.setattr(main_window, "is_admin", lambda: True)
    monkeypatch.setattr(main_window.MainWindow, "refresh", lambda self: None)
    monkeypatch.setattr(main_window.MainWindow, "request_change", Mock())
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))
    win = main_window.MainWindow()
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    win.show()
    win.scanner.adapters = (adapter,)
    win.scanner.populate_adapters()
    settle_layout()
    yield win
    win.hide()
    win.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def report():
    good = tuple(DomainTestDetail(d, True, True, 3, 12, "1.1.1.1") for d in DOMAINS)
    mixed = (*good[:3],
             DomainTestDetail(DOMAINS[3], True, False, 3, 40, "8.8.8.8", "Chứng chỉ TLS không hợp lệ"),
             DomainTestDetail(DOMAINS[4], False, False, 18, error="NXDOMAIN"))
    slow = tuple(replace(d, tls_ms=1231) for d in good)
    down = tuple(DomainTestDetail(d, False, False, 18, error="NXDOMAIN") for d in DOMAINS)
    first = replace(BUILTINS[0], name="Nhà cung cấp DNS cho đường truyền dự phòng tại Việt Nam")
    return ScanReport(tuple(ProviderBenchmarkResult(p, ds) for p, ds in
                            zip((first, *BUILTINS[1:4]), (good, mixed, slow, down))), (), 1.23)


@pytest.mark.parametrize(("width", "height"), [
    (640, 520), (779, 520), (780, 520), (980, 690), (1179, 720), (1180, 720), (3840, 2160),
])
def test_window_breakpoints_and_table_geometry(window, width, height):
    window.resize(width, height)
    settle_layout()
    page = window.scanner
    table = page.table
    assert window.minimumSize().toTuple() == (640, 520)
    assert window.size().toTuple() == (width, height)
    compact = width < 780
    assert [i for i in range(table.columnCount()) if not table.isColumnHidden(i)] == (
        [0, 1, 3, 4] if compact else list(range(5)))
    assert page.restore_tool.isVisible() == page.dhcp_tool.isVisible() == compact
    assert page.restore_button.isVisible() == page.dhcp_button.isVisible() == (not compact)
    if compact:
        assert window.navigationInterface.panel.displayMode == NavigationDisplayMode.COMPACT
        assert window.navigationInterface.width() == 48
    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert all(header.sectionResizeMode(i) == QHeaderView.ResizeMode.Fixed for i in range(1, 5))
    assert table.columnWidth(3) == 96
    assert table.horizontalScrollBar().maximum() == page.horizontalScrollBar().maximum() == 0
    content = table.parentWidget()
    viewport = page.viewport()
    assert content.width() == min(viewport.width(), 1140)
    assert abs(2 * content.mapTo(viewport, QPoint()).x() + content.width() - viewport.width()) <= 1
    assert page.empty.isVisible()


def test_narrowing_during_navigation_animation_closes_the_panel(window):
    window.resize(1280, 720)
    settle_layout()
    nav = window.navigationInterface
    nav.expand(useAni=True)
    finished = QSignalSpy(nav.panel.expandAni.finished)
    window.resize(779, 520)
    if nav.panel.displayMode != NavigationDisplayMode.COMPACT:
        assert finished.wait(1500)
    settle_layout()
    assert nav.panel.displayMode == NavigationDisplayMode.COMPACT
    assert nav.width() == 48
    assert not nav.panel.menuButton.isVisible()
    window.resize(780, 520)
    settle_layout()
    assert nav.panel.menuButton.isVisible()


def test_secondary_actions_keep_permissions_and_emit_once(window):
    page = window.scanner
    restore, dhcp = page.restore_tool, page.dhcp_tool
    for tool in (restore, dhcp):
        assert isinstance(tool, ToolButton) and not tool.text() and not tool.icon().isNull()
        assert tool.accessibleName() and tool.findChild(ToolTipFilter)
    for width, controls in ((640, (restore, dhcp)), (780, (page.restore_button, page.dhcp_button)),
                            (779, (restore, dhcp))):
        window.resize(width, 520)
        settle_layout()
        page.set_controls(busy=False, enabled_count=12, has_backup=True)
        for control in controls:
            assert control.isVisible() and control.isEnabled()
            control.click()
        assert window.request_change.call_args_list == [call("restore", None), call("dhcp", None)]
        window.request_change.reset_mock()
        page.set_controls(busy=True, enabled_count=12, has_backup=True)
        for control in controls:
            assert not control.isEnabled() and "tác vụ" in control.toolTip()
            control.click()
        window.request_change.assert_not_called()
    page.set_controls(busy=False, enabled_count=12, has_backup=False)
    assert not restore.isEnabled() and dhcp.isEnabled() and "bản sao lưu" in restore.toolTip()
    page.admin = False
    page.set_controls(busy=False, enabled_count=12, has_backup=True)
    assert all(not control.isEnabled() and "Admin" in control.toolTip() for control in (restore, dhcp))


def assert_combo_display(combo, full_text):
    option = QStyleOptionButton()
    combo.initStyleOption(option)
    rect = combo.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, combo)
    expected = QFontMetrics(combo.font(), combo).elidedText(full_text, Qt.TextElideMode.ElideRight, rect.width())
    assert option.text == expected.replace("&", "&&")
    assert combo.currentText() == combo.itemText(combo.currentIndex()) == combo.toolTip() == full_text
    return expected


def test_hover_shows_full_domain_results_and_disabled_action_help(window, report):
    page = window.scanner
    window.resize(640, 520)
    page.show_report(report)
    page.set_controls(busy=False, enabled_count=12, has_backup=False)
    settle_layout()
    for widget, expected in ((page.table.cellWidget(1, 1), DOMAINS),
                             (page.restore_tool, ("Khôi phục cũ", "Chưa có bản sao lưu"))):
        tooltip_filter = widget.findChild(ToolTipFilter)
        ready = QSignalSpy(tooltip_filter.timer.timeout)
        pos = QPointF(widget.rect().center())
        QApplication.sendEvent(widget, QEnterEvent(pos, pos, widget.mapToGlobal(pos)))
        assert ready.wait(1500)
        assert any(bubble.isVisible() and all(text in bubble.text() for text in expected)
                   for bubble in window.findChildren(ToolTip))
        QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))


def test_resizing_preserves_text_selection_results_and_latency_alignment(window, report, adapter):
    page = window.scanner
    page.show_report(report)
    scope = QSignalSpy(page.scopeChanged)
    name = page.table.cellWidget(0, 0).findChild(StrongBodyLabel)
    full = report.results[0].provider.name
    summary = page.table.cellWidget(1, 1)
    for width in (640, 1180, 779):
        window.resize(width, 690)
        settle_layout()
        assert page.adapter == adapter and page.combo.currentData() == adapter.guid
        displayed = assert_combo_display(page.combo, adapter.name + " · Internet")
        assert displayed.endswith("…") == (width == 640)
        expected = QFontMetrics(name.font(), name).elidedText(full, Qt.TextElideMode.ElideRight, name.width())
        assert name.text() == expected
        assert name.toolTip() == name.accessibleName() == full
        assert (name.text() == full) == (width == 1180)
        assert page.report is report and scope.count() == 0
        details = summary.toolTip()
        assert page.table.item(1, 1).toolTip() == details and summary.findChild(ToolTipFilter)
        for domain in DOMAINS:
            assert domain in details
        for field in ("3/5", "8.8.8.8", "HTTPS: Lỗi", "HTTPS: Chưa đo", "NXDOMAIN", "Chứng chỉ TLS"):
            assert field in details
        right_edges = []
        for i, expected_value in ((0, "15 ms"), (2, "1234 ms"), (3, "—")):
            cell = page.table.cellWidget(i, 3)
            latency = cell.findChild(StrongBodyLabel)
            assert latency.text() == expected_value
            assert latency.font().families()[:2] == ["Cascadia Mono", "Consolas"]
            assert latency.alignment() == Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            right_edges.append(latency.mapTo(cell, latency.rect().topRight()).x())
        assert right_edges == [page.table.columnWidth(3) - 9] * 3


@pytest.mark.parametrize("offline", [True, False], ids=["offline", "dpi"])
def test_warning_wraps_and_ranking_remains_reachable_at_minimum_size(window, offline):
    page = window.scanner
    window.resize(640, 520)
    ds = tuple(DomainTestDetail(d, not offline, False, 3, error="Hết thời gian chờ") for d in DOMAINS)
    page.show_report(ScanReport(tuple(ProviderBenchmarkResult(p, ds) for p in BUILTINS),
                                () if offline else DOMAINS[:2], 2.4))
    settle_layout()
    bar = page.warning_bar
    assert bar.isVisible() and bar.contentLabel.wordWrap()
    assert bar.width() == page.table.width()
    assert bar.geometry().bottom() < page.ranking_note.geometry().top() < page.table.geometry().top()
    assert not page.horizontalScrollBar().maximum()
    scroll = page.verticalScrollBar()
    scroll.setValue(scroll.maximum())
    assert page.viewport().rect().contains(page.table.mapTo(page.viewport(), page.table.rect().center()))
    page.clear_report()
    page.set_progress(4, 12)
    window.resize(780, 520)
    settle_layout()
    assert page.warning_bar is None and page.progress.isVisible() and page.progress.value() == 4
    assert page.empty_title.text() == "Đang quét DNS…" and not page.table.isColumnHidden(2)


def test_custom_provider_and_backup_adapter_names_keep_full_text(window, adapter, report, monkeypatch):
    provider = report.results[0].provider
    custom = window.custom
    monkeypatch.setattr(custom.store, "load", lambda: ((provider,), set()))
    custom.reload()
    window.resize(640, 520)
    window.switchTo(custom)
    settle_layout()
    assert custom.isVisible()
    name = custom.table.cellWidget(0, 0).findChild(StrongBodyLabel)
    assert name.text().endswith("…") and name.toolTip() == provider.name
    logs = window.logs
    record = DnsBackupRecord(adapter.guid, adapter.if_index, adapter.name * 4, "2026-11-25",
                             adapter.settings_v4, adapter.settings_v6)
    monkeypatch.setattr(logs.network, "backups", lambda: {record.guid: record})
    logs.reload()
    window.switchTo(logs)
    settle_layout()
    assert logs.isVisible()
    assert assert_combo_display(logs.adapters, record.name).endswith("…")
    assert logs.adapters.currentData() == record.guid
    assert logs.delete_button.mapTo(logs, logs.delete_button.rect().topRight()).x() < logs.width()
