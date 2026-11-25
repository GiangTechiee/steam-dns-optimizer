from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from .models import DnsProvider, NonPublicAddress
from .storage import archive_json, data_directory, locked, read_json, write_json


BUILTINS = (
    DnsProvider("cloudflare", "Cloudflare", "1.1.1.1", "1.0.0.1", "2606:4700:4700::1111", "2606:4700:4700::1001"),
    DnsProvider("google", "Google Public DNS", "8.8.8.8", "8.8.4.4", "2001:4860:4860::8888", "2001:4860:4860::8844"),
    DnsProvider("quad9", "Quad9 Unfiltered", "9.9.9.10", "149.112.112.10", "2620:fe::10", "2620:fe::fe:10"),
    DnsProvider("opendns", "OpenDNS Home", "208.67.222.222", "208.67.220.220", "2620:119:35::35", "2620:119:53::53"),
    DnsProvider("adguard", "AdGuard Unfiltered", "94.140.14.140", "94.140.14.141", "2a10:50c0::1:ff", "2a10:50c0::2:ff"),
    DnsProvider("controld", "Control D Free", "76.76.2.0", "76.76.10.0", "2606:1a40::", "2606:1a40:1::"),
    DnsProvider("verisign", "Verisign Public DNS", "64.6.64.6", "64.6.65.6", "2620:74:1b::1:1", "2620:74:1c::2:2"),
    DnsProvider("cleanbrowsing", "CleanBrowsing Security", "185.228.168.9", "185.228.169.9", "2a0d:2a00:1::2", "2a0d:2a00:2::2"),
    DnsProvider("gcore", "Gcore Public DNS", "95.85.95.85", "2.56.220.2", "2a03:90c0:999d::1", "2a03:90c0:9992::1"),
    DnsProvider("dnssb", "DNS.SB", "185.222.222.222", "45.11.45.11", "2a09::", "2a11::"),
    DnsProvider("he", "Hurricane Electric", "74.82.42.42", "", "2001:470:20::2"),
    DnsProvider("mullvad", "Mullvad Public DNS", "194.242.2.2", "", "2a07:e340::2"),
)


class ProviderStore:
    def __init__(self, directory: Path | None = None):
        self.path = (directory or data_directory()) / "custom_dns.json"

    def load(self) -> tuple[tuple[DnsProvider, ...], set[str]]:
        with locked(self.path):
            return self._load_locked()

    def _load_locked(self) -> tuple[tuple[DnsProvider, ...], set[str]]:
        data = read_json(self.path, {"version": 1, "custom": [], "disabled": []}, lock_held=True)
        try:
            custom = []
            retired = set()
            for item in data["custom"]:
                try:
                    provider = DnsProvider(**item)
                except NonPublicAddress:
                    # Version 1 previously allowed private custom resolvers.
                    retired.add(item["key"])
                    continue
                if not provider.is_custom:
                    raise ValueError("Mã DNS tùy chỉnh không hợp lệ.")
                UUID(provider.key.removeprefix("custom-"))
                custom.append(provider)
            providers = BUILTINS + tuple(custom)
            if len({p.key for p in providers}) != len(providers):
                raise ValueError("Trùng mã DNS.")
            if not isinstance(data["disabled"], list) or not all(isinstance(k, str) for k in data["disabled"]):
                raise ValueError("Danh sách bật/tắt không hợp lệ.")
            disabled = set(data["disabled"]) - retired
            if retired:
                archive_json(self.path, self.path.read_bytes())
                self._save(providers, disabled)
            return providers, disabled
        except (TypeError, KeyError, AttributeError, ValueError) as exc:
            raise ValueError(f"Dữ liệu {self.path.name} không hợp lệ: {exc}") from exc

    def _save(self, providers: tuple[DnsProvider, ...], disabled: set[str]) -> None:
        write_json(self.path, {"version": 1, "custom": [asdict(p) for p in providers if p.is_custom],
                               "disabled": sorted(disabled)})

    def set_enabled(self, key: str, enabled: bool) -> None:
        with locked(self.path):
            providers, disabled = self._load_locked()
            if key not in {p.key for p in providers}:
                raise ValueError("DNS này đã bị xóa.")
            if enabled:
                disabled.discard(key)
            else:
                disabled.add(key)
            self._save(providers, disabled)

    def add(self, name: str, ipv4_primary: str, ipv4_secondary: str = "",
            ipv6_primary: str = "", ipv6_secondary: str = "") -> None:
        new = DnsProvider(f"custom-{uuid4()}", name, ipv4_primary, ipv4_secondary, ipv6_primary, ipv6_secondary)
        with locked(self.path):
            providers, disabled = self._load_locked()
            if any(p.name.casefold() == new.name.casefold() for p in providers):
                raise ValueError("Tên DNS này đã có trong danh sách.")
            self._save((*providers, new), disabled)

    def remove(self, key: str) -> None:
        with locked(self.path):
            providers, disabled = self._load_locked()
            if not any(p.key == key and p.is_custom for p in providers):
                raise ValueError("Chỉ có thể xóa một DNS tự thêm.")
            self._save(tuple(p for p in providers if p.key != key), disabled - {key})
