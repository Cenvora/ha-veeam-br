"""Tests for backup proxy and WAN accelerator support.

Proxy figures come from get_all_proxies_states, which carries both the configuration and the
live state, so one call feeds every proxy entity — on API 1.3-rev0 and newer. Older servers
have only get_all_proxies, so there the proxies are configuration only and the state entities
are not created. WAN accelerators have no states endpoint on any version, so those entities
are configuration only throughout.
"""

import importlib.util
import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"
INIT_PATH = COMPONENT / "__init__.py"
SENSOR_PATH = COMPONENT / "sensor.py"
BUTTON_PATH = COMPONENT / "button.py"
BINARY_PATH = COMPONENT / "binary_sensor.py"


def _load_display():
    spec = importlib.util.spec_from_file_location("veeam_br_display", COMPONENT / "display.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "raw,expected",
    [
        # EProxyType, as reported by a real 13.1 server
        ("GeneralPurposeProxy", "General purpose"),
        ("ViProxy", "VMware"),
        ("HvProxy", "Hyper-V"),
        ("NutanixAHV", "Nutanix AHV"),
        ("PVE", "Proxmox VE"),
    ],
)
def test_proxy_types_read_like_english(raw, expected):
    """ "ViProxy" and "PVE" tell a dashboard reader nothing on their own."""
    assert _load_display().humanize(raw) == expected


def test_proxies_are_fetched_from_the_states_endpoint():
    """get_all_proxies carries configuration only; the states endpoint carries both."""
    source = INIT_PATH.read_text(encoding="utf-8")

    assert "get_all_proxies_states" in source
    assert 'f"proxies.{proxies_endpoint}"' in source, "should be pre-imported like the others"


def test_proxy_endpoint_falls_back_when_states_is_missing():
    """The states endpoint arrived in 1.3-rev0; before that only get_all_proxies exists.

    Asking for it anyway raised ModuleNotFoundError on every poll and lost the proxy
    entities entirely on VBR 12.x (issue #104).
    """
    source = INIT_PATH.read_text(encoding="utf-8")

    assert 'PROXY_STATES_FEATURE = "api.proxies.get_all_proxies_states"' in source
    assert "check_api_feature_availability(api_version, PROXY_STATES_FEATURE)" in source
    assert '"get_all_proxies_states" if proxy_states_supported else "get_all_proxies"' in source
    assert "getattr(proxies_api, proxies_endpoint)" in source


@pytest.mark.parametrize(
    "package,expected",
    [("veeam_br.v1_2_rev1", False), ("veeam_br.v1_3_rev0", True)],
)
def test_states_endpoint_presence_matches_what_the_fallback_assumes(package, expected):
    """The fallback is only correct if 1.2-rev1 really lacks the endpoint and 1.3 has it."""
    pytest.importorskip(package, reason="veeam-br not installed")

    found = importlib.util.find_spec(f"{package}.api.proxies.get_all_proxies_states")
    assert (found is not None) is expected


def test_proxy_state_flags_are_read_as_booleans_or_unknown():
    """A missing flag must not read as False, which would claim a proxy is online."""
    source = INIT_PATH.read_text(encoding="utf-8")
    block = source[source.index("proxies_list = []") :]
    block = block[: block.index("wan_accelerators_list = []")]

    for flag in ("is_online", "is_disabled", "is_out_of_date"):
        assert f'_bool_or_none(proxy, "{flag}")' in block, f"{flag} should go through the guard"


def test_proxies_without_an_id_are_skipped():
    """Entity unique IDs are built from it, as with jobs and repositories."""
    source = INIT_PATH.read_text(encoding="utf-8")
    block = source[source.index("proxies_list = []") :]
    block = block[: block.index("wan_accelerators_list = []")]

    assert "no usable ID" in block


def test_proxy_failures_do_not_take_down_the_refresh():
    """A proxy endpoint error should cost the proxy entities, not everything else."""
    source = INIT_PATH.read_text(encoding="utf-8")
    block = source[source.index("proxies_list = []") :]
    block = block[: block.index('_LOGGER.debug("Total proxies')]

    assert "Failed to parse proxies" in block
    assert "Failed to fetch proxies" in block


def test_wan_accelerator_optional_fields_are_guarded():
    """server and cache are both optional in the schema, and UNSET is not None."""
    source = INIT_PATH.read_text(encoding="utf-8")
    block = source[source.index("wan_accelerators_list = []") :]
    block = block[: block.index("# Fetch High Availability cluster")]

    assert "has_server" in block and "has_cache" in block
    assert "_is_unset(server)" in block and "_is_unset(cache)" in block


def test_both_kinds_reach_the_coordinator_payload():
    source = INIT_PATH.read_text(encoding="utf-8")

    assert '"proxies": proxies_list' in source
    assert '"wan_accelerators": wan_accelerators_list' in source


