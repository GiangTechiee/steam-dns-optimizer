import base64
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.core.models import FamilyDns
from src.services import windows_net as net


def stdout(value):
    return subprocess.CompletedProcess([], 0, stdout=value, stderr="")


@pytest.mark.parametrize("output,expected", [
    ("", []), ("\r\n", []), ("\ufeff \r\n", []), ("null", []), ("[]", []),
    ('{"Name":"Ethernet","ifIndex":12}', [{"Name": "Ethernet", "ifIndex": 12}]),
    ('[{"ifIndex":12},{"ifIndex":15}]', [{"ifIndex": 12}, {"ifIndex": 15}]),
])
def test_convert_to_json_shapes(monkeypatch, output, expected):
    run = Mock(return_value=stdout(output))
    monkeypatch.setattr(net.subprocess, "run", run)
    assert net.powershell("Get-NetAdapter | ConvertTo-Json", Mock()) == expected
    assert run.call_args.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("output", ["{bad", "17", '["not a row"]'])
def test_invalid_json_rows_are_not_accepted_as_acknowledgement(monkeypatch, output):
    monkeypatch.setattr(net.subprocess, "run", Mock(return_value=stdout(output)))
    with pytest.raises(ValueError):
        net.powershell("test", Mock())


def inventory_row(adapter, **fields):
    return dict(if_index=adapter.if_index, guid=adapter.guid, name=adapter.name, description="NIC", up=True,
                virtual=False, route_cost=25, dns_v4=list(adapter.dns_v4), dns_v6=list(adapter.dns_v6),
                static_v4=list(adapter.settings_v4.static), static_v6=[], ipv6_enabled=True,
                source_v4=adapter.source_v4, source_v6=adapter.source_v6) | fields


def decoded_command(run):
    return base64.b64decode(run.call_args.args[0][-1]).decode("utf-16le")


def test_unicode_adapter_is_read_verbatim_and_never_interpolated(adapter, monkeypatch):
    run = Mock(return_value=stdout(json.dumps(inventory_row(adapter), ensure_ascii=False)))
    monkeypatch.setattr(net.subprocess, "run", run)
    single, = net.list_adapters(Mock())
    assert single.name == "Wi-Fi 'Phòng Khách' & Bếp"
    assert single.settings_v4.static == ("10.0.0.2",)
    run.return_value = stdout('{"skipped":false}')
    assert not net._write_settings(replace(single, if_index="0012"), FamilyDns(("8.8.8.8",)), FamilyDns(), Mock())
    command = decoded_command(run)
    assert command.startswith("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;")
    assert "$ix = 12\n" in command and single.name not in command
    assert "-InterfaceIndex $ix" in command and single.guid in command
    assert all(c.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW and c.kwargs["encoding"] == "utf-8"
               for c in run.call_args_list)
    run.reset_mock()
    with pytest.raises(ValueError):
        net._write_settings(replace(single, if_index="12x"), FamilyDns(), None, Mock())
    run.assert_not_called()


def test_invalid_write_ack_cannot_report_success(adapter, monkeypatch):
    monkeypatch.setattr(net.subprocess, "run", Mock(return_value=stdout("")))
    with pytest.raises(RuntimeError, match="xác nhận"):
        net._write_settings(adapter, FamilyDns(), None, Mock())


@pytest.mark.parametrize("warp", ["CloudflareWARP", "Cloudflare WARP"])
def test_hidden_vpn_route_selects_physical_guid(adapter, monkeypatch, qt_app, warp):
    from src.ui.pages.scanner_page import ScannerPage

    vpn = "32ce855a-7f3a-4df2-9e9e-74461aa67041"
    wsl = "6972772a-8c97-469f-8234-710bc8d88e67"
    rows = [inventory_row(adapter, name="Ethernet"), inventory_row(adapter, if_index=91, guid=wsl,
            name="vEthernet (WSL)", route_cost=6), inventory_row(adapter, if_index=67, guid=vpn, name=warp, route_cost=1)]
    monkeypatch.setattr(net.subprocess, "run", Mock(return_value=stdout(json.dumps(rows))))
    adapters = net.list_adapters(Mock())
    assert [a.guid for a in adapters] == [vpn, wsl, adapter.guid]
    assert [a.is_virtual for a in adapters] == [True, True, False]
    assert net.adapter_choices(adapters, True, None)[1] == vpn
    assert net.adapter_choices(adapters, True, wsl)[1] == wsl
    page = ScannerPage(False)
    page.adapters = adapters
    page.populate_adapters()
    assert page.combo.count() == 1 and page.adapter.if_index == adapter.if_index
    page.virtual.setChecked(True)
    page.combo.setCurrentIndex(page.combo.findData(vpn))
    assert page.adapter.if_index == 67
    page.virtual.setChecked(False)
    assert page.adapter.guid == adapter.guid and page.combo.currentIndex() == 0
    page.deleteLater()


