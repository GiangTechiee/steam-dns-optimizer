from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from ipaddress import IPv6Address
from pathlib import Path
from uuid import UUID

from src.core.models import DOMAINS, DnsBackupRecord, DnsProvider, DomainTestDetail, FamilyDns, NetworkAdapterInfo, checked_ip
from src.core.scanner import check_current
from src.core.storage import data_directory, locked, read_json, write_json


Log = Callable[[str], None]
VIRTUAL = re.compile(r"loopback|vethernet|\bwsl\b|vmware|virtualbox|hyper-v|radmin|tailscale|zerotier|tap-windows|cloudflare[\s_-]*warp", re.I)
PS_PREFIX = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;\n$ErrorActionPreference = 'Stop'\n"
WRITE_MUTEX_NAME = r"Global\SteamDNSOptimizer.DnsWrite"

INVENTORY = r"""
$routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -PolicyStore ActiveStore -ErrorAction SilentlyContinue)
$dns = @(Get-DnsClientServerAddress)
$ips = @(Get-NetIPAddress | Where-Object { $_.AddressState -eq 'Preferred' -and !$_.SkipAsSource })
$bindings = @(Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden)
function StaticDns($stack, $guid) {
    $path = "HKLM:\SYSTEM\CurrentControlSet\Services\$stack\Parameters\Interfaces\$guid"
    if (Test-Path -LiteralPath $path) {
        $key = Get-Item -LiteralPath $path
        foreach ($value in @($key.GetValue('NameServer', ''))) {
            $value -split '[,;\s]+' | Where-Object { $_ }
        }
    }
}
$rows = @(foreach ($nic in @(Get-NetAdapter -IncludeHidden)) {
    if (!$nic.InterfaceGuid -or !$nic.ifIndex) { continue }
    $ix = $nic.ifIndex
    $route = $routes | Where-Object InterfaceIndex -eq $ix |
        Sort-Object { [int]$_.RouteMetric + [int]$_.InterfaceMetric } | Select-Object -First 1
    $cost = $null
    if ($route) { $cost = [int]$route.RouteMetric + [int]$route.InterfaceMetric }
    $v4 = $dns | Where-Object { $_.InterfaceIndex -eq $ix -and [int]$_.AddressFamily -eq 2 }
    $v6 = $dns | Where-Object { $_.InterfaceIndex -eq $ix -and [int]$_.AddressFamily -eq 23 }
    $local = @($ips | Where-Object InterfaceIndex -eq $ix)
    $bind6 = $bindings | Where-Object Name -eq $nic.Name | Select-Object -First 1
    [pscustomobject]@{
        if_index = [int]$ix; guid = [string]$nic.InterfaceGuid; name = $nic.Name
        description = $nic.InterfaceDescription; up = ($nic.Status -eq 'Up')
        virtual = [bool]($nic.Virtual -or !$nic.HardwareInterface -or $nic.Hidden); route_cost = $cost
        dns_v4 = @($v4.ServerAddresses | Where-Object { $_ }); dns_v6 = @($v6.ServerAddresses | Where-Object { $_ })
        static_v4 = @(StaticDns Tcpip $nic.InterfaceGuid); static_v6 = @(StaticDns Tcpip6 $nic.InterfaceGuid)
        ipv6_enabled = [bool]$bind6.Enabled
        source_v4 = [string](($local | Where-Object { [int]$_.AddressFamily -eq 2 } | Select-Object -First 1).IPAddress)
        source_v6 = [string](($local | Where-Object { [int]$_.AddressFamily -eq 23 } |
            Sort-Object { $_.IPAddress -like 'fe80:*' } | Select-Object -First 1).IPAddress)
    }
})
ConvertTo-Json -InputObject $rows -Depth 4 -Compress
"""


def powershell(script: str, log: Log) -> list[dict]:
    command = PS_PREFIX + script
    log("PowerShell › " + script.strip())
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    exe = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(exe), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            creationflags=subprocess.CREATE_NO_WINDOW, encoding="utf-8", errors="replace",
                            capture_output=True, timeout=35, check=False)
    if result.returncode:
        # EncodedCommand serializes PowerShell errors as CLIXML. Extract the error records for the UI.
        import xml.etree.ElementTree as ET
        message = result.stderr.strip()
        if "<Objs" in message:
            try:
                root = ET.fromstring(message[message.index("<Objs"):])
                errors = [s.text or "" for s in root.iter() if s.get("S") == "Error"]
                message = " ".join(errors).replace("_x000D__x000A_", "\n").strip() or message
            except ET.ParseError:
                message = result.stderr.strip()
        raise RuntimeError(message or f"PowerShell kết thúc với mã {result.returncode}.")
    output = result.stdout.strip().lstrip("\ufeff").strip()
    data = json.loads(output) if output else None
    if data is None:
        return []
    rows = data if isinstance(data, list) else [data]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("PowerShell trả về dữ liệu khác với đối tượng JSON dự kiến.")
    return rows