def test_entities_are_gated_on_the_endpoint_existing():
    """Neither endpoint is present in every API revision the library ships.

    The proxy binary sensors read state fields that only the states endpoint returns, and
    the enable/disable buttons call operations that arrived with it, so both are gated on
    that endpoint rather than on the api.proxies namespace — which exists further back and
    would leave three permanently unknown sensors and two failing buttons (issue #104).
    """
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    binary_source = BINARY_PATH.read_text(encoding="utf-8")
    button_source = BUTTON_PATH.read_text(encoding="utf-8")

    assert (
        'check_api_feature_availability(api_version, "api.proxies.get_all_proxies_states")'
        in binary_source
    )
    assert (
        'check_api_feature_availability(api_version, "api.proxies.enable_proxy")'
        in button_source
    )
    assert 'check_api_feature_availability(api_version, "api.wan_accelerators")' in sensor_source


def test_proxy_enabled_is_reported_the_healthy_way_round():
    """The API reports isDisabled; a sensor called "Disabled" would show red when fine."""
    binary_source = BINARY_PATH.read_text(encoding="utf-8")
    block = binary_source[binary_source.index("class VeeamProxyEnabledSensor") :]
    block = block[: block.index("class VeeamProxyOutOfDate")]

    assert 'self._attr_name = "Enabled"' in block
    assert 'return not proxy["is_disabled"]' in block
    assert 'proxy.get("is_disabled") is None' in block, "unknown must stay unknown, not True"


def test_proxy_buttons_exist_for_both_directions():
    button_source = BUTTON_PATH.read_text(encoding="utf-8")

    assert "class VeeamProxyEnableButton" in button_source
    assert "class VeeamProxyDisableButton" in button_source
    assert "enable_proxy" in button_source and "disable_proxy" in button_source


def test_proxy_buttons_are_config_entities():
    """They change infrastructure state, so they belong with the other controls."""
    button_source = BUTTON_PATH.read_text(encoding="utf-8")
    block = button_source[button_source.index("class VeeamProxyButtonBase") :]
    block = block[: block.index("class VeeamProxyEnableButton")]

    assert "EntityCategory.CONFIG" in block


def test_proxy_entities_share_one_device_per_proxy():
    """Every platform must agree on the identifier or they split into separate devices."""
    sources = [
        SENSOR_PATH.read_text(encoding="utf-8"),
        BUTTON_PATH.read_text(encoding="utf-8"),
    ]

    identifier = 'f"proxy_{self._proxy_id}"'
    for source in sources:
        assert identifier in source

    for source in sources:
        block = source[source.index(identifier) - 400 : source.index(identifier) + 400]
        assert '"model": "Backup Proxy"' in block


def test_new_device_kinds_can_be_pruned_and_deleted():
    """A deleted proxy should be removable like a deleted repository."""
    source = INIT_PATH.read_text(encoding="utf-8")

    assert '"proxy_": "proxies"' in source
    assert '"wan_": "wan_accelerators"' in source


def test_feature_map_documents_the_new_entities():
    source = (COMPONENT / "const.py").read_text(encoding="utf-8")

    for key in ("proxy_data", "proxy_enable_button", "wan_accelerator_data"):
        assert key in source


def test_wan_cache_unit_comes_from_the_api():
    """Veeam reports the unit next to the number rather than fixing it to one."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    block = sensor_source[sensor_source.index("class VeeamWanAcceleratorCacheSensor") :]

    assert "def native_unit_of_measurement" in block
    assert 'accelerator.get("cache_size_unit")' in block


def test_the_real_payload_shape_is_covered():
    """Shapes taken from a real get_all_proxies response, including the nested server block."""
    payload = json.loads("""
        {"data": [
          {"server": {"hostId": "6745a759-2205-4cd2-b172-8ec8f7e60ef8",
                      "hostName": "This server", "maxTaskCount": 2},
           "type": "GeneralPurposeProxy",
           "id": "50cbf622-129e-482a-9197-d67e5bb1fb1f",
           "name": "Backup Proxy", "description": "Created by Veeam Backup & Replication"},
          {"server": {"transportMode": "Auto", "hostId": "6745a759-2205-4cd2-b172-8ec8f7e60ef8",
                      "hostName": "This server", "maxTaskCount": 8},
           "type": "ViProxy",
           "id": "18b661c1-d9dc-4233-90a0-7e7b10dc2d09",
           "name": "VMware Backup Proxy", "description": "Created by Veeam Backup & Replication"}
        ]}
        """)
    humanize = _load_display().humanize

    labels = [humanize(item["type"]) for item in payload["data"]]
    assert labels == ["General purpose", "VMware"]

    # Both proxies sit on the same host, so the device name has to come from the proxy name
    hosts = {item["server"]["hostName"] for item in payload["data"]}
    assert len(hosts) == 1
    assert len({item["name"] for item in payload["data"]}) == 2
