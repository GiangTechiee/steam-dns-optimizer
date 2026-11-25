from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ipaddress import ip_address
from statistics import fmean
from uuid import UUID


DOMAINS = (
    "store.steampowered.com",
    "steamcommunity.com",
    "login.steampowered.com",
    "api.steampowered.com",
    "help.steampowered.com",
)
DOMAIN_LABELS = ("Store", "Comm", "Login", "API", "Help")


def checked_ip(value: str, version: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Cần chuỗi địa chỉ IPv{version}.")
    value = value.strip()
    address, separator, zone = value.partition("%")
    if separator and (version != 6 or not zone.isascii() or not zone.isdecimal()):
        raise ValueError("Mã vùng của địa chỉ IPv6 phải là số giao diện.")
    try:
        parsed = ip_address(address)
    except ValueError as exc:
        raise ValueError(f"Địa chỉ IPv{version} không hợp lệ: {value}") from exc
    if parsed.version != version:
        raise ValueError(f"Cần địa chỉ IPv{version}: {value}")
    return str(parsed) + (f"%{int(zone)}" if separator else "")


class NonPublicAddress(ValueError):
    """An address unsuitable for a public resolver or a Steam CDN endpoint."""


def public_ip(value: str, version: int) -> str:
    normalized = checked_ip(value, version)
    address = ip_address(normalized)
    if (not address.is_global or address.is_multicast or address.is_reserved or "%" in normalized
            or getattr(address, "is_site_local", False)):
        raise NonPublicAddress(f"Cần địa chỉ IPv{version} unicast công cộng: {value.strip()}")
    return normalized


@dataclass(frozen=True, slots=True)
class DnsProvider:
    key: str
    name: str
    ipv4_primary: str
    ipv4_secondary: str = ""
    ipv6_primary: str = ""
    ipv6_secondary: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 64 or not self.name.strip().isprintable():
            raise ValueError("Tên DNS cần từ 1 đến 64 ký tự hiển thị được.")
        object.__setattr__(self, "name", self.name.strip())
        for field, version in (("ipv4_primary", 4), ("ipv4_secondary", 4),
                               ("ipv6_primary", 6), ("ipv6_secondary", 6)):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise ValueError(f"Cần chuỗi địa chỉ IPv{version}.")
            value = value.strip()
            object.__setattr__(self, field, public_ip(value, version) if value or field == "ipv4_primary" else "")
        if self.ipv6_secondary and not self.ipv6_primary:
            raise ValueError("Nhập IPv6 chính trước IPv6 phụ.")
        if self.ipv4_primary == self.ipv4_secondary or (self.ipv6_primary and self.ipv6_primary == self.ipv6_secondary):
            raise ValueError("DNS chính và phụ cần là hai địa chỉ khác nhau.")

    @property
    def ipv4(self) -> tuple[str, ...]:
        return tuple(filter(None, (self.ipv4_primary, self.ipv4_secondary)))

    @property
    def ipv6(self) -> tuple[str, ...]:
        return tuple(filter(None, (self.ipv6_primary, self.ipv6_secondary)))

    @property
    def is_custom(self) -> bool:
        return self.key.startswith("custom-")


@dataclass(frozen=True, slots=True)
class DomainTestDetail:
    domain: str
    dns_ok: bool
    https_ok: bool
    dns_ms: float
    tls_ms: float | None = None
    resolved_ip: str = ""
    error: str = ""


class Grade(str, Enum):
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class ProviderBenchmarkResult:
    provider: DnsProvider
    details: tuple[DomainTestDetail, ...]

    @property
    def passed(self) -> int:
        return sum(d.dns_ok and d.https_ok for d in self.details)

    @property
    def grade(self) -> Grade:
        good = {d.domain for d in self.details if d.dns_ok and d.https_ok}
        if good.issuperset(DOMAINS):
            return Grade.PASS
        if good.issuperset(DOMAINS[:2]):
            return Grade.PARTIAL
        return Grade.FAIL

    @property
    def avg_dns_ms(self) -> float | None:
        times = [d.dns_ms for d in self.details if d.dns_ok]
        return fmean(times) if times else None

    @property
    def avg_tls_ms(self) -> float | None:
        times = [d.tls_ms for d in self.details if d.https_ok and d.tls_ms is not None]
        return fmean(times) if times else None

    @property
    def total_ms(self) -> float | None:
        dns, tls = self.avg_dns_ms, self.avg_tls_ms
        return None if dns is None or tls is None else dns + tls


@dataclass(frozen=True, slots=True)
class FamilyDns:
    # An empty static list represents automatic DNS. DHCP addresses themselves expire.
    static: tuple[str, ...] = ()

    @property
    def description(self) -> str:
        return ", ".join(self.static) if self.static else "Tự động (DHCP)"


@dataclass(frozen=True, slots=True)
class NetworkAdapterInfo:
    if_index: int
    guid: str
    name: str
    description: str
    up: bool
    is_virtual: bool
    route_cost: int | None
    dns_v4: tuple[str, ...]
    dns_v6: tuple[str, ...]
    settings_v4: FamilyDns
    settings_v6: FamilyDns
    ipv6_enabled: bool
    source_v4: str
    source_v6: str

    def uses(self, provider: DnsProvider) -> bool:
        return self.dns_v4 == provider.ipv4 and (
            not provider.ipv6 or not self.ipv6_enabled or self.dns_v6 == provider.ipv6
        )


@dataclass(frozen=True, slots=True)
class DnsBackupRecord:
    guid: str
    if_index: int
    name: str
    saved_at: str
    ipv4: FamilyDns
    ipv6: FamilyDns

    @classmethod
    def from_dict(cls, data: dict) -> DnsBackupRecord:
        def family(key: str, version: int) -> FamilyDns:
            values = data[key]["static"]
            if not isinstance(values, list):
                raise ValueError("Danh sách DNS trong bản sao lưu không hợp lệ.")
            return FamilyDns(tuple(checked_ip(v, version) for v in values))

        return cls(str(UUID(data["guid"])), int(data["if_index"]), data["name"], data["saved_at"],
                   family("ipv4", 4), family("ipv6", 6))