def list_adapters(log: Log) -> tuple[NetworkAdapterInfo, ...]:
    rows = powershell(INVENTORY, log)
    adapters = []
    for row in rows:
        guid = str(UUID(row["guid"]))
        ix = int(row["if_index"])
        source6 = row["source_v6"]
        if source6 and "%" not in source6 and IPv6Address(source6).is_link_local:
            source6 += f"%{ix}"
        def addresses(key: str, version: int) -> tuple[str, ...]:
            return tuple(checked_ip(v, version) for v in row[key])
        adapters.append(NetworkAdapterInfo(
            ix, guid, row["name"], row["description"], row["up"],
            bool(row["virtual"] or VIRTUAL.search(row["name"] + " " + row["description"])),
            row["route_cost"], addresses("dns_v4", 4), addresses("dns_v6", 6),
            FamilyDns(addresses("static_v4", 4)), FamilyDns(addresses("static_v6", 6)),
            row["ipv6_enabled"], row["source_v4"], source6,
        ))
    return tuple(sorted(adapters, key=lambda a: (
        not a.up, a.route_cost is None, a.route_cost if a.route_cost is not None else 0, a.if_index
    )))


def adapter_choices(adapters: tuple[NetworkAdapterInfo, ...], show_virtual: bool,
                    previous_guid: str | None) -> tuple[tuple[NetworkAdapterInfo, ...], str | None]:
    """Inventory is already ordered by link state and default route cost."""
    visible = tuple(a for a in adapters if show_virtual or not a.is_virtual)
    selected = next((a.guid for a in visible if a.guid == previous_guid), None)
    if selected is None and visible:
        selected = visible[0].guid
    return visible, selected


def is_admin() -> bool:
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


@contextmanager
def _network_write_lock() -> Iterator[None]:
    # DNS is machine-wide; two elevated sessions can have different LOCALAPPDATA paths.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    handle = wintypes.HANDLE(create(None, False, WRITE_MUTEX_NAME))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status == 258:  # WAIT_TIMEOUT
            raise RuntimeError("Một tác vụ khác đang cập nhật DNS. Hãy thử lại khi hoàn tất.")
        if status not in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            kernel.ReleaseMutex(handle)
    finally:
        kernel.CloseHandle(handle)


def restart_elevated() -> bool:
    frozen = getattr(sys, "frozen", False)
    # The user requests elevation from an open window, including after a --tray start.
    args = [] if frozen else [str(Path(sys.argv[0]).resolve())]
    args += ["--handoff-from", str(os.getpid())]
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.argtypes = [ctypes.c_void_p] + [ctypes.c_wchar_p] * 4 + [ctypes.c_int]
    shell_execute.restype = ctypes.c_void_p
    reset_key = "PYINSTALLER_RESET_ENVIRONMENT"
    previous = os.environ.get(reset_key)
    if frozen:
        os.environ[reset_key] = "1"
    try:
        result = shell_execute(None, "runas", sys.executable, subprocess.list2cmdline(args), str(Path.cwd()), 1)
    finally:
        if frozen:
            if previous is None:
                os.environ.pop(reset_key, None)
            else:
                os.environ[reset_key] = previous
    return bool(result and result > 32)


def _family_command(family: int, settings: FamilyDns) -> str:
    ips = [checked_ip(ip, family) for ip in settings.static]
    argument = "-ResetServerAddresses"
    if ips:
        argument = "-ServerAddresses @(" + ",".join(f"'{v}'" for v in ips) + ")"
    # AddressFamily belongs to Get, not Set. Pipe the family-specific CIM instance.
    return (f"$family = Get-DnsClientServerAddress -InterfaceIndex $ix -AddressFamily IPv{family}\n"
            f"if (!$family) {{ throw 'Missing IPv{family} DNS interface' }}\n"
            f"$family | Set-DnsClientServerAddress {argument} -ErrorAction Stop")


def _write_settings(adapter: NetworkAdapterInfo, ipv4: FamilyDns, ipv6: FamilyDns | None, log: Log) -> bool:
    ix = int(adapter.if_index)
    if not 0 < ix <= 0xffffffff:
        raise ValueError("Chỉ số card mạng không hợp lệ.")
    guid = str(UUID(adapter.guid))
    script = f"""
$ix = {ix}
$nic = Get-NetAdapter -IncludeHidden -InterfaceIndex $ix
if ([guid]$nic.InterfaceGuid -ne [guid]'{guid}') {{ throw 'Network interface changed. Refresh and try again.' }}
$skipped = $false
{_family_command(4, ipv4)}
"""
    if ipv6 is not None:
        script += """
function IsV6Enabled {
    $binding = Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden |
        Where-Object Name -eq $nic.Name | Select-Object -First 1
    return [bool]$binding.Enabled
}
if (IsV6Enabled) {
    try {
""" + _family_command(6, ipv6) + """
    } catch {
        if (IsV6Enabled) { throw }
        $skipped = $true
    }
} else { $skipped = $true }
"""
    script += "\nClear-DnsClientCache\nConvertTo-Json -InputObject @{ skipped = $skipped } -Compress"
    rows = powershell(script, log)
    if len(rows) != 1 or not isinstance(rows[0].get("skipped"), bool):
        raise RuntimeError("Thiếu kết quả xác nhận ghi DNS từ PowerShell.")
    return rows[0]["skipped"]


