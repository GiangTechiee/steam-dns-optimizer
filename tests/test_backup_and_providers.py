import json
from dataclasses import asdict, replace
from unittest.mock import Mock, patch

import pytest

from src.core.models import DnsProvider, FamilyDns
from src.core.providers import BUILTINS, ProviderStore
from src.services import windows_net as net


@pytest.mark.parametrize("finish", ["restore", "dhcp"])
def test_snapshot_cycle_keeps_first_dns_then_captures_new_baseline(network_machine, adapter, finish):
    machine = network_machine
    service = machine.service
    guid = adapter.guid
    service.change("apply", "{" + guid.upper() + "}", BUILTINS[1], Mock())
    first = service.path.read_bytes()
    assert json.loads(first)["adapters"][guid]["ipv4"]["static"] == ["10.0.0.2"]
    assert machine.adapter.dns_v4[0] == "8.8.8.8"
    service.change("apply", guid, BUILTINS[2], Mock())
    assert machine.adapter.dns_v4[0] == "9.9.9.10"
    assert service.path.read_bytes() == first
    machine.adapter = replace(machine.adapter, name="Card đổi tên", if_index=71)
    fresh = net.WindowsNetwork(service.path.parent)
    after = fresh.change(finish, guid, None, Mock()).adapter
    assert after.if_index == 71
    assert after.settings_v4 == (adapter.settings_v4 if finish == "restore" else FamilyDns())
    assert after.settings_v6 == FamilyDns() and guid not in fresh.backups()
    machine.adapter = replace(after, settings_v4=FamilyDns(("10.0.0.9",)), dns_v4=("10.0.0.9",))
    fresh.change("apply", guid, BUILTINS[0], Mock())
    assert fresh.backups()[guid].ipv4.static == ("10.0.0.9",)


def test_retirement_is_specific_to_the_selected_card(network_machine, adapter):
    service = network_machine.service
    service.change("apply", adapter.guid, BUILTINS[0], Mock())
    other = replace(adapter, guid="a04a4d97-6f3e-4b59-87d2-89ceef60a548", if_index=45)
    network_machine.adapter = other
    service.change("apply", other.guid, BUILTINS[1], Mock())
    service.change("restore", other.guid, None, Mock())
    assert set(service.backups()) == {adapter.guid}


def test_dhcp_is_snapshotted_before_first_write_and_mixed_families_restore(network_machine, adapter):
    machine = network_machine
    machine.adapter = replace(adapter, settings_v4=FamilyDns(), settings_v6=FamilyDns(("fd00::53",)))
    service = machine.service
    first = machine.writer.side_effect

    def verify_snapshot(before, v4, v6, log):
        snapshot = json.loads(service.path.read_bytes())["adapters"][adapter.guid]
        assert snapshot["ipv4"]["static"] == [] and snapshot["ipv6"]["static"] == ["fd00::53"]
        return first(before, v4, v6, log)

    machine.writer.side_effect = verify_snapshot
    service.change("apply", adapter.guid, BUILTINS[0], Mock())
    after = service.change("restore", adapter.guid, None, Mock()).adapter
    assert not after.settings_v4.static and after.settings_v6.static == ("fd00::53",)


@pytest.mark.parametrize("action", ["apply", "restore", "dhcp"])
def test_partial_write_rolls_back_and_preserves_original_record(network_machine, adapter, action):
    machine = network_machine
    before = machine.service.change("apply", adapter.guid, BUILTINS[1], Mock()).adapter
    first = machine.service.path.read_bytes()
    machine.fail_next_ipv6 = True
    with pytest.raises(RuntimeError, match="hoàn tác"):
        machine.service.change(action, adapter.guid, BUILTINS[2] if action == "apply" else None, Mock())
    assert machine.adapter == before
    assert machine.service.path.read_bytes() == first


