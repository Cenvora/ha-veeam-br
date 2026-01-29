"""Support for Veeam Backup & Replication sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup & Replication sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Create sensors for each backup job and server info
    entities = []
    if coordinator.data:
        # Add job sensors
        jobs = coordinator.data.get("jobs", [])
        for job in jobs:
            entities.append(VeeamJobSensor(coordinator, entry, job))

        # Add server info sensor if available
        server_info = coordinator.data.get("server_info")
        if server_info:
            entities.append(VeeamServerInfoSensor(coordinator, entry, server_info))

    async_add_entities(entities)


class VeeamJobSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam Backup Job sensor."""

    def __init__(self, coordinator, config_entry, job_data):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id", job_data.get("name"))
        self._job_name = job_data.get("name", "Unknown Job")

        # Set unique ID
        self._attr_unique_id = f"{config_entry.entry_id}_{self._job_id}"
        self._attr_name = f"Veeam {self._job_name}"

    def _find_job_data(self) -> dict[str, Any] | None:
        """Find the job data for this sensor from coordinator data."""
        if not self.coordinator.data:
            return None

        jobs = self.coordinator.data.get("jobs", [])
        for job in jobs:
            job_id = job.get("id", job.get("name"))
            if job_id == self._job_id:
                return job

        return None

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        job_data = self._find_job_data()
        if job_data:
            return job_data.get("status", "unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        job_data = self._find_job_data()
        if job_data:
            return {
                "job_id": job_data.get("id"),
                "job_name": job_data.get("name"),
                "job_type": job_data.get("type"),
                "last_run": job_data.get("last_run"),
                "next_run": job_data.get("next_run"),
                "last_result": job_data.get("last_result"),
            }

        return {}

    @property
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        state = self.native_value
        if state == "running":
            return "mdi:backup-restore"
        elif state == "success":
            return "mdi:check-circle"
        elif state == "warning":
            return "mdi:alert"
        elif state == "failed":
            return "mdi:close-circle"
        return "mdi:cloud-sync"

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": f"Veeam BR ({self._config_entry.data.get('host', 'Unknown')})",
            "manufacturer": "Veeam",
            "model": "Backup & Replication",
        }


class VeeamServerInfoSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Veeam Backup Server Info sensor."""

    def __init__(self, coordinator, config_entry, server_info):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry

        # Set unique ID
        self._attr_unique_id = f"{config_entry.entry_id}_server_info"
        self._attr_name = "Veeam Server Info"

    def _find_server_info(self) -> dict[str, Any] | None:
        """Find the server info from coordinator data."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("server_info")

    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        server_info = self._find_server_info()
        if server_info:
            return server_info.get("build_version", "Unknown")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        server_info = self._find_server_info()
        if server_info:
            return {
                "vbr_id": server_info.get("vbr_id"),
                "server_name": server_info.get("name"),
                "patches": server_info.get("patches", []),
                "database_vendor": server_info.get("database_vendor"),
                "sql_server_edition": server_info.get("sql_server_edition"),
                "sql_server_version": server_info.get("sql_server_version"),
                "database_schema_version": server_info.get("database_schema_version"),
                "database_content_version": server_info.get("database_content_version"),
                "platform": server_info.get("platform"),
            }
        return {}

    @property
    def icon(self) -> str:
        """Return the icon to use in the frontend."""
        return "mdi:server"

    @property
    def device_info(self):
        """Return device information about this entity."""
        return {
            "identifiers": {(DOMAIN, self._config_entry.entry_id)},
            "name": f"Veeam B&R ({self._config_entry.data.get('host', 'Unknown')})",
            "manufacturer": "Veeam",
            "model": "Backup & Replication",
        }