@pytest.mark.parametrize("code,succeeded", [(0, False), (5, False), (31, False), (32, False), (33, True)])
def test_uac_returns_bool_and_only_quits_after_success(code, succeeded, monkeypatch, qt_app):
    from src.ui.main_window import MainWindow

    execute = Mock(return_value=code)
    monkeypatch.setattr(net.ctypes.windll.shell32, "ShellExecuteW", execute)
    fake_window = SimpleNamespace(job=None, _notify=Mock(), request_quit=Mock())
    with patch.object(sys, "exit", side_effect=AssertionError("Unexpected exit")):
        assert net.restart_elevated() is succeeded
        MainWindow.elevate(fake_window)
    assert execute.call_args.args[1] == "runas"
    assert fake_window.request_quit.call_count == int(succeeded)


@pytest.mark.parametrize("initial_binding,drop_at_write,expected", [
    (True, True, ["IPv4", "IPv6", "flush"]), (False, False, ["IPv4", "flush"]),
    (True, False, ["IPv4", "IPv6", "flush"]),
])
def test_ipv6_binding_change_keeps_ipv4_and_flushes(adapter, monkeypatch, initial_binding, drop_at_write, expected):
    # Run the actual branching logic in a private PowerShell with fake cmdlets.
    # Module autoload is disabled; DnsClient and NetAdapter cannot be resolved implicitly.
    run = Mock(return_value=stdout('{"skipped":false}'))
    monkeypatch.setattr(net.subprocess, "run", run)
    net._write_settings(replace(adapter, if_index=2_000_000_012), FamilyDns(("8.8.8.8",)), FamilyDns(("2001:4860:4860::8888",)), Mock())
    command = decoded_command(run)
    prelude = r"""
$ErrorActionPreference = 'Stop'
Import-Module Microsoft.PowerShell.Utility
$PSModuleAutoLoadingPreference = 'None'
if (Get-Module DnsClient, NetAdapter) { throw 'Unexpected system network module' }
$trace = [Collections.Generic.List[string]]::new()
function Get-NetAdapter([int]$InterfaceIndex,[switch]$IncludeHidden) {
    if ($InterfaceIndex -ne 2000000012) { throw 'Unexpected interface' }
    [pscustomobject]@{InterfaceGuid=$testGuid; Name='Simulated'}
}
function Get-NetAdapterBinding([string]$ComponentID,[switch]$IncludeHidden) {
    [pscustomobject]@{Name='Simulated'; Enabled=$script:binding}
}
function Get-DnsClientServerAddress([int]$InterfaceIndex,[string]$AddressFamily) { $AddressFamily }
function Set-DnsClientServerAddress {
    [CmdletBinding()]param([Parameter(ValueFromPipeline)]$Family,[string[]]$ServerAddresses)
    process {
        $trace.Add($Family)
        if ($Family -eq 'IPv4') { $script:v4 = $ServerAddresses[0] }
        if ($Family -eq 'IPv6' -and $drop) { $script:binding=$false; throw 'IPv6 disabled during assignment' }
    }
}
function Clear-DnsClientCache { $trace.Add('flush') }
"""
    flag = {True: "$true", False: "$false"}
    setup = f"\n$testGuid = '{adapter.guid}'\n$script:binding={flag[initial_binding]}\n$drop={flag[drop_at_write]}\n"
    report = "\n}\n@{ ack=($reply | ConvertFrom-Json); steps=$trace.ToArray(); ipv4=$script:v4 } | ConvertTo-Json -Compress"
    script = net.PS_PREFIX + prelude + setup + "$reply = & {\n" + command + report
    exe = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(exe), "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16le")).decode("ascii")],
                            check=True, timeout=15, capture_output=True, encoding="utf-8",
                            creationflags=subprocess.CREATE_NO_WINDOW)
    observed = json.loads(result.stdout)
    assert observed["steps"] == expected
    assert observed["ipv4"] == "8.8.8.8"
    assert observed["ack"]["skipped"] == (not initial_binding or drop_at_write)
