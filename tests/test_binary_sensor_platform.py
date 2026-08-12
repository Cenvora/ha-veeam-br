"""Tests for the binary sensor platform.

Home Assistant derives an entity domain from the platform that creates it, not from the entity
class. BinarySensorEntity subclasses added by the sensor platform therefore landed in the sensor
domain, where the binary-sensor device class wording never applies and every one of them
displayed as a raw "on"/"off" — which is what was reported.

In the binary_sensor domain the same entities read as Connected/Disconnected, OK/Problem and
Running/Not running, with no per-entity strings to maintain.
"""

from pathlib import Path
import re

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"
BINARY_PATH = COMPONENT / "binary_sensor.py"
SENSOR_PATH = COMPONENT / "sensor.py"
INIT_PATH = COMPONENT / "__init__.py"
BLUEPRINTS = Path(__file__).parent.parent / "blueprints" / "automation" / "veeam_br"


def binary_source():
    return BINARY_PATH.read_text(encoding="utf-8")


def test_the_platform_exists_and_is_registered():
    """Without the platform in PLATFORMS, nothing sets these entities up at all."""
    assert BINARY_PATH.exists()

    init = INIT_PATH.read_text(encoding="utf-8")
    assert "Platform.BINARY_SENSOR" in init


def test_no_binary_entities_are_left_on_the_sensor_platform():
    """A BinarySensorEntity created by the sensor platform is the original bug."""
    sensor = SENSOR_PATH.read_text(encoding="utf-8")

    assert "BinarySensorEntity" not in sensor
    assert "BinarySensorDeviceClass" not in sensor


def test_every_binary_sensor_moved():
    """All sixteen, not just the proxy ones that prompted the report."""
    source = binary_source()
    concrete = re.findall(r"^class (Veeam\w+)\(.*Base\):", source, re.M)

    assert len(concrete) >= 16, f"only found {concrete}"


@pytest.mark.parametrize(
    "entity,device_class",
    [
        ("VeeamProxyOnlineSensor", "CONNECTIVITY"),
        ("VeeamProxyOutOfDateSensor", "PROBLEM"),
        ("VeeamRepositoryOnlineStatusSensor", "CONNECTIVITY"),
        ("VeeamServerConnectedSensor", "CONNECTIVITY"),
        ("VeeamHAClusterOnlineSensor", "CONNECTIVITY"),
        ("VeeamHAClusterFailoverInProgressSensor", "RUNNING"),
    ],
)
def test_device_classes_supply_the_wording(entity, device_class):
    """The device class is what turns on/off into readable text, so each one needs the right
    class rather than a hand-written label."""
    source = binary_source()
    block = source[source.index(f"class {entity}(") :]
    block = block[: block.index("\n\nclass ")] if "\n\nclass " in block else block

    assert f"BinarySensorDeviceClass.{device_class}" in block


def test_enabled_has_no_device_class_and_says_why():
    """There is no "enabled/disabled" device class, so this one keeps a custom icon instead."""
    source = binary_source()
    block = source[source.index("class VeeamProxyEnabledSensor") :]
    block = block[: block.index("\n\nclass ")]

    assert "BinarySensorDeviceClass" not in block
    assert "def icon" in block


def test_old_sensor_entities_are_cleaned_up_on_upgrade():
    """The unique IDs do not change, only the domain, so both would otherwise coexist and the
    stale one would sit there unavailable forever."""
    source = binary_source()

    assert "_drop_superseded_sensor_entities" in source
    block = source[source.index("def _drop_superseded_sensor_entities") :]
    block = block[: block.index("async def async_setup_entry")]

    assert 'existing.domain != "sensor"' in block, "only sensor-domain strays should be removed"
    assert "existing.unique_id not in unique_ids" in block, "matching should be by unique ID"
    assert "registry.async_remove" in block
    assert "_LOGGER.info" in block, "an entity id changing under the user deserves a log line"


def test_the_blueprints_can_now_find_these_entities():
    """Three shipped blueprints filter on domain: binary_sensor, which matched nothing while
    the entities lived in the sensor domain."""
    filters = 0
    for path in sorted(BLUEPRINTS.glob("*.yaml")):
        filters += path.read_text(encoding="utf-8").count("domain: binary_sensor")

    assert filters >= 3, "the proxy and HA cluster blueprints depend on this domain"


def test_mixins_are_reused_rather_than_duplicated():
    """Device grouping has to match the sensor platform exactly, or a proxy shows up twice."""
    source = binary_source()

    assert "from .sensor import (" in source
    for mixin in (
        "VeeamHAClusterMixin",
        "VeeamLicenseMixin",
        "VeeamProxyMixin",
        "VeeamRepositoryMixin",
    ):
        assert mixin in source