def test_failed_readback_keeps_snapshot_and_rolls_back(network_machine, adapter):
    service = network_machine.service
    before = service.change("apply", adapter.guid, BUILTINS[0], Mock()).adapter
    with patch.object(net, "_verify", side_effect=[RuntimeError("readback failed"), None]):
        with pytest.raises(RuntimeError, match="readback failed"):
            service.change("restore", adapter.guid, None, Mock())
    assert network_machine.adapter == before
    assert service.backups()[adapter.guid].ipv4 == adapter.settings_v4


def test_disabled_ipv6_keeps_pending_restore_until_configuration_matches(network_machine, adapter):
    service = network_machine.service
    after = service.change("apply", adapter.guid, BUILTINS[0], Mock()).adapter
    network_machine.adapter = replace(after, ipv6_enabled=False)
    assert service.change("restore", adapter.guid, None, Mock()).ipv6_skipped
    assert network_machine.adapter.settings_v4 == adapter.settings_v4
    assert adapter.guid in service.backups()
    network_machine.adapter = replace(network_machine.adapter, ipv6_enabled=True)
    assert not service.change("restore", adapter.guid, None, Mock()).ipv6_skipped
    assert network_machine.adapter.settings_v6 == adapter.settings_v6 and not service.backups()


def test_ipv4_only_provider_preserves_configured_ipv6(network_machine, adapter):
    provider = DnsProvider("single", "IPv4 only", "8.8.8.8")
    after = network_machine.service.change("apply", adapter.guid, provider, Mock()).adapter
    assert after.settings_v6 == adapter.settings_v6 and after.dns_v6 == adapter.dns_v6


def test_no_permission_or_no_restore_record_never_writes(network_machine, adapter):
    with pytest.raises(ValueError):
        network_machine.service.change("restore", adapter.guid, None, Mock())
    with patch.object(net, "is_admin", return_value=False), pytest.raises(PermissionError):
        network_machine.service.change("apply", adapter.guid, BUILTINS[0], Mock())
    network_machine.writer.assert_not_called()
    assert not network_machine.service.path.exists()


@pytest.mark.parametrize("kind", ["backup", "providers"])
@pytest.mark.parametrize("raw", [b"", b"{bad_json", b"\xff\xfe\xff"])
def test_broken_json_is_archived_byte_for_byte_and_reinitialized(tmp_path, kind, raw):
    if kind == "backup":
        store = net.WindowsNetwork(tmp_path)
        load, default = store.backups, {}
    else:
        store = ProviderStore(tmp_path)
        load, default = store.load, (BUILTINS, set())
    path = store.path
    path.write_bytes(raw)
    assert load() == default
    assert path.with_name(path.name + ".bak").read_bytes() == raw
    assert json.loads(path.read_bytes())["version"] == 1 and load() == default
    path.write_bytes(b"{again")
    assert load() == default
    assert path.with_name(path.name + ".bak").read_bytes() == raw
    assert path.with_name(path.name + ".1.bak").read_bytes() == b"{again"


def test_restore_of_corrupt_backup_and_archive_failure_never_touch_adapter(network_machine, adapter):
    machine = network_machine
    path = machine.service.path
    path.write_bytes(b"{broken")
    with patch("src.core.storage.archive_json", side_effect=OSError("disk full")), pytest.raises(OSError):
        machine.service.change("apply", adapter.guid, BUILTINS[0], Mock())
    assert path.read_bytes() == b"{broken"
    with pytest.raises(ValueError, match="Chưa có"):
        machine.service.change("restore", adapter.guid, None, Mock())
    machine.writer.assert_not_called()
    assert machine.service.backups() == {} and path.with_name(path.name + ".bak").read_bytes() == b"{broken"


