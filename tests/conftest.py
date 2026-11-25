import os
import socket
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import dns.resolver
import pytest

from src.core.models import DOMAINS, DomainTestDetail, FamilyDns, NetworkAdapterInfo
from src.services import windows_net


@pytest.fixture(autouse=True)
def isolated_machine(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected real network or Windows configuration call in an offline test")

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(windows_net, "WRITE_MUTEX_NAME", "Local\\SteamDNSOptimizer.QA." + uuid4().hex)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    for method in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, method, forbidden)
    monkeypatch.setattr(dns.resolver.Resolver, "resolve", forbidden)
    monkeypatch.setattr(windows_net, "subprocess", SimpleNamespace(
        run=forbidden, list2cmdline=subprocess.list2cmdline, CREATE_NO_WINDOW=subprocess.CREATE_NO_WINDOW))
    monkeypatch.setattr(windows_net.ctypes.windll.shell32, "ShellExecuteW", Mock(side_effect=forbidden))


@pytest.fixture
def adapter():
    return NetworkAdapterInfo(
        if_index=12, guid="6a55af90-c01b-4328-8ca1-bd15be607a77", name="Wi-Fi 'Phòng Khách' & Bếp",
        description="Ethernet", up=True, is_virtual=False, route_cost=25, dns_v4=("10.0.0.2",),
        dns_v6=("fd00::53",), settings_v4=FamilyDns(("10.0.0.2",)), settings_v6=FamilyDns(),
        ipv6_enabled=True, source_v4="192.168.1.7", source_v6="fd00::7",
    )


@pytest.fixture(scope="session")
def qt_app():
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["qa", "-platform", os.environ.get("QT_QPA_PLATFORM", "offscreen")])
    if app.platformName() == "offscreen" and not QFontDatabase.families():
        # The Windows offscreen plugin otherwise measures even ASCII with missing-glyph boxes.
        fonts = Path(os.environ["WINDIR"]) / "Fonts"
        for filename in ("segoeui.ttf", "seguisb.ttf", "consola.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
        app.setFont(QFont("Segoe UI", 9))
    return app


@pytest.fixture
def network_machine(adapter, tmp_path, monkeypatch):
    state = SimpleNamespace(adapter=adapter, fail_next_ipv6=False)
    service = windows_net.WindowsNetwork(tmp_path)

    def find(guid, log):
        if guid != state.adapter.guid:
            raise ValueError("Adapter missing")
        return state.adapter

    def write(before, v4, v6, log):
        state.adapter = replace(state.adapter, settings_v4=v4, dns_v4=v4.static or ("10.0.0.8",))
        if state.fail_next_ipv6:
            state.fail_next_ipv6 = False
            raise OSError("IPv6 write failed")
        if v6 is not None and state.adapter.ipv6_enabled:
            state.adapter = replace(state.adapter, settings_v6=v6, dns_v6=v6.static or ("fd00::53",))
        return v6 is not None and not state.adapter.ipv6_enabled

    state.writer = Mock(side_effect=write)
    state.check = Mock(return_value=(DomainTestDetail(DOMAINS[0], True, True, 1, 2, "8.8.8.8"),))
    monkeypatch.setattr(windows_net, "is_admin", Mock(return_value=True))
    monkeypatch.setattr(windows_net, "_find_adapter", find)
    monkeypatch.setattr(windows_net, "_write_settings", state.writer)
    monkeypatch.setattr(windows_net, "check_current", state.check)
    state.service = service
    return state
