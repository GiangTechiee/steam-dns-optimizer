from __future__ import annotations

import re
import socket
import ssl
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait
from dataclasses import dataclass
from enum import Enum
from threading import Event
from time import perf_counter

import dns.exception
import dns.resolver

from .models import DOMAINS, DnsProvider, DomainTestDetail, Grade, NetworkAdapterInfo, ProviderBenchmarkResult, public_ip


Log = Callable[[str], None]
DNS_TIMEOUT = 2.0
HTTPS_TIMEOUT = 2.5
MAX_PROBES = 80


class ScanDiagnosis(str, Enum):
    OFFLINE_OR_DNS_BLOCKED = "OFFLINE_OR_DNS_BLOCKED"
    DPI_SUSPECTED = "DPI_SUSPECTED"
    NO_DPI_EVIDENCE = "NO_DPI_EVIDENCE"


@dataclass(frozen=True, slots=True)
class ScanReport:
    results: tuple[ProviderBenchmarkResult, ...]
    suspected_sni: tuple[str, ...]
    seconds: float

    @property
    def is_dpi_suspected(self) -> bool:
        return bool(self.suspected_sni)

    @property
    def diagnosis(self) -> ScanDiagnosis:
        if not any(d.dns_ok for r in self.results for d in r.details):
            return ScanDiagnosis.OFFLINE_OR_DNS_BLOCKED
        return ScanDiagnosis.DPI_SUSPECTED if self.is_dpi_suspected else ScanDiagnosis.NO_DPI_EVIDENCE


def error_text(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, dns.exception.Timeout)):
        return "Hết thời gian chờ"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return f"Chứng chỉ TLS không hợp lệ: {getattr(exc, 'verify_message', None) or str(exc)}"
    if isinstance(exc, ConnectionResetError):
        return "Kết nối bị reset"
    if isinstance(exc, dns.resolver.NXDOMAIN):
        return "Tên miền không tồn tại (NXDOMAIN)"
    if isinstance(exc, dns.resolver.NoAnswer):
        return "DNS không trả về bản ghi yêu cầu."
    return str(exc) or type(exc).__name__


def _check_cancelled(event: Event | None) -> None:
    if event is not None and event.is_set():
        raise CancelledError()


def _https_head(domain: str, ip: str, source: str, context: ssl.SSLContext,
                deadline: float, cancel_event: Event | None) -> None:
    def remaining() -> float:
        _check_cancelled(cancel_event)
        timeout = deadline - perf_counter()
        if timeout <= 0:
            raise TimeoutError
        return timeout

    with socket.create_connection((ip, 443), timeout=remaining(), source_address=(source, 0)) as raw:
        raw.settimeout(remaining())
        with context.wrap_socket(raw, server_hostname=domain) as tls:
            tls.settimeout(remaining())
            tls.sendall(f"HEAD / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n".encode("ascii"))
            line = bytearray()
            while len(line) < 4096:
                tls.settimeout(remaining())
                part = tls.recv(min(1024, 4096 - len(line)))
                if not part:
                    break
                line.extend(part)
                if b"\n" in part:
                    status = line.partition(b"\n")[0]
                    if re.fullmatch(rb"HTTP/1\.[01] [1-5][0-9]{2}(?: [^\r\n]*)?\r?", status):
                        return
                    break
            raise OSError("Máy chủ không trả về dòng trạng thái HTTP hợp lệ.")


def _resolve_targets(domain: str, adapter: NetworkAdapterInfo, resolver_ip: str | None) -> tuple[str, ...]:
    version = 4 if adapter.source_v4 else 6
    if not adapter.source_v4 and not adapter.source_v6:
        raise OSError("Card mạng chưa có địa chỉ IP khả dụng.")
    if resolver_ip is None:
        family = socket.AF_INET if version == 4 else socket.AF_INET6
        rows = socket.getaddrinfo(domain, 443, family, socket.SOCK_STREAM)
        addresses = (row[4][0] for row in rows)
    else:
        dns_source = adapter.source_v6 if ":" in resolver_ip else adapter.source_v4
        if not dns_source:
            raise OSError("Card mạng thiếu địa chỉ cho giao thức DNS đã chọn.")
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [resolver_ip]
        resolver.timeout = resolver.lifetime = DNS_TIMEOUT
        answer = resolver.resolve(domain, "A" if version == 4 else "AAAA", source=dns_source, search=False)
        addresses = (record.address for record in answer)
    targets = {}
    for address in addresses:
        try:
            targets[public_ip(address, version)] = None
        except ValueError:
            continue
    if not targets:
        raise ValueError(f"DNS không trả về địa chỉ CDN IPv{version} công cộng hợp lệ.")
    return tuple(targets)