@pytest.mark.parametrize("fields", [
    ("1.1.1.1:53",), ("https://1.1.1.1",), ("1.1.1.1; whoami",), ("10.0.0.1",), ("192.168.1.1",),
    ("127.0.0.1",), ("0.0.0.0",), ("224.0.0.1",), ("8.8.8.8", " 8.8.8.8 "), ("::1",),
    ("8.8.8.8", "127.0.0.1"), ("8.8.8.8", "", "1.1.1.1"), ("8.8.8.8", "", "::"),
    ("8.8.8.8", "", "::1"), ("8.8.8.8", "", "fd00::53"), ("8.8.8.8", "", "[2606:4700:4700::1111]:53"),
    ("8.8.8.8", "", "2606:4700:4700::1111", "2606:4700:4700:0:0:0:0:1111"),
    ("8.8.8.8", "", "", "2606:4700:4700::1111"), ("8.8.8.8", "", "fe80::1%tên card"),
])
def test_invalid_custom_resolver_is_rejected_before_save(tmp_path, fields):
    store = ProviderStore(tmp_path)
    with pytest.raises(ValueError):
        store.add("DNS thử", *fields)
    assert store.load() == (BUILTINS, set())
    assert not store.path.exists()


def test_custom_crud_normalizes_whitespace_and_remembers_toggles(tmp_path):
    store = ProviderStore(tmp_path)
    store.add("  DNS nhà  ", " 1.2.3.4 ", "", " 2001:4860:4860:0:0:0:0:8888 ")
    item = store.load()[0][-1]
    assert (item.name, item.ipv4_primary, item.ipv6_primary) == ("DNS nhà", "1.2.3.4", "2001:4860:4860::8888")
    with pytest.raises(ValueError):
        store.add("dns NHÀ", "1.2.3.5")
    store.set_enabled(BUILTINS[0].key, False)
    store.remove(item.key)
    assert ProviderStore(tmp_path).load() == (BUILTINS, {BUILTINS[0].key})


def test_legacy_private_resolver_preserves_other_entries_and_original_bytes(tmp_path):
    store = ProviderStore(tmp_path)
    store.add("DNS hợp lệ", "8.8.8.8")
    valid = store.load()[0][-1]
    legacy = asdict(valid) | {"key": "custom-4aae5e75-28b3-480e-a103-fe4810e156bc", "ipv4_primary": "10.0.0.2"}
    data = json.loads(store.path.read_bytes())
    data["custom"].append(legacy)
    data["disabled"] = [valid.key, legacy["key"]]
    raw = json.dumps(data).encode()
    store.path.write_bytes(raw)
    assert store.load() == (BUILTINS + (valid,), {valid.key})
    assert store.path.with_name("custom_dns.json.bak").read_bytes() == raw
    assert not any(p["ipv4_primary"] == "10.0.0.2" for p in json.loads(store.path.read_bytes())["custom"])


def test_future_storage_version_is_preserved(tmp_path):
    store = net.WindowsNetwork(tmp_path)
    raw = b'{"version":42,"adapters":{}}'
    store.path.write_bytes(raw)
    with pytest.raises(ValueError):
        store.backups()
    assert store.path.read_bytes() == raw


def test_first_dhcp_saves_original_before_write_and_can_start_next_cycle(network_machine, adapter):
    before_write = network_machine.writer.side_effect

    def check_snapshot(before, v4, v6, log):
        assert network_machine.service.backups()[adapter.guid].ipv4.static == ("10.0.0.2",)
        return before_write(before, v4, v6, log)

    network_machine.writer.side_effect = check_snapshot
    after = network_machine.service.change("dhcp", adapter.guid, None, Mock()).adapter
    assert after.settings_v4 == after.settings_v6 == FamilyDns()
    assert network_machine.service.backups() == {}


def test_post_commit_check_failure_returns_warning_without_undoing_verified_dns(network_machine, adapter):
    network_machine.check.side_effect = OSError("no trust store")
    outcome = network_machine.service.change("apply", adapter.guid, BUILTINS[1], Mock())
    assert outcome.adapter.dns_v4 == BUILTINS[1].ipv4
    assert not outcome.store_check.https_ok and "no trust store" in outcome.store_check.error
    network_machine.writer.assert_called_once()
    assert network_machine.service.backups()[adapter.guid].ipv4 == adapter.settings_v4
