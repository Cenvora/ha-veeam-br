"""Support for Veeam Backup & Replication sensors."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    added_repository_ids: set[str] = set()
    server_added = False
    license_added = False

    @callback
    def _sync_entities() -> None:
        nonlocal server_added
        nonlocal license_added

        if not coordinator.data:
            return

        new_entities = []

        # ---- JOB SENSORS (dynamic) - Each job becomes a device with multiple sensors ----
        for job in coordinator.data.get("jobs", []):
            job_id = job.get("id")
            if not job_id or job_id in added_job_ids:
                continue

            # Create sensors for each job attribute
            new_entities.extend(
                [
                    VeeamJobStatusSensor(coordinator, entry, job),
                    VeeamJobTypeSensor(coordinator, entry, job),
                    VeeamJobLastRunSensor(coordinator, entry, job),
                    VeeamJobNextRunSensor(coordinator, entry, job),
                ]
            )
            added_job_ids.add(job_id)

        # ---- REPOSITORY SENSORS (dynamic) - Each repository becomes a device with multiple sensors ----
        for repository in coordinator.data.get("repositories", []):
            repo_id = repository.get("id")
            if not repo_id or repo_id in added_repository_ids:
                continue

            # Create sensors for each repository attribute
            new_entities.extend(
                [
                    VeeamRepositoryTypeSensor(coordinator, entry, repository),
                    VeeamRepositoryDescriptionSensor(coordinator, entry, repository),
                    VeeamRepositoryCapacitySensor(coordinator, entry, repository),
                    VeeamRepositoryFreeSpaceSensor(coordinator, entry, repository),
                    VeeamRepositoryUsedSpaceSensor(coordinator, entry, repository),
                    VeeamRepositoryUsedSpacePercentSensor(coordinator, entry, repository),
                    VeeamRepositoryOnlineStatusSensor(coordinator, entry, repository),
                    VeeamRepositoryOutOfDateSensor(coordinator, entry, repository),
                ]
            )
            added_repository_ids.add(repo_id)
            _LOGGER.debug(
                "Adding repository sensors for: %s (id: %s)",
                repository.get("name"),
                repo_id,
            )

        # ---- SERVER SENSORS (once) - Server info becomes a device with multiple sensors ----
        if not server_added and coordinator.data.get("server_info"):
            new_entities.extend(
                [
                    VeeamServerBuildVersionSensor(coordinator, entry),
                    VeeamServerNameSensor(coordinator, entry),
                    VeeamServerPlatformSensor(coordinator, entry),
                    VeeamServerDatabaseVendorSensor(coordinator, entry),
                    VeeamServerSQLEditionSensor(coordinator, entry),
                    VeeamServerSQLVersionSensor(coordinator, entry),
                ]
            )
            server_added = True

        # ---- LICENSE SENSORS (once) - License becomes a device with multiple sensors ----
        if not license_added and coordinator.data.get("license_info"):
            new_entities.extend(
                [
                    VeeamLicenseStatusSensor(coordinator, entry),
                    VeeamLicenseEditionSensor(coordinator, entry),
                    VeeamLicenseTypeSensor(coordinator, entry),
                    VeeamLicenseExpirationSensor(coordinator, entry),
                    VeeamLicenseSupportExpirationSensor(coordinator, entry),
                    VeeamLicenseLicensedToSensor(coordinator, entry),
                    VeeamLicenseSupportIDSensor(coordinator, entry),
                    VeeamLicenseAutoUpdateSensor(coordinator, entry),
                    VeeamLicenseCloudConnectSensor(coordinator, entry),
                ]
            )
            license_added = True

        if new_entities:
            _LOGGER.debug("Adding %d Veeam sensors", len(new_entities))
            async_add_entities(new_entities)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


# ===========================
# MIXINS (shared logic for base classes)
# ===========================


class VeeamLicenseMixin:
    """Mixin providing shared license-related functionality."""

    def __init__(self, coordinator, config_entry):
        """Initialize the mixin."""
        self._config_entry = config_entry

    def _license_info(self) -> dict[str, Any] | None:
        """Get license info from coordinator data."""
        return self.coordinator.data.get("license_info") if self.coordinator.data else None

    @property
    def device_info(self):
        """Return device info for the Veeam license."""
        return {
            "identifiers": {(DOMAIN, f"license_{self._config_entry.entry_id}")},
            "name": "Veeam License",
            "manufacturer": "Veeam",
            "model": "License",
        }


class VeeamRepositoryMixin:
    """Mixin providing shared repository-related functionality."""

    def __init__(self, coordinator, config_entry, repository_data):
        """Initialize the mixin."""
        self._config_entry = config_entry
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")

    def _repository(self) -> dict[str, Any] | None:
        """Get repository data from coordinator."""
        if not self.coordinator.data:
            return None
        for repo in self.coordinator.data.get("repositories", []):
            if repo.get("id") == self._repo_id:
                return repo
        return None

    @property
    def device_info(self):
        """Return device info for this repository."""
        return {
            "identifiers": {(DOMAIN, f"repository_{self._repo_id}")},
            "name": f"{self._repo_name}",
            "manufacturer": "Veeam",
            "model": "Backup Repository",
        }


# ===========================
# JOB SENSORS (device per job)
# ===========================


class VeeamJobBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Veeam Job sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")

    def _job(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        for job in self.coordinator.data.get("jobs", []):
            if job.get("id") == self._job_id:
                return job
        return None

    @property
    def device_info(self):
        """Return device info for this job."""
        return {
            "identifiers": {(DOMAIN, f"job_{self._job_id}")},
            "name": f"{self._job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Job",
        }


class VeeamJobStatusSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Status."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        job = self._job()
        if not job:
            return None
        status = job.get("status", "").lower()
        if status in ("running", "starting"):
            return "running"
        last_result = job.get("last_result", "").lower()
        return last_result if last_result else "unknown"

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


class VeeamJobTypeSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Type."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_type"
        self._attr_name = "Type"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        job = self._job()
        return job.get("type") if job else None

    @property
    def icon(self) -> str:
        return "mdi:file-tree"


class VeeamJobLastRunSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Last Run."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_last_run"
        self._attr_name = "Last Run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        job = self._job()
        return job.get("last_run") if job else None

    @property
    def icon(self) -> str:
        return "mdi:clock-start"


class VeeamJobNextRunSensor(VeeamJobBaseSensor):
    """Sensor for Veeam Job Next Run."""

    def __init__(self, coordinator, config_entry, job_data):
        super().__init__(coordinator, config_entry, job_data)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_next_run"
        self._attr_name = "Next Run"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        job = self._job()
        return job.get("next_run") if job else None

    @property
    def icon(self) -> str:
        return "mdi:clock-end"


# ===========================
# SERVER INFO SENSORS (single device)
# ===========================


class VeeamServerBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Veeam Server Info sensors."""

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
        server_name = server_info.get("name", "Unknown") if server_info else "Unknown"
        return {
            "identifiers": {(DOMAIN, f"server_{self._config_entry.entry_id}")},
            "name": f"{server_name}",
            "manufacturer": "Veeam",
            "model": "Backup & Replication Server",
        }


class VeeamServerBuildVersionSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Build Version."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_build_version"
        self._attr_name = "Build Version"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("build_version") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:tag"


class VeeamServerNameSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Name."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_name"
        self._attr_name = "Server Name"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("name") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:server"


class VeeamServerPlatformSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Platform."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_platform"
        self._attr_name = "Platform"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("platform") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:desktop-tower"


class VeeamServerDatabaseVendorSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server Database Vendor."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_database_vendor"
        self._attr_name = "Database Vendor"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("database_vendor") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:database"


class VeeamServerSQLEditionSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server SQL Edition."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_sql_edition"
        self._attr_name = "SQL Server Edition"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("sql_server_edition") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:database-settings"


class VeeamServerSQLVersionSensor(VeeamServerBaseSensor):
    """Sensor for Veeam Server SQL Version."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_server_sql_version"
        self._attr_name = "SQL Server Version"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        server_info = self._server_info()
        return server_info.get("sql_server_version") if server_info else None

    @property
    def icon(self) -> str:
        return "mdi:database-check"


# ===========================
# LICENSE SENSORS (single device)
# ===========================


class VeeamLicenseBaseSensor(VeeamLicenseMixin, CoordinatorEntity, SensorEntity):
    """Base class for Veeam License sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamLicenseMixin.__init__(self, coordinator, config_entry)


class VeeamLicenseStatusSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Status."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("status") if license_info else None

    @property
    def icon(self) -> str:
        state = self.native_value
        if state and state.lower() == "valid":
            return "mdi:license"
        if state and state.lower() == "expired":
            return "mdi:license-off"
        return "mdi:license"


class VeeamLicenseEditionSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Edition."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_edition"
        self._attr_name = "Edition"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("edition") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:certificate"


class VeeamLicenseTypeSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Type."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_type"
        self._attr_name = "Type"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("type") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:file-document"


class VeeamLicenseExpirationSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Expiration Date."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_expiration"
        self._attr_name = "Expiration Date"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        license_info = self._license_info()
        return license_info.get("expiration_date") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:calendar-end"


class VeeamLicenseSupportExpirationSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Support Expiration Date."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_support_expiration"
        self._attr_name = "Support Expiration Date"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        license_info = self._license_info()
        return license_info.get("support_expiration_date") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:calendar-clock"


class VeeamLicenseLicensedToSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Licensed To."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_licensed_to"
        self._attr_name = "Licensed To"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("licensed_to") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:account"


class VeeamLicenseSupportIDSensor(VeeamLicenseBaseSensor):
    """Sensor for Veeam License Support ID."""

    def __init__(self, coordinator, config_entry):
        super().__init__(coordinator, config_entry)
        self._attr_unique_id = f"{config_entry.entry_id}_license_support_id"
        self._attr_name = "Support ID"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        license_info = self._license_info()
        return license_info.get("support_id") if license_info else None

    @property
    def icon(self) -> str:
        return "mdi:identifier"


class VeeamLicenseBinarySensorBase(VeeamLicenseMixin, CoordinatorEntity, BinarySensorEntity):
    """Base class for Veeam License binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamLicenseMixin.__init__(self, coordinator, config_entry)


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
        return bool(license_info.get("auto_update_enabled"))


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


# ===========================
# REPOSITORY SENSORS (device per repository)
# ===========================


class VeeamRepositoryBaseSensor(VeeamRepositoryMixin, CoordinatorEntity, SensorEntity):
    """Base class for Veeam Repository sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, repository_data):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamRepositoryMixin.__init__(self, coordinator, config_entry, repository_data)


class VeeamRepositoryTypeSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Type."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_type"
        self._attr_name = "Type"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        repo = self._repository()
        return repo.get("type", "unknown") if repo else None

    @property
    def icon(self) -> str:
        repo = self._repository()
        if not repo:
            return "mdi:database"

        repo_type = (repo.get("type") or "").lower()
        if "linux" in repo_type:
            return "mdi:linux"
        if "win" in repo_type:
            return "mdi:microsoft-windows"
        if "cloud" in repo_type or "azure" in repo_type or "aws" in repo_type:
            return "mdi:cloud"
        if "scale" in repo_type or "sobr" in repo_type:
            return "mdi:database-cluster"
        return "mdi:database"


class VeeamRepositoryDescriptionSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Description."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_description"
        self._attr_name = "Description"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        repo = self._repository()
        return repo.get("description", "") if repo else None

    @property
    def icon(self) -> str:
        return "mdi:text"


class VeeamRepositoryCapacitySensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Total Capacity."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_capacity"
        self._attr_name = "Capacity"
        self._attr_native_unit_of_measurement = "GB"
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        repo = self._repository()
        if not repo:
            return None
        return repo.get("capacity_gb")

    @property
    def icon(self) -> str:
        return "mdi:harddisk"


class VeeamRepositoryFreeSpaceSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Free Space."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_free_space"
        self._attr_name = "Free Space"
        self._attr_native_unit_of_measurement = "GB"
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        repo = self._repository()
        if not repo:
            return None
        return repo.get("free_gb")

    @property
    def icon(self) -> str:
        return "mdi:database-check"


class VeeamRepositoryUsedSpaceSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Used Space."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_used_space"
        self._attr_name = "Used Space"
        self._attr_native_unit_of_measurement = "GB"
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float | None:
        repo = self._repository()
        if not repo:
            return None
        return repo.get("used_space_gb")

    @property
    def icon(self) -> str:
        return "mdi:database-alert"


class VeeamRepositoryUsedSpacePercentSensor(VeeamRepositoryBaseSensor):
    """Sensor for Veeam Repository Used Space Percentage."""

    def __init__(self, coordinator, config_entry, repository_data):
        super().__init__(coordinator, config_entry, repository_data)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_repository_{self._repo_id}_used_space_percent"
        )
        self._attr_name = "Used Space"
        self._attr_native_unit_of_measurement = "%"
        self._attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        repo = self._repository()
        if not repo:
            return None
        capacity = repo.get("capacity_gb")
        used = repo.get("used_space_gb")
        if capacity and capacity > 0 and used is not None:
            return round((used / capacity) * 100, 1)
        return None

    @property
    def icon(self) -> str:
        value = self.native_value
        if value is None:
            return "mdi:percent"
        if value >= 90:
            return "mdi:alert-circle"
        if value >= 75:
            return "mdi:alert"
        return "mdi:chart-arc"


class VeeamRepositoryBinarySensorBase(VeeamRepositoryMixin, CoordinatorEntity, BinarySensorEntity):
    """Base class for Veeam Repository binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, repository_data):
        CoordinatorEntity.__init__(self, coordinator)
        VeeamRepositoryMixin.__init__(self, coordinator, config_entry, repository_data)


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
        return repo.get("is_online")


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
        return bool(repo.get("is_out_of_date"))
