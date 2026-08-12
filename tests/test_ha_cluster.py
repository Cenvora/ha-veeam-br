"""Tests for High Availability cluster support (VBR 13.1, API 1.3-rev2).

The parse helpers live in __init__.py, which imports Home Assistant, so they are loaded
from source into a module stubbed with just the names they use. That keeps the assertions
behavioural — real payloads in, coordinator data out — without needing Home Assistant
installed, which this repo's test environment does not have.
"""

import ast
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re

import pytest

INIT_PATH = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"
BUTTON_PATH = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "button.py"
SENSOR_PATH = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sensor.py"

HELPERS = (
    "_bool_or_none",
    "_parse_ha_cluster_node",
    "_parse_ha_cluster_last_online",
    "_parse_ha_cluster",
)


def _parse_datetime(value):
    """Stand-in for homeassistant.util.dt.parse_datetime."""
    value = re.sub(r"\.(\d{6})\d+", r".\1", value)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@pytest.fixture(name="helpers", scope="module")
def helpers_fixture():
    """Execute just the HA cluster helpers from __init__.py."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    wanted = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    ]
    assert len(wanted) == len(HELPERS), f"expected {HELPERS} in __init__.py, found {wanted}"

    # display.py is Home Assistant free, so the real humanize() is used rather than a stub —
    # the parsed labels are what the sensors actually receive
    display_path = INIT_PATH.parent / "display.py"
    display_spec = importlib.util.spec_from_file_location("veeam_br_display", display_path)
    display = importlib.util.module_from_spec(display_spec)
    display_spec.loader.exec_module(display)

    namespace = {
        "dt_util": type("dt_util", (), {"parse_datetime": staticmethod(_parse_datetime)}),
        "timezone": timezone,
        "humanize": display.humanize,
    }
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(INIT_PATH), "exec"), namespace)
    return namespace


# Helpers the coordinator passes in, matching the real ones in async_update_data
def get_enum_value(value, default="unknown"):
    if value is None:
        return default
    return getattr(value, "value", None) or str(value)


def get_uuid_value(value):
    return str(value) if value is not None else None


def serialize_value(value):
    return value


class Enum:
    """Stands in for a generated str-enum."""

    def __init__(self, value):
        self.value = value


class Node:
    def __init__(self, **kwargs):
        self.id = "6f1f0f8a-0000-4000-8000-000000000001"
        self.name = "vbr-node-1"
        self.ip_address = "10.0.0.11"
        self.fqdn = "vbr-node-1.example.com"
        self.role = Enum("Leader")
        self.state = Enum("Running")
        self.timeline = "3"
        self.lag_mb = 0
        self.external_endpoint = None
        self.__dict__.update(kwargs)


class States:
    def __init__(self, **kwargs):
        self.is_creation_in_progress = False
        self.is_failover_in_progress = False
        self.is_removal_in_progress = False
        self.is_first_launch_after_failover = False
        self.is_cluster_endpoint_migration_in_progress = False
        self.last_online_time_utc = "2026-08-12T09:30:00.0000000Z"
        self.is_online = True
        self.is_secondary_reinit_in_progress = False
        self.is_maintenance_in_progress = False
        self.__dict__.update(kwargs)


class Cluster:
    def __init__(self, **kwargs):
        self.id = "aaaaaaaa-0000-4000-8000-000000000009"
        self.name = "VBR-HA"
        self.cluster_endpoint = "10.0.0.10"
        self.cluster_dns_name = "vbr-ha.example.com"
        self.is_cross_subnet_mode = False
        self.states = States()
        self.primary_node = Node()
        self.secondary_node = Node(
            name="vbr-node-2",
            ip_address="10.0.0.12",
            role=Enum("SyncStandby"),
            state=Enum("Streaming"),
            lag_mb=12,
        )
        self.additional_properties = {}
        self.__dict__.update(kwargs)


def parse(helpers, cluster):
    return helpers["_parse_ha_cluster"](cluster, get_enum_value, get_uuid_value, serialize_value)


def test_parses_a_healthy_cluster(helpers):
    """A clustered server should yield the fields the sensors read."""
    parsed = parse(helpers, Cluster())

    assert parsed["name"] == "VBR-HA"
    assert parsed["cluster_endpoint"] == "10.0.0.10"
    assert parsed["cluster_dns_name"] == "vbr-ha.example.com"
    assert parsed["is_online"] is True
    assert parsed["is_failover_in_progress"] is False
    # Roles are labels now, with the API value kept alongside for exact matching
    assert parsed["primary"]["role"] == "Leader"
    assert parsed["primary"]["role_raw"] == "Leader"
    assert parsed["secondary"]["role"] == "Sync standby"
    assert parsed["secondary"]["role_raw"] == "SyncStandby"
    assert parsed["secondary"]["state"] == "Streaming"
    assert parsed["secondary"]["lag_mb"] == 12


def test_last_online_time_is_timezone_aware(helpers):
    """Timestamp sensors reject naive datetimes, and the field is documented as UTC."""
    parsed = parse(helpers, Cluster())

    assert parsed["last_online_time"] == datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)
    assert parsed["last_online_time"].tzinfo is not None


def test_last_online_time_without_offset_is_stamped_utc(helpers):
    """Veeam types the field as a plain string, so an offset is not guaranteed."""
    parsed = parse(helpers, Cluster(states=States(last_online_time_utc="2026-08-12T09:30:00")))

    assert parsed["last_online_time"] == datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "raw_value",
    [None, "", "not a timestamp", 12345, object()],
    ids=["none", "empty", "unparseable", "number", "object"],
)
def test_unusable_last_online_time_is_none(helpers, raw_value):
    """A bad timestamp should not lose the whole cluster."""
    parsed = parse(helpers, Cluster(states=States(last_online_time_utc=raw_value)))

    assert parsed["last_online_time"] is None
    assert parsed["name"] == "VBR-HA", "the rest of the cluster should still parse"


def test_missing_states_leaves_flags_unknown(helpers):
    """states is optional in the schema; absent flags must not read as False."""
    parsed = parse(helpers, Cluster(states=None))

    for flag in ("is_online", "is_failover_in_progress", "is_maintenance_in_progress"):
        assert parsed[flag] is None, f"{flag} should be unknown, not False"
    assert parsed["last_online_time"] is None
    assert parsed["cluster_endpoint"] == "10.0.0.10", "configuration should still parse"


def test_unset_flags_read_as_none(helpers):
    """UNSET is not a bool, and must not be reported as one."""

    class Unset:
        pass

    parsed = parse(helpers, Cluster(states=States(is_online=Unset())))

    assert parsed["is_online"] is None


def test_missing_secondary_node_is_none(helpers):
    """A half-built cluster should still report its primary."""
    parsed = parse(helpers, Cluster(secondary_node=None))

    assert parsed["primary"]["name"] == "vbr-node-1"
    assert parsed["secondary"] is None


def test_non_string_external_endpoint_is_dropped(helpers):
    """externalEndpoint is a nullable union in the schema."""

    class Unset:
        pass

    parsed = parse(helpers, Cluster(primary_node=Node(external_endpoint=Unset())))

    assert parsed["primary"]["external_endpoint"] is None


def test_non_numeric_lag_is_dropped(helpers):
    """Lag feeds a measurement sensor, which must not receive a sentinel."""

    class Unset:
        pass

    parsed = parse(helpers, Cluster(primary_node=Node(lag_mb=Unset())))

    assert parsed["primary"]["lag_mb"] is None


def test_additional_properties_are_carried_through(helpers):
    """Fields Veeam adds later should still reach diagnostics."""
    parsed = parse(helpers, Cluster(additional_properties={"newFlag": True}))

    assert parsed["newFlag"] is True


def test_additional_properties_cannot_overwrite_parsed_fields(helpers):
    """A stray key must not clobber a field the sensors depend on."""
    parsed = parse(helpers, Cluster(additional_properties={"name": "not-the-name"}))

    assert parsed["name"] == "VBR-HA"


# ---------------------------------------------------------------------------
# Wiring: the endpoints are 1.3-rev2 only, and failover is a dangerous action
# ---------------------------------------------------------------------------


def test_cluster_fetch_is_version_guarded():
    """Older API versions do not have the endpoint; the call must be skipped, not failed."""
    content = INIT_PATH.read_text(encoding="utf-8")

    assert 'HA_CLUSTER_FEATURE = "api.high_availability_ha_cluster"' in content
    assert (
        "check_api_feature_availability(api_version, HA_CLUSTER_FEATURE)" in content
    ), "HA cluster support should be probed through the shared feature check"

    fetch = content.index("get_high_availability_cluster")
    guard = content.rindex("if ha_cluster_supported:", 0, fetch)
    assert guard < fetch, "the fetch should sit behind the support flag"


def test_unclustered_server_is_not_an_error():
    """Most servers are not clustered; that must not log a warning every poll.

    The behaviour itself — which Error means "no cluster" and which means a real failure —
    is tested in test_license_dates.py against the server's actual 400 payload. This checks
    the two are wired to the right log levels.
    """
    content = INIT_PATH.read_text(encoding="utf-8")

    marker = "No HA cluster on this server"
    assert marker in content
    line_start = content.rindex("_LOGGER.", 0, content.index(marker))
    assert content[line_start:].startswith(
        "_LOGGER.debug"
    ), "an unclustered server should be a debug message, not a warning"

    # An auth or server error arrives as the same Error model and must not be buried
    failure = "Could not read the HA cluster"
    assert failure in content
    line_start = content.rindex("_LOGGER.", 0, content.index(failure))
    assert content[line_start:].startswith(
        "_LOGGER.warning"
    ), "401/403/500 come back as an Error too, and should be reported"


def test_failover_button_is_disabled_by_default():
    """Failover is an emergency action and buttons have no confirmation step."""
    content = BUTTON_PATH.read_text(encoding="utf-8")

    failover = content.index("class VeeamHAClusterFailoverButton")
    switchover = content.index("class VeeamHAClusterSwitchoverButton")
    failover_block = content[failover:]

    assert (
        "_attr_entity_registry_enabled_default = False" in failover_block
    ), "the failover button should ship disabled"

    switchover_block = content[switchover:failover]
    assert (
        "_attr_entity_registry_enabled_default" not in switchover_block
    ), "switchover is the planned operation and should be enabled"


def test_switchover_keeps_the_lag_check():
    """Promoting a badly lagging secondary should stay the server's call to refuse."""
    content = BUTTON_PATH.read_text(encoding="utf-8")

    assert (
        "HighAvailabilitySwitchoverSpec(ignore_lag=False)" in content
    ), "switchover should not ask the server to ignore replication lag"


def test_cluster_buttons_unavailable_during_failover():
    """A second press must not pile onto a running operation."""
    content = BUTTON_PATH.read_text(encoding="utf-8")

    base = content[content.index("class VeeamHAClusterButtonBase") :]
    base = base[: base.index("class VeeamHAClusterSwitchoverButton")]

    assert "is_failover_in_progress" in base
    assert "def available" in base


def test_cluster_entities_require_reported_cluster():
    """Entities should go unavailable rather than show stale values."""
    content = SENSOR_PATH.read_text(encoding="utf-8")

    mixin = content[content.index("class VeeamHAClusterMixin") :]
    mixin = mixin[: mixin.index("class VeeamHAClusterBaseSensor")]

    assert "def available" in mixin
    assert "self._cluster() is not None" in mixin
