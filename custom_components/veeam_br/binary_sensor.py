"""Binary sensors for Veeam Backup & Replication.

These live in their own platform rather than alongside the sensors on purpose. Home Assistant
derives an entity domain from the platform that creates it, so a BinarySensorEntity added by the
sensor platform lands in the sensor domain — where the binary-sensor device class wording never
applies and every state displays as a raw "on"/"off". In the binary_sensor domain the same
entities read as Connected/Disconnected, OK/Problem and Running/Not running.

Entities that existed under the sensor domain are removed as their replacements are created, so
one upgrade moves them rather than leaving two of everything.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, check_api_feature_availability, configured_api_version
from .sensor import (
    VeeamHAClusterMixin,
    VeeamLicenseMixin,
    VeeamProxyMixin,
    VeeamRepositoryMixin,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


def _drop_superseded_sensor_entities(hass: HomeAssistant, entry: ConfigEntry, entities) -> None:
    """Remove the sensor-domain entities these binary sensors replace.

    Matching is by unique ID, which is unchanged — only the domain moves. Without this an
    upgrade would leave the old sensor.* entities behind as unavailable strays, since nothing
    provides them any more.
    """
    registry = er.async_get(hass)
    unique_ids = {entity.unique_id for entity in entities if entity.unique_id}

    for existing in list(er.async_entries_for_config_entry(registry, entry.entry_id)):
        if existing.domain != "sensor" or existing.unique_id not in unique_ids:
            continue
        _LOGGER.info(
            "Replacing %s with its binary_sensor equivalent; update any automation or "
            "dashboard that referenced it",
            existing.entity_id,
        )
        registry.async_remove(existing.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Veeam binary sensors."""
    coordinator = entry.runtime_data["coordinator"]

    added_repository_ids: set[str] = set()
    added_proxy_ids: set[str] = set()
    server_added = False
    license_added = False
    ha_cluster_added = False

    @callback
    def _sync_entities() -> None:
        nonlocal server_added
        nonlocal license_added
        nonlocal ha_cluster_added

        if not coordinator.data:
            return

        api_version = configured_api_version(entry)
        new_entities: list[BinarySensorEntity] = []

        if check_api_feature_availability(api_version, "api.repositories"):
            for repository in coordinator.data.get("repositories", []):
                repo_id = repository.get("id")
                if not repo_id or repo_id in added_repository_ids:
                    continue

                new_entities.extend(
                    [
                        VeeamRepositoryOnlineStatusSensor(coordinator, entry, repository),
                        VeeamRepositoryOutOfDateSensor(coordinator, entry, repository),
                        VeeamRepositoryImmutableSensor(coordinator, entry, repository),
                        VeeamRepositoryAccessibleSensor(coordinator, entry, repository),
                        VeeamRepositoryCapacityWarningSensor(coordinator, entry, repository),
                        VeeamRepositoryCapacityCriticalSensor(coordinator, entry, repository),
                    ]
                )
                added_repository_ids.add(repo_id)

        if check_api_feature_availability(api_version, "api.proxies"):
            for proxy in coordinator.data.get("proxies", []):
                proxy_id = proxy.get("id")
                if not proxy_id or proxy_id in added_proxy_ids:
                    continue

                new_entities.extend(
                    [
                        VeeamProxyOnlineSensor(coordinator, entry, proxy),
                        VeeamProxyEnabledSensor(coordinator, entry, proxy),
                        VeeamProxyOutOfDateSensor(coordinator, entry, proxy),
                    ]
                )
                added_proxy_ids.add(proxy_id)

        if (
            not server_added
            and coordinator.data.get("server_info")
            and check_api_feature_availability(api_version, "api.service")
        ):
            new_entities.extend(
                [
                    VeeamServerHealthOkSensor(coordinator, entry),
                    VeeamServerConnectedSensor(coordinator, entry),
                ]
            )
            server_added = True

        if (
            not license_added
            and coordinator.data.get("license_info")
            and check_api_feature_availability(api_version, "api.license_")
        ):
            new_entities.extend(
                [
                    VeeamLicenseAutoUpdateSensor(coordinator, entry),
                    VeeamLicenseCloudConnectSensor(coordinator, entry),
                ]
            )
            license_added = True

        if (
            not ha_cluster_added
            and coordinator.data.get("ha_cluster")
            and check_api_feature_availability(api_version, "api.high_availability_ha_cluster")
        ):
            new_entities.extend(
                [
                    VeeamHAClusterOnlineSensor(coordinator, entry),
                    VeeamHAClusterFailoverInProgressSensor(coordinator, entry),
                    VeeamHAClusterMaintenanceSensor(coordinator, entry),
                ]
            )
            ha_cluster_added = True

        if new_entities:
            _drop_superseded_sensor_entities(hass, entry, new_entities)
            _LOGGER.debug("Adding %d Veeam binary sensors", len(new_entities))
            async_add_entities(new_entities)

    _sync_entities()
    coordinator.async_add_listener(_sync_entities)


class VeeamServerBinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """Base class for Veeam Server binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry

    def _server_info(self) -> dict[str, Any] | None:
        return self.coordinator.data.get("server_info") if self.coordinator.data else None

    @property
    def device_info(self):
        """Return device info for the Veeam server."""
        server_info = self._server_info()
        # Fall back to the configured host rather than a bare "Unknown": when server info
        # fails to fetch, every entry's device would otherwise carry the same name (#82)
        host = self._config_entry.data.get(CONF_HOST, "Unknown")
        server_name = (server_info.get("name") if server_info else None) or host
        return {
            "identifiers": {(DOMAIN, f"server_{self._config_entry.entry_id}")},
            "name": f"{server_name}",
            "manufacturer": "Veeam",
            "model": "Backup & Replication Server",
        }


class VeeamLicenseBinarySensorBase(VeeamLicenseMixin, CoordinatorEntity, BinarySensorEntity):
    """Base class for Veeam License binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamLicenseMixin.__init__(self, coordinator, config_entry)


class VeeamRepositoryBinarySensorBase(VeeamRepositoryMixin, CoordinatorEntity, BinarySensorEntity):
    """Base class for Veeam Repository binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, repository_data):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamRepositoryMixin.__init__(self, coordinator, config_entry, repository_data)

    def _get_free_space_percent(self) -> float | None:
        """Calculate free space percentage for the repository.

        Free space is calculated as (capacity - used) / capacity * 100
        to get the actual available space percentage.
        """
        repo = self._repository()
        if not repo:
            return None
        capacity = repo.get("capacity_gb")
        used = repo.get("used_space_gb")
        if capacity and capacity > 0 and used is not None:
            free = capacity - used
            return (free / capacity) * 100
        return None


class VeeamHAClusterBinarySensorBase(VeeamHAClusterMixin, CoordinatorEntity, BinarySensorEntity):
    """Base class for HA cluster binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamHAClusterMixin.__init__(self, coordinator, config_entry)


class VeeamProxyBinarySensorBase(VeeamProxyMixin, CoordinatorEntity, BinarySensorEntity):
    """Base class for proxy binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, proxy_data):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamProxyMixin.__init__(self, coordinator, config_entry, proxy_data)


class VeeamServerHealthOkSensor(VeeamServerBinarySensorBase):
    """Binary sensor for Veeam Server Health."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_health_ok"
        self._attr_name = "Health OK"

    @property
    def is_on(self) -> bool | None:
        # Health reflects the current update status
        return self.coordinator.last_update_success

    @property
    def icon(self) -> str:
        return "mdi:heart-pulse" if self.is_on else "mdi:heart-off"


