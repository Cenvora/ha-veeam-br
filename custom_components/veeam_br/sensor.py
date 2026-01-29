"""Support for Veeam Backup & Replication sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    added_job_ids: set[str] = set()
    server_added = False
    license_added = False

    @callback
    def _sync_entities() -> None:
        nonlocal server_added
        nonlocal license_added

        if not coordinator.data:
            return

        new_entities = []

        # ---- JOB SENSORS (dynamic) ----
        for job in coordinator.data.get("jobs", []):
            job_id = job.get("id")
            if not job_id or job_id in added_job_ids:
                continue

            new_entities.append(VeeamJobSensor(coordinator, entry, job))
            added_job_ids.add(job_id)

        # ---- SERVER SENSOR (once) ----
        if not server_added and coordinator.data.get("server_info"):
            new_entities.append(VeeamServerInfoSensor(coordinator, entry))
            server_added = True

        # ---- LICENSE SENSOR (once) ----
        if not license_added and coordinator.data.get("license_info"):
            new_entities.append(VeeamLicenseSensor(coordinator, entry))
            license_added = True

        if new_entities:
            _LOGGER.debug("Adding %d Veeam sensors", len(new_entities))
            async_add_entities(new_entities)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


class VeeamJobSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam Backup Job sensor."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")

        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}"
        self._attr_name = f"Veeam {self._job_name}"

    def _job(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        for job in self.coordinator.data.get("jobs", []):
            if job.get("id") == self._job_id:
                return job
        return None

    @property
    def native_value(self) -> str | None:
        job = self._job()
        return job.get("status") if job else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._job() or {}

    @property
    def icon(self) -> str:
        state = self.native_value
        if state == "running":
            return "mdi:backup-restore"
        if state == "success":
            return "mdi:check-circle"
        if state == "warning":
            return "mdi:alert"
        if state == "failed":
            return "mdi:close-circle"
        return "mdi:cloud-sync"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": f"Veeam B&R ({self._config_entry.data.get('host')})",
            "manufacturer": "Veeam",
            "model": "Backup & Replication",
        }


class VeeamServerInfoSensor(CoordinatorEntity, SensorEntity):
    """Representation of the Veeam Backup Server Info sensor."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_server_info"
        self._attr_name = "Veeam Server Info"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data.get("server_info") if self.coordinator.data else None
        return data.get("build_version") if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self.coordinator.data.get("server_info") if self.coordinator.data else {}

    @property
    def icon(self) -> str:
        return "mdi:server"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": f"Veeam B&R ({self._config_entry.data.get('host')})",
            "manufacturer": "Veeam",
            "model": "Backup & Replication",
        }


class VeeamLicenseSensor(CoordinatorEntity, SensorEntity):
    """Representation of the Veeam Backup License sensor."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_license"
        self._attr_name = "Veeam License"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        license_info = self.coordinator.data.get("license_info")
        if not license_info:
            return None
        return license_info.get("status")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self.coordinator.data:
            return {}
        license_info = self.coordinator.data.get("license_info")
        return license_info if license_info else {}

    @property
    def icon(self) -> str:
        state = self.native_value
        if state and state.lower() == "valid":
            return "mdi:license"
        if state and state.lower() == "expired":
            return "mdi:license-off"
        return "mdi:license"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": f"Veeam B&R ({self._config_entry.data.get('host')})",
            "manufacturer": "Veeam",
            "model": "Backup & Replication",
        }