def probe_domain(domain: str, adapter: NetworkAdapterInfo, context: ssl.SSLContext,
                 resolver_ip: str | None, label: str, log: Log, *, cancel_event: Event | None = None) -> DomainTestDetail:
    _check_cancelled(cancel_event)
    started = perf_counter()
    domain_deadline = started + DNS_TIMEOUT + HTTPS_TIMEOUT
    try:
        targets = _resolve_targets(domain, adapter, resolver_ip)
    except (dns.exception.DNSException, OSError, ValueError) as exc:
        elapsed = (perf_counter() - started) * 1000
        error = error_text(exc)
        log(f"{label} · {domain} · DNS {elapsed:.0f} ms · {error}")
        return DomainTestDetail(domain, False, False, elapsed, error=f"DNS: {error}")
    dns_ms = (perf_counter() - started) * 1000
    log(f"{label} · {domain} · {'A' if adapter.source_v4 else 'AAAA'} → {', '.join(targets)} · DNS {dns_ms:.0f} ms")
    started = perf_counter()
    deadline = min(domain_deadline, started + HTTPS_TIMEOUT)
    last_ip = targets[0]
    error = "Hết thời gian chờ"
    for index, ip in enumerate(targets):
        _check_cancelled(cancel_event)
        now = perf_counter()
        if now >= deadline:
            break
        # Reserve a share of the remaining budget for each untried address.
        attempt_deadline = now + (deadline - now) / (len(targets) - index)
        last_ip = ip
        try:
            _https_head(domain, ip, adapter.source_v4 or adapter.source_v6, context, attempt_deadline, cancel_event)
        except (OSError, ValueError) as exc:
            error = error_text(exc)
            log(f"{label} · {domain} · HTTPS {ip} · {error}")
        else:
            tls_ms = (perf_counter() - started) * 1000
            log(f"{label} · {domain} · HTTPS {tls_ms:.0f} ms · {ip} · HTTP OK")
            return DomainTestDetail(domain, True, True, dns_ms, tls_ms, ip)
    tls_ms = (perf_counter() - started) * 1000
    return DomainTestDetail(domain, True, False, dns_ms, tls_ms, last_ip, f"HTTPS: {error}")


def suspect_sni(results: Sequence[ProviderBenchmarkResult], *, ipv6: bool = False) -> tuple[str, ...]:
    suspects = []
    for domain in DOMAINS[:2]:
        responding = set()
        https_worked = False
        for result in results:
            for detail in result.details:
                if detail.domain == domain:
                    if detail.dns_ok:
                        responding.add(result.provider.ipv6_primary if ipv6 else result.provider.ipv4_primary)
                    https_worked |= detail.https_ok
        if len(responding) >= 3 and not https_worked:
            suspects.append(domain)
    return tuple(suspects)


def benchmark(providers: Sequence[DnsProvider], adapter: NetworkAdapterInfo, ipv6: bool,
              progress: Callable[[int, int], None], log: Log, *, cancel_event: Event | None = None) -> ScanReport:
    _check_cancelled(cancel_event)
    if not providers:
        raise ValueError("Hãy bật ít nhất một DNS phù hợp trong Danh sách DNS.")
    started = perf_counter()
    context = ssl.create_default_context()
    groups: dict[str, dict[str, DomainTestDetail]] = defaultdict(dict)
    results = []
    servers = [(p, p.ipv6_primary if ipv6 else p.ipv4_primary) for p in providers]
    for provider, server in servers:
        if not server:
            raise ValueError(f"{provider.name} thiếu địa chỉ DNS cho giao thức đã chọn.")
    probes = ((p, server, domain) for p, server in servers for domain in DOMAINS)
    capacity = min(MAX_PROBES, len(providers) * len(DOMAINS))
    pool = ThreadPoolExecutor(max_workers=capacity)
    try:
        pending = {}
        # Keep unscheduled probes in the iterator so cancellation never drains a long work queue.
        while True:
            while len(pending) < capacity:
                _check_cancelled(cancel_event)
                probe = next(probes, None)
                if probe is None:
                    break
                provider, server, domain = probe
                job = pool.submit(probe_domain, domain, adapter, context, server, provider.name, log, cancel_event=cancel_event)
                pending[job] = provider
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            _check_cancelled(cancel_event)
            for job in done:
                provider = pending.pop(job)
                detail = job.result()
                group = groups[provider.key]
                group[detail.domain] = detail
                if len(group) == len(DOMAINS):
                    results.append(ProviderBenchmarkResult(provider, tuple(group[d] for d in DOMAINS)))
                    progress(len(results), len(providers))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    _check_cancelled(cancel_event)
    order = {Grade.PASS: 0, Grade.PARTIAL: 1, Grade.FAIL: 2}
    def rank(item: ProviderBenchmarkResult) -> tuple:
        score = item.total_ms
        return order[item.grade], -item.passed, score if score is not None else float("inf"), item.provider.name

    results.sort(key=rank)
    return ScanReport(tuple(results), suspect_sni(results, ipv6=ipv6), perf_counter() - started)


def check_current(adapter: NetworkAdapterInfo, log: Log,
                  domains: tuple[str, ...] = DOMAINS) -> tuple[DomainTestDetail, ...]:
    context = ssl.create_default_context()
    with ThreadPoolExecutor(max_workers=len(domains)) as pool:
        jobs = [pool.submit(probe_domain, domain, adapter, context, None, "DNS Windows", log) for domain in domains]
        return tuple(job.result() for job in jobs)