def _find_adapter(guid: str, log: Log) -> NetworkAdapterInfo:
    for adapter in list_adapters(log):
        if adapter.guid == guid:
            return adapter
    raise ValueError("Card mạng đã được tháo hoặc thay thế. Hãy làm mới danh sách.")


def _verify(adapter: NetworkAdapterInfo, ipv4: FamilyDns, ipv6: FamilyDns | None, skipped: bool) -> None:
    if adapter.settings_v4 != ipv4 or (ipv6 is not None and not skipped and adapter.settings_v6 != ipv6):
        raise RuntimeError("Windows đọc lại cấu hình DNS khác với cấu hình vừa yêu cầu.")


@dataclass(frozen=True, slots=True)
class ChangeResult:
    adapter: NetworkAdapterInfo
    store_check: DomainTestDetail
    ipv6_skipped: bool


class WindowsNetwork:
    def __init__(self, directory: Path | None = None):
        self.path = (directory or data_directory()) / "dns_backup.json"

    def backups(self) -> dict[str, DnsBackupRecord]:
        return self._read_backups()

    def _read_backups(self, *, lock_held: bool = False) -> dict[str, DnsBackupRecord]:
        data = read_json(self.path, {"version": 1, "adapters": {}}, lock_held=lock_held)
        try:
            records = {str(UUID(k)): DnsBackupRecord.from_dict(v) for k, v in data["adapters"].items()}
            if any(k != v.guid for k, v in records.items()):
                raise ValueError("GUID trong bản sao lưu không khớp.")
            return records
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"Dữ liệu {self.path.name} không hợp lệ: {exc}") from exc

    def _save(self, records: dict[str, DnsBackupRecord]) -> None:
        write_json(self.path, {"version": 1, "adapters": {k: asdict(v) for k, v in records.items()}})

    def delete_backup(self, guid: str) -> None:
        with locked(self.path):
            records = self._read_backups(lock_held=True)
            if records.pop(guid, None) is None:
                raise ValueError("Bản sao lưu này đã được xóa.")
            self._save(records)

    def change(self, action: str, guid: str, provider: DnsProvider | None, log: Log) -> ChangeResult:
        if not is_admin():
            raise PermissionError("Hãy khởi động lại với quyền Admin để đổi DNS.")
        guid = str(UUID(guid))
        with _network_write_lock(), locked(self.path):
            before = _find_adapter(guid, log)
            records = self._read_backups(lock_held=True)
            if action == "restore":
                if guid not in records:
                    raise ValueError("Chưa có cấu hình gốc cho card mạng này.")
                v4, v6 = records[guid].ipv4, records[guid].ipv6
            elif action == "dhcp":
                v4, v6 = FamilyDns(), FamilyDns()
            elif action == "apply" and provider is not None:
                v4 = FamilyDns(provider.ipv4)
                v6 = FamilyDns(provider.ipv6) if provider.ipv6 else None
            else:
                raise ValueError("Thao tác DNS không hợp lệ.")
            if guid not in records:
                records[guid] = DnsBackupRecord(guid, before.if_index, before.name,
                    datetime.now().astimezone().isoformat(timespec="seconds"), before.settings_v4, before.settings_v6)
                self._save(records)
                log(f"Đã lưu bản gốc của {before.name} vào {self.path.name}.")
            try:
                skipped = _write_settings(before, v4, v6, log)
                after = _find_adapter(guid, log)
                _verify(after, v4, v6, skipped)
                if action in ("restore", "dhcp") and (not skipped or after.settings_v6 == v6):
                    del records[guid]
                    self._save(records)
            except Exception as change_error:
                log(f"Lỗi ghi DNS, đang hoàn tác trên {before.name}: {change_error}")
                try:
                    rollback_v6 = before.settings_v6 if v6 is not None else None
                    _write_settings(before, before.settings_v4, rollback_v6, log)
                    _verify(_find_adapter(guid, log), before.settings_v4, rollback_v6, skipped=False)
                except Exception as rollback_error:
                    raise RuntimeError(f"Đổi DNS lỗi: {change_error}\nHoàn tác chưa xác nhận: {rollback_error}\n"
                                       f"Bản gốc đã lưu tại {self.path}.") from rollback_error
                raise RuntimeError(f"Đã hoàn tác cấu hình trước thao tác. Chi tiết: {change_error}") from change_error
            log(f"Đã xác nhận DNS của {after.name}; đang kiểm tra Steam Store qua DNS Windows.")
            try:
                detail, = check_current(after, log, DOMAINS[:1])
            except Exception as exc:
                # The OS configuration is already committed. Surface a failed diagnostic separately.
                message = f"Chưa xác nhận được kết nối Steam Store: {exc}"
                log(message)
                detail = DomainTestDetail(DOMAINS[0], False, False, 0, error=message)
        return ChangeResult(after, detail, skipped)