class VeeamServerConnectedSensor(VeeamServerBinarySensorBase):
    """Binary sensor for Veeam Server Connection Status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_connected"
        self._attr_name = "Connected"

    @property
    def is_on(self) -> bool | None:
        # Connection status reflects the current update status
        return self.coordinator.last_update_success

    @property
    def icon(self) -> str:
        return "mdi:lan-connect" if self.is_on else "mdi:lan-disconnect"


# ===========================
# LICENSE SENSORS (single device)
# ===========================


class VeeamLicenseAutoUpdateSensor(VeeamLicenseBinarySensorBase):
    """Binary sensor for Veeam License Auto Update."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_auto_update"
        self._attr_name = "Auto Update Enabled"

    @property
    def is_on(self) -> bool | None:
        license_info = self._license_info()
        if not license_info:
            return None
        value = license_info.get("auto_update_enabled")
        if value is None:
            return None
        return bool(value)

    @property
    def icon(self) -> str:
        return "mdi:update"


class VeeamLicenseCloudConnectSensor(VeeamLicenseBinarySensorBase):
    """Binary sensor for Veeam License Cloud Connect."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_cloud_connect"
        self._attr_name = "Cloud Connect Enabled"

    @property
    def is_on(self) -> bool | None:
        license_info = self._license_info()
        if not license_info:
            return None
        cloud_connect = license_info.get("cloud_connect")
        if cloud_connect is None:
            return None
        # cloud_connect is an enum string (e.g., "Enabled", "Disabled"), not a boolean
        return str(cloud_connect).lower() == "enabled"

    @property
    def icon(self) -> str:
        return "mdi:cloud-check" if self.is_on else "mdi:cloud-off"


# ===========================
# REPOSITORY SENSORS (device per repository)
# ===========================


class VeeamRepositoryOnlineStatusSensor(VeeamRepositoryBinarySensorBase):
    """Binary sensor for Veeam Repository Online Status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_online"
        self._attr_name = "Online"

    @property
    def is_on(self) -> bool | None:
        repo = self._repository()
        if not repo:
            return None
        value = repo.get("is_online")
        if value is None:
            return None
        return bool(value)

    @property
    def icon(self) -> str:
        return "mdi:check-network" if self.is_on else "mdi:close-network"


class VeeamRepositoryOutOfDateSensor(VeeamRepositoryBinarySensorBase):
    """Binary sensor for Veeam Repository Out of Date Status."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_out_of_date"
        self._attr_name = "Out of Date"

    @property
    def is_on(self) -> bool | None:
        repo = self._repository()
        if not repo:
            return None
        value = repo.get("is_out_of_date")
        if value is None:
            return None
        return bool(value)

    @property
    def icon(self) -> str:
        return "mdi:alert-octagon" if self.is_on else "mdi:check-decagram"


class VeeamRepositoryImmutableSensor(VeeamRepositoryBinarySensorBase):
    """Binary sensor for Veeam Repository Immutability."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_immutable"
        self._attr_name = "Immutable"

    @property
    def is_on(self) -> bool | None:
        repo = self._repository()
        if not repo:
            return None
        value = repo.get("is_immutable")
        if value is None:
            return None
        return bool(value)

    @property
    def icon(self) -> str:
        return "mdi:lock" if self.is_on else "mdi:lock-open"


class VeeamRepositoryAccessibleSensor(VeeamRepositoryBinarySensorBase):
    """Binary sensor for Veeam Repository Accessible status."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_accessible"
        self._attr_name = "Accessible"

    @property
    def is_on(self) -> bool | None:
        repo = self._repository()
        if not repo:
            return None
        value = repo.get("is_accessible")
        if value is None:
            return None
        return bool(value)

    @property
    def icon(self) -> str:
        return "mdi:folder-open" if self.is_on else "mdi:folder-lock"


class VeeamRepositoryCapacityWarningSensor(VeeamRepositoryBinarySensorBase):
    """Binary sensor for Veeam Repository Capacity Warning (< 15% free)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_repository_{self._repo_id}_capacity_warning"
        )
        self._attr_name = "Capacity Warning"

    @property
    def is_on(self) -> bool | None:
        free_percent = self._get_free_space_percent()
        if free_percent is None:
            return None
        return free_percent < 15

    @property
    def icon(self) -> str:
        return "mdi:alert" if self.is_on else "mdi:check-circle"


