import socket
import ssl
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import dns.exception
import dns.resolver
import pytest

from src.core import scanner
from src.core.models import DOMAINS, DnsProvider, DomainTestDetail, Grade
from src.core.providers import BUILTINS


@pytest.fixture
def wire():
    with patch.object(scanner.dns.resolver, "Resolver") as factory, \
         patch.object(scanner.socket, "create_connection") as connect, \
         patch.object(scanner.ssl, "create_default_context") as make_context:
        resolver = factory.return_value
        resolver.resolve.return_value = [SimpleNamespace(address="8.8.8.8")]
        raw = connect.return_value.__enter__.return_value
        context = make_context.return_value
        tls = context.wrap_socket.return_value.__enter__.return_value
        tls.recv.return_value = b"HTTP/1.1 200 OK\r\n"
        yield SimpleNamespace(resolver=resolver, connect=connect, context=context, tls=tls, raw=raw)


def probe(wire, adapter, server="1.1.1.1"):
    return scanner.probe_domain(DOMAINS[0], adapter, wire.context, server, "Test", Mock())


@pytest.mark.parametrize("ip", [
    "0.0.0.0", "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "::1", "fd00::1",
    "100.64.0.1", "224.0.0.1", "ff02::1", "192.0.2.1",
])
def test_bogon_never_opens_tcp(wire, adapter, ip):
    wire.resolver.resolve.return_value = [SimpleNamespace(address=ip)]
    if ":" in ip:
        adapter = replace(adapter, source_v4="")
    detail = probe(wire, adapter, "2606:4700:4700::1111" if ":" in ip else "1.1.1.1")
    assert not detail.dns_ok and not detail.https_ok and detail.error
    wire.connect.assert_not_called()


