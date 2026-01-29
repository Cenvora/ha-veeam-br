"""Support for Veeam Backup & Replication sensors."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
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

    # Create sensors for each backup job
    entities = []
    if coordinator.data:
        for job in coordinator.data:
            entities.append(VeeamJobSensor(coordinator, entry, job))

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
        
    @property
    def native_value(self) -> str | None:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None
            
        # Find this job in the coordinator data
        for job in self.coordinator.data:
            job_id = job.get("id", job.get("name"))
            if job_id == self._job_id:
                return job.get("status", "unknown")
        
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}
            
        # Find this job in the coordinator data
        for job in self.coordinator.data:
            job_id = job.get("id", job.get("name"))
            if job_id == self._job_id:
                return {
                    "job_id": job.get("id"),
                    "job_name": job.get("name"),
                    "job_type": job.get("type"),
                    "last_run": job.get("last_run"),
                    "next_run": job.get("next_run"),
                    "last_result": job.get("last_result"),
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
            "name": f"Veeam BR ({self._config_entry.data['host']})",
            "manufacturer": "Veeam",
            "model": "Backup & Replication",
        }