class VeeamRepositoryCapacityCriticalSensor(VeeamRepositoryBinarySensorBase):
    """Binary sensor for Veeam Repository Capacity Critical (< 5% free)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_repository_{self._repo_id}_capacity_critical"
        )
        self._attr_name = "Capacity Critical"

    @property
    def is_on(self) -> bool | None:
        free_percent = self._get_free_space_percent()
        if free_percent is None:
            return None
        return free_percent < 5

    @property
    def icon(self) -> str:
        return "mdi:alert-circle" if self.is_on else "mdi:check-circle"


# ===========================
# SOBR SENSORS (device per SOBR)
# ===========================


class VeeamHAClusterOnlineSensor(VeeamHAClusterBinarySensorBase):
    """Binary sensor for whether the HA cluster is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_ha_cluster_online"
        self._attr_name = "Online"

    @property
    def is_on(self) -> bool | None:
        cluster = self._cluster()
        return cluster.get("is_online") if cluster else None


class VeeamHAClusterFailoverInProgressSensor(VeeamHAClusterBinarySensorBase):
    """Binary sensor for an in-progress failover."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_ha_cluster_failover_in_progress"
        self._attr_name = "Failover In Progress"

    @property
    def is_on(self) -> bool | None:
        cluster = self._cluster()
        return cluster.get("is_failover_in_progress") if cluster else None

    @property
    def icon(self) -> str:
        return "mdi:swap-horizontal-bold" if self.is_on else "mdi:swap-horizontal"


class VeeamHAClusterMaintenanceSensor(VeeamHAClusterBinarySensorBase):
    """Binary sensor for cluster maintenance mode."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_ha_cluster_maintenance"
        self._attr_name = "Maintenance In Progress"

    @property
    def is_on(self) -> bool | None:
        cluster = self._cluster()
        return cluster.get("is_maintenance_in_progress") if cluster else None

    @property
    def icon(self) -> str:
        return "mdi:wrench-clock" if self.is_on else "mdi:wrench"


class VeeamProxyOnlineSensor(VeeamProxyBinarySensorBase):
    """Binary sensor for whether a proxy is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, config_entry, proxy_data):
        super().__init__(coordinator, config_entry, proxy_data)
        self._attr_unique_id = f"{config_entry.entry_id}_proxy_{self._proxy_id}_online"
        self._attr_name = "Online"

    @property
    def is_on(self) -> bool | None:
        proxy = self._proxy()
        return proxy.get("is_online") if proxy else None


class VeeamProxyEnabledSensor(VeeamProxyBinarySensorBase):
    """Binary sensor for whether a proxy is enabled.

    Reported as enabled rather than disabled, so "on" is the healthy state and the tile colour
    matches every other sensor here.
    """

    def __init__(self, coordinator, config_entry, proxy_data):
        super().__init__(coordinator, config_entry, proxy_data)
        self._attr_unique_id = f"{config_entry.entry_id}_proxy_{self._proxy_id}_enabled"
        self._attr_name = "Enabled"

    @property
    def is_on(self) -> bool | None:
        proxy = self._proxy()
        if not proxy or proxy.get("is_disabled") is None:
            return None
        return not proxy["is_disabled"]

    @property
    def icon(self) -> str:
        return "mdi:play-circle" if self.is_on else "mdi:pause-circle"


class VeeamProxyOutOfDateSensor(VeeamProxyBinarySensorBase):
    """Binary sensor for a proxy running outdated components."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, config_entry, proxy_data):
        super().__init__(coordinator, config_entry, proxy_data)
        self._attr_unique_id = f"{config_entry.entry_id}_proxy_{self._proxy_id}_out_of_date"
        self._attr_name = "Out Of Date"

    @property
    def is_on(self) -> bool | None:
        proxy = self._proxy()
        return proxy.get("is_out_of_date") if proxy else None