def test_windows_resolver_uses_same_destination_filter(wire, adapter):
    with patch.object(socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        assert not probe(wire, adapter, None).dns_ok
    wire.connect.assert_not_called()


@pytest.mark.parametrize("error", [
    ssl.SSLCertVerificationError("hostname mismatch"), ssl.SSLError("invalid TLS record"),
])
def test_certificate_and_tls_failures_are_results(wire, adapter, error):
    wire.context.wrap_socket.side_effect = error
    detail = probe(wire, adapter)
    assert detail.dns_ok and not detail.https_ok and detail.error.startswith("HTTPS")
    wire.context.wrap_socket.assert_called_once_with(wire.raw, server_hostname=DOMAINS[0])
    wire.tls.sendall.assert_not_called()


@pytest.mark.parametrize("status", [b"200 OK", b"301 Moved Permanently", b"302 Found", b"403 Forbidden", b"404 Not Found"])
def test_steam_http_status_and_sni(wire, adapter, status):
    wire.tls.recv.return_value = b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\n\r\n"
    detail = probe(wire, adapter)
    assert detail.https_ok and detail.dns_ok
    assert wire.connect.call_args.args == (("8.8.8.8", 443),)
    assert wire.connect.call_args.kwargs["source_address"] == (adapter.source_v4, 0)
    wire.context.wrap_socket.assert_called_once_with(wire.raw, server_hostname=DOMAINS[0])
    wire.tls.sendall.assert_called_once_with(
        f"HEAD / HTTP/1.1\r\nHost: {DOMAINS[0]}\r\nConnection: close\r\n\r\n".encode("ascii"))


@pytest.mark.parametrize("chunks,ok", [
    ([b""], False), ([b"\x15\x03\x01...", b""], False), ([b"HTTP/", b""], False),
    ([b"HTTP/1.1 xyz\r\n"], False), ([b"HT", b"TP/1.", b"1 404 Not Found\r", b"\n"], True),
])
def test_eof_garbage_or_fragmented_status_line(wire, adapter, chunks, ok):
    wire.tls.recv.side_effect = chunks
    detail = probe(wire, adapter)
    assert detail.dns_ok and detail.https_ok is ok


@pytest.mark.parametrize("stage,error", [
    ("dns", dns.exception.Timeout()), ("dns", dns.resolver.NXDOMAIN()), ("dns", dns.resolver.NoAnswer()),
    ("connect", socket.timeout()), ("recv", socket.timeout()), ("recv", ConnectionResetError()),
    ("connect", ConnectionResetError()),
])
def test_network_errors_do_not_escape_threadpool(wire, adapter, stage, error):
    operation = {"dns": wire.resolver.resolve, "connect": wire.connect, "recv": wire.tls.recv}[stage]
    operation.side_effect = error
    progress = Mock()
    report = scanner.benchmark(BUILTINS[:1], adapter, False, progress, Mock())
    progress.assert_called_once_with(1, 1)
    details = report.results[0].details
    assert tuple(d.domain for d in details) == DOMAINS
    assert all(d.dns_ok == (stage != "dns") and not d.https_ok and d.error for d in details)


@pytest.mark.parametrize("secondary_failure", [False, True])
def test_multiple_cdn_ips_share_one_deadline(wire, adapter, secondary_failure):
    now = 0.0

    def resolve(*args, **kwargs):
        nonlocal now
        now += 1.9
        return [SimpleNamespace(address=ip) for ip in ("10.0.0.1", "8.8.8.8", "8.8.8.8", "1.1.1.1")]

    raw_socket = MagicMock()

    def connect(destination, *, timeout, source_address):
        nonlocal now
        if destination[0] == "8.8.8.8" or secondary_failure:
            now += timeout
            raise socket.timeout()
        now += 0.01
        return raw_socket

    wire.resolver.resolve.side_effect = resolve
    wire.connect.side_effect = connect
    with patch.object(scanner, "perf_counter", side_effect=lambda: now):
        detail = probe(wire, adapter)
    assert [c.args[0][0] for c in wire.connect.call_args_list] == ["8.8.8.8", "1.1.1.1"]
    assert detail.dns_ok and detail.https_ok is not secondary_failure
    assert detail.resolved_ip == "1.1.1.1"
    assert 0 < detail.tls_ms <= scanner.HTTPS_TIMEOUT * 1000 + 0.01
    assert now <= scanner.DNS_TIMEOUT + scanner.HTTPS_TIMEOUT


@pytest.mark.parametrize("critical", DOMAINS[:2])
@pytest.mark.parametrize("count,one_success,suspected", [(2, False, False), (3, False, True), (4, True, False)])
def test_dpi_threshold_is_per_required_domain(wire, adapter, critical, count, one_success, suspected):
    def measure(domain, nic, context, server, label, log, **kwargs):
        ok = domain != critical or (one_success and label == BUILTINS[1].name)
        return DomainTestDetail(domain, True, ok, 5, 20, "8.8.8.8")

    with patch.object(scanner, "probe_domain", side_effect=measure):
        report = scanner.benchmark(BUILTINS[:count], adapter, False, Mock(), Mock())
    assert report.is_dpi_suspected is suspected
    assert report.suspected_sni == ((critical,) if suspected else ())
    assert report.diagnosis != "OFFLINE_OR_DNS_BLOCKED"


@pytest.mark.parametrize("bogon", [True, False])
def test_total_dns_failure_is_offline_not_dpi(wire, adapter, bogon):
    if bogon:
        wire.resolver.resolve.return_value = [SimpleNamespace(address="192.168.1.1")]
    else:
        wire.resolver.resolve.side_effect = dns.exception.Timeout()
    report = scanner.benchmark(BUILTINS[:4], adapter, False, Mock(), Mock())
    assert report.diagnosis == "OFFLINE_OR_DNS_BLOCKED"
    assert not report.is_dpi_suspected and not report.suspected_sni
    wire.connect.assert_not_called()


def test_grade_sort_order_and_provider_progress(wire, adapter):
    def measure(domain, nic, ctx, server, label, log, **kwargs):
        blocked = {BUILTINS[1].name: DOMAINS[:1], BUILTINS[2].name: DOMAINS[2:]}
        return DomainTestDetail(domain, True, domain not in blocked.get(label, ()), 4,
                                80 if label == BUILTINS[0].name else 20, "8.8.8.8")

    progress = Mock()
    with patch.object(scanner, "probe_domain", side_effect=measure):
        report = scanner.benchmark(BUILTINS[:4], adapter, False, progress, Mock())
    assert [r.provider for r in report.results] == [BUILTINS[3], BUILTINS[0], BUILTINS[2], BUILTINS[1]]
    assert [r.grade for r in report.results] == [Grade.PASS, Grade.PASS, Grade.PARTIAL, Grade.FAIL]
    assert [c.args for c in progress.call_args_list] == [(1, 4), (2, 4), (3, 4), (4, 4)]


def test_three_distinct_resolvers_and_one_dns_failure_still_detect_dpi(wire, adapter):
    resolvers = [MagicMock() for _ in range(4)]
    for resolver in resolvers[:3]:
        resolver.resolve.return_value = [SimpleNamespace(address="8.8.8.8")]
    resolvers[3].resolve.side_effect = dns.resolver.NoAnswer()
    # Each probe constructs its own resolver; route the answer based on its assigned nameserver.
    by_server = dict(zip((p.ipv4_primary for p in BUILTINS[:4]), resolvers))

    def make_resolver(**kwargs):
        instance = MagicMock()
        instance.resolve.side_effect = lambda *a, **kw: by_server[instance.nameservers[0]].resolve(*a, **kw)
        return instance

    with patch.object(scanner.dns.resolver, "Resolver", side_effect=make_resolver):
        wire.connect.side_effect = ConnectionResetError()
        report = scanner.benchmark(BUILTINS[:4], adapter, False, Mock(), Mock())
        assert report.diagnosis == "DPI_SUSPECTED" and report.suspected_sni == DOMAINS[:2]
        alias = DnsProvider("custom-alias", "Alias", BUILTINS[0].ipv4_primary)
        duplicate = scanner.benchmark((BUILTINS[0], BUILTINS[1], alias), adapter, False, Mock(), Mock())
        assert not duplicate.is_dpi_suspected


def test_ipv6_only_uses_aaaa_and_binds_both_stages(wire, adapter):
    ipv6 = replace(adapter, source_v4="")
    address = "2606:4700:4700::1111"
    wire.resolver.resolve.return_value = [SimpleNamespace(address=address)]
    detail = probe(wire, ipv6, "2001:4860:4860::8888")
    assert detail.https_ok and detail.resolved_ip == address
    wire.resolver.resolve.assert_called_once_with(DOMAINS[0], "AAAA", source=ipv6.source_v6, search=False)
    assert wire.connect.call_args.args == ((address, 443),)
    assert wire.connect.call_args.kwargs["source_address"] == (ipv6.source_v6, 0)


def test_offline_report_is_displayed_as_dns_failure(wire, adapter, qt_app):
    from qfluentwidgets import InfoBadge
    from src.ui.pages.scanner_page import ScannerPage

    wire.resolver.resolve.return_value = []
    report = scanner.benchmark(BUILTINS[:3], adapter, False, Mock(), Mock())
    page = ScannerPage(False)
    page.show_report(report)
    assert page.warning_bar.title == "Không phân giải được tên miền Steam"
    assert all(page.table.cellWidget(i, 1).findChild(InfoBadge).text() == "LỖI DNS" for i in range(3))
    page.deleteLater()
