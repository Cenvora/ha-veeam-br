"""Support for Veeam Backup & Replication buttons."""

from __future__ import annotations

import importlib
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    DEFAULT_API_VERSION,
    DOMAIN,
    check_api_feature_availability,
)

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming the Veeam API
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup & Replication buttons."""
    coordinator = entry.runtime_data["coordinator"]
    veeam_client = entry.runtime_data["veeam_client"]

    added_repository_ids: set[str] = set()
    added_sobr_extent_ids: set[tuple[str, str]] = set()  # (sobr_id, extent_id) tuples
    added_job_ids: set[str] = set()

    @callback
    def _sync_entities() -> None:
        if not coordinator.data:
            return

        # Get the configured API version
        api_version = entry.options.get(
            CONF_API_VERSION,
            entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )

        new_entities = []

        # Create buttons for each job
        for job in coordinator.data.get("jobs", []):
            job_id = job.get("id")
            if not job_id or job_id in added_job_ids:
                continue

            job_buttons = []

            # Check if each button type's API feature is available before creating
            if check_api_feature_availability(api_version, "models.job_start_spec"):
                job_buttons.append(VeeamJobStartButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_stop_spec"):
                job_buttons.append(VeeamJobStopButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "models.job_retry_spec"):
                job_buttons.append(VeeamJobRetryButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, "api.jobs"):
                job_buttons.append(VeeamJobEnableButton(coordinator, entry, job, veeam_client))
                job_buttons.append(VeeamJobDisableButton(coordinator, entry, job, veeam_client))

            new_entities.extend(job_buttons)
            added_job_ids.add(job_id)
            _LOGGER.debug(
                "Adding %d buttons for job: %s (id: %s)",
                len(job_buttons),
                job.get("name"),
                job_id,
            )

        # Create rescan button for each repository
        if check_api_feature_availability(api_version, "models.repositories_rescan_spec"):
            for repository in coordinator.data.get("repositories", []):
                repo_id = repository.get("id")
                if not repo_id or repo_id in added_repository_ids:
                    continue

                new_entities.append(
                    VeeamRepositoryRescanButton(coordinator, entry, repository, veeam_client)
                )
                added_repository_ids.add(repo_id)
                _LOGGER.debug(
                    "Adding rescan button for repository: %s (id: %s)",
                    repository.get("name"),
                    repo_id,
                )

        # Create buttons for each SOBR extent
        if check_api_feature_availability(api_version, "models.scale_out_extent_maintenance_spec"):
            for sobr in coordinator.data.get("sobrs", []):
                sobr_id = sobr.get("id")
                sobr_name = sobr.get("name", "Unknown SOBR")
                if not sobr_id:
                    continue

                for extent in sobr.get("extents", []):
                    extent_id = extent.get("id")
                    if not extent_id:
                        continue

                    extent_key = (sobr_id, extent_id)
                    if extent_key in added_sobr_extent_ids:
                        continue

                    # Create 4 buttons for each extent (enable/disable sealed and maintenance mode)
                    new_entities.extend(
                        [
                            VeeamSOBRExtentEnableSealedModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                            VeeamSOBRExtentDisableSealedModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                            VeeamSOBRExtentEnableMaintenanceModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                            VeeamSOBRExtentDisableMaintenanceModeButton(
                                coordinator, entry, sobr, extent, veeam_client
                            ),
                        ]
                    )
                    added_sobr_extent_ids.add(extent_key)
                    _LOGGER.debug(
                        "Adding buttons for SOBR extent: %s/%s (sobr_id: %s, extent_id: %s)",
                        sobr_name,
                        extent.get("name"),
                        sobr_id,
                        extent_id,
                    )

        if new_entities:
            _LOGGER.debug("Adding %d Veeam buttons", len(new_entities))
            async_add_entities(new_entities)

        # Remove stale button entities
        _remove_stale_button_entities(
            hass, entry, added_repository_ids, added_sobr_extent_ids, added_job_ids
        )

    def _remove_stale_button_entities(
        hass: HomeAssistant,
        entry: ConfigEntry,
        current_repo_ids: set[str],
        current_sobr_extent_ids: set[tuple[str, str]],
        current_job_ids: set[str],
    ) -> None:
        """Remove button entities for repos/sobrs/jobs that no longer exist."""
        if not coordinator.data:
            return

        entity_reg = er.async_get(hass)

        # Get current IDs from coordinator data
        current_repos_in_data = {
            repo.get("id") for repo in coordinator.data.get("repositories", []) if repo.get("id")
        }
        current_jobs_in_data = {
            job.get("id") for job in coordinator.data.get("jobs", []) if job.get("id")
        }

        # Track current SOBR extents in data
        current_sobr_extents_in_data = set()
        for sobr in coordinator.data.get("sobrs", []):
            sobr_id = sobr.get("id")
            if sobr_id:
                for extent in sobr.get("extents", []):
                    extent_id = extent.get("id")
                    if extent_id:
                        current_sobr_extents_in_data.add((sobr_id, extent_id))

        # Find stale repository buttons
        stale_repo_ids = current_repo_ids - current_repos_in_data
        for repo_id in stale_repo_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"repository_{repo_id}_rescan" in entity.unique_id:
                    _LOGGER.info("Removing stale repository button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            current_repo_ids.discard(repo_id)

        # Find stale SOBR extent buttons
        stale_sobr_extents = current_sobr_extent_ids - current_sobr_extents_in_data
        for sobr_id, extent_id in stale_sobr_extents:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"sobr_{sobr_id}_extent_{extent_id}" in entity.unique_id:
                    _LOGGER.info("Removing stale SOBR extent button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            current_sobr_extent_ids.discard((sobr_id, extent_id))

        # Find stale job buttons
        stale_job_ids = current_job_ids - current_jobs_in_data
        for job_id in stale_job_ids:
            for entity in er.async_entries_for_config_entry(entity_reg, entry.entry_id):
                if entity.unique_id and f"job_{job_id}" in entity.unique_id:
                    _LOGGER.info("Removing stale job button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            current_job_ids.discard(job_id)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


class VeeamRepositoryRescanButton(CoordinatorEntity, ButtonEntity):
    """Button to trigger repository rescan."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, repository_data, veeam_client):
        """Initialize the rescan button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")
        self._veeam_client = veeam_client
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_rescan"
        self._attr_name = "Rescan"

    @property
    def device_info(self):
        """Return device info for this repository."""
        return {
            "identifiers": {(DOMAIN, f"repository_{self._repo_id}")},
            "name": f"{self._repo_name}",
            "manufacturer": "Veeam",
            "model": "Backup Repository",
        }

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:magnify-scan"

    async def async_press(self) -> None:
        """Handle the button press to trigger a repository rescan.

        This method calls the Veeam API to rescan the repository using the
        veeam-br library's rescan_repositories endpoint with the repository ID.
        After a successful rescan request, it triggers a coordinator refresh
        to update all repository sensors.

        Side effects:
            - Calls the Veeam API repositories rescan endpoint via veeam-br library
            - Triggers coordinator.async_request_refresh() on success
        """
        try:
            # Get the API version
            api_version = self._config_entry.options.get(
                CONF_API_VERSION,
                self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            )
            api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

            # VeeamClient handles token refresh automatically - no manual check needed

            # Trigger the rescan using veeam-br library VeeamClient
            try:
                # Import the body model for the rescan request
                models_module = importlib.import_module(
                    f"veeam_br.{api_module}.models.repositories_rescan_spec"
                )
                RepositoriesRescanSpec = models_module.RepositoriesRescanSpec
                body = RepositoriesRescanSpec(repository_ids=[self._repo_id])
            except (ImportError, AttributeError) as e:
                _LOGGER.error(
                    "Failed to import RepositoriesRescanSpec: %s. Cannot rescan repository.", e
                )
                return

            # Call the rescan endpoint using VeeamClient
            try:
                await self._veeam_client.call(
                    self._veeam_client.api("repositories").rescan_repositories,
                    body=body,
                )
                _LOGGER.info("Successfully triggered rescan for repository: %s", self._repo_name)
                # Request coordinator update to refresh repository state
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error(
                    "Failed to rescan repository %s: %s",
                    self._repo_name,
                    call_err,
                )
                raise

        except Exception as err:
            _LOGGER.error("Error rescanning repository %s: %s", self._repo_name, err)
            raise


# ===========================
# SOBR EXTENT BUTTONS
# ===========================


class VeeamSOBRExtentButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for SOBR extent buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the SOBR extent button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._sobr_id = sobr_data.get("id")
        self._sobr_name = sobr_data.get("name", "Unknown SOBR")
        self._extent_id = extent_data.get("id")
        self._extent_name = extent_data.get("name", "Unknown Extent")
        self._veeam_client = veeam_client

    @property
    def device_info(self):
        """Return device info for this SOBR."""
        return {
            "identifiers": {(DOMAIN, f"sobr_{self._sobr_id}")},
            "name": f"{self._sobr_name}",
            "manufacturer": "Veeam",
            "model": "Scale-Out Backup Repository",
        }


class VeeamSOBRExtentEnableSealedModeButton(VeeamSOBRExtentButtonBase):
    """Button to enable sealed mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_enable_sealed_mode"
        )
        self._attr_name = f"{self._extent_name} Enable Sealed Mode"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:lock-check"

    async def async_press(self) -> None:
        """Handle the button press to enable sealed mode for the extent."""
        try:
            # Get the API version
            api_version = self._config_entry.options.get(
                CONF_API_VERSION,
                self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            )
            api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

            # Import the body model for the request
            try:
                models_module = importlib.import_module(
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec"
                )
                ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
                body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
            except (ImportError, AttributeError) as e:
                _LOGGER.error(
                    "Failed to import ScaleOutExtentMaintenanceSpec: %s. Cannot enable sealed mode.",
                    e,
                )
                return

            # Call the enable sealed mode endpoint
            try:
                await self._veeam_client.call(
                    self._veeam_client.api("repositories").enable_scale_out_extent_sealed_mode,
                    id=self._sobr_id,
                    body=body,
                )
                _LOGGER.info(
                    "Successfully enabled sealed mode for extent %s in SOBR %s",
                    self._extent_name,
                    self._sobr_name,
                )
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error(
                    "Failed to enable sealed mode for extent %s in SOBR %s: %s",
                    self._extent_name,
                    self._sobr_name,
                    call_err,
                )
                raise

        except Exception as err:
            _LOGGER.error(
                "Error enabling sealed mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise


class VeeamSOBRExtentDisableSealedModeButton(VeeamSOBRExtentButtonBase):
    """Button to disable sealed mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_disable_sealed_mode"
        )
        self._attr_name = f"{self._extent_name} Disable Sealed Mode"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:lock-open"

    async def async_press(self) -> None:
        """Handle the button press to disable sealed mode for the extent."""
        try:
            # Get the API version
            api_version = self._config_entry.options.get(
                CONF_API_VERSION,
                self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            )
            api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

            # Import the body model for the request
            try:
                models_module = importlib.import_module(
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec"
                )
                ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
                body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
            except (ImportError, AttributeError) as e:
                _LOGGER.error(
                    "Failed to import ScaleOutExtentMaintenanceSpec: %s. Cannot disable sealed mode.",
                    e,
                )
                return

            # Call the disable sealed mode endpoint
            try:
                await self._veeam_client.call(
                    self._veeam_client.api("repositories").disable_scale_out_extent_sealed_mode,
                    id=self._sobr_id,
                    body=body,
                )
                _LOGGER.info(
                    "Successfully disabled sealed mode for extent %s in SOBR %s",
                    self._extent_name,
                    self._sobr_name,
                )
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error(
                    "Failed to disable sealed mode for extent %s in SOBR %s: %s",
                    self._extent_name,
                    self._sobr_name,
                    call_err,
                )
                raise

        except Exception as err:
            _LOGGER.error(
                "Error disabling sealed mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise


class VeeamSOBRExtentEnableMaintenanceModeButton(VeeamSOBRExtentButtonBase):
    """Button to enable maintenance mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_enable_maintenance_mode"
        )
        self._attr_name = f"{self._extent_name} Enable Maintenance Mode"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:tools"

    async def async_press(self) -> None:
        """Handle the button press to enable maintenance mode for the extent."""
        try:
            # Get the API version
            api_version = self._config_entry.options.get(
                CONF_API_VERSION,
                self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            )
            api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

            # Import the body model for the request
            try:
                models_module = importlib.import_module(
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec"
                )
                ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
                body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
            except (ImportError, AttributeError) as e:
                _LOGGER.error(
                    "Failed to import ScaleOutExtentMaintenanceSpec: %s. "
                    "Cannot enable maintenance mode.",
                    e,
                )
                return

            # Call the enable maintenance mode endpoint
            try:
                await self._veeam_client.call(
                    self._veeam_client.api("repositories").enable_scale_out_extent_maintenance_mode,
                    id=self._sobr_id,
                    body=body,
                )
                _LOGGER.info(
                    "Successfully enabled maintenance mode for extent %s in SOBR %s",
                    self._extent_name,
                    self._sobr_name,
                )
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error(
                    "Failed to enable maintenance mode for extent %s in SOBR %s: %s",
                    self._extent_name,
                    self._sobr_name,
                    call_err,
                )
                raise

        except Exception as err:
            _LOGGER.error(
                "Error enabling maintenance mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise


class VeeamSOBRExtentDisableMaintenanceModeButton(VeeamSOBRExtentButtonBase):
    """Button to disable maintenance mode for a SOBR extent."""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, sobr_data, extent_data, veeam_client)
        self._attr_unique_id = (
            f"{config_entry.entry_id}_sobr_{self._sobr_id}_extent_{self._extent_id}"
            f"_disable_maintenance_mode"
        )
        self._attr_name = f"{self._extent_name} Disable Maintenance Mode"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:close-circle-outline"

    async def async_press(self) -> None:
        """Handle the button press to disable maintenance mode for the extent."""
        try:
            # Get the API version
            api_version = self._config_entry.options.get(
                CONF_API_VERSION,
                self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
            )
            api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

            # Import the body model for the request
            try:
                models_module = importlib.import_module(
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec"
                )
                ScaleOutExtentMaintenanceSpec = models_module.ScaleOutExtentMaintenanceSpec
                body = ScaleOutExtentMaintenanceSpec(repository_ids=[self._extent_id])
            except (ImportError, AttributeError) as e:
                _LOGGER.error(
                    "Failed to import ScaleOutExtentMaintenanceSpec: %s. "
                    "Cannot disable maintenance mode.",
                    e,
                )
                return

            # Call the disable maintenance mode endpoint
            try:
                await self._veeam_client.call(
                    self._veeam_client.api(
                        "repositories"
                    ).disable_scale_out_extent_maintenance_mode,
                    id=self._sobr_id,
                    body=body,
                )
                _LOGGER.info(
                    "Successfully disabled maintenance mode for extent %s in SOBR %s",
                    self._extent_name,
                    self._sobr_name,
                )
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error(
                    "Failed to disable maintenance mode for extent %s in SOBR %s: %s",
                    self._extent_name,
                    self._sobr_name,
                    call_err,
                )
                raise

        except Exception as err:
            _LOGGER.error(
                "Error disabling maintenance mode for extent %s in SOBR %s: %s",
                self._extent_name,
                self._sobr_name,
                err,
            )
            raise


# ===========================
# JOB BUTTONS
# ===========================


class VeeamJobButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for Veeam job buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the job button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")
        self._veeam_client = veeam_client

    @property
    def device_info(self):
        """Return device info for this job."""
        return {
            "identifiers": {(DOMAIN, f"job_{self._job_id}")},
            "name": f"{self._job_name}",
            "manufacturer": "Veeam",
            "model": "Backup Job",
        }

    def _get_api_module(self) -> str:
        """Get the API module name based on the configured API version."""
        api_version = self._config_entry.options.get(
            CONF_API_VERSION,
            self._config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )
        return API_VERSIONS.get(api_version, "v1_3_rev1")

    def _import_spec_model(self, spec_name: str):
        """Import a spec model from the veeam_br library.

        Args:
            spec_name: Name of the spec model (e.g., 'job_start_spec', 'job_stop_spec')

        Returns:
            The spec model class

        Raises:
            ImportError: If the model cannot be imported
            AttributeError: If the model class cannot be found
        """
        api_module = self._get_api_module()
        models_module = importlib.import_module(f"veeam_br.{api_module}.models.{spec_name}")
        # Convert snake_case to PascalCase for class name
        class_name = "".join(word.capitalize() for word in spec_name.split("_"))
        return getattr(models_module, class_name)


class VeeamJobStartButton(VeeamJobButtonBase):
    """Button to start a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_start"
        self._attr_name = "Start"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:play"

    async def async_press(self) -> None:
        """Handle the button press to start the job."""
        # Import the body model for the start request
        try:
            JobStartSpec = self._import_spec_model("job_start_spec")
            body = JobStartSpec(perform_active_full=False)
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStartSpec: %s. Cannot start job.", e)
            return

        # Call the start endpoint using VeeamClient
        try:
            await self._veeam_client.call(
                self._veeam_client.api("jobs").start_job,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully started job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to start job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobStopButton(VeeamJobButtonBase):
    """Button to stop a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_stop"
        self._attr_name = "Stop"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:stop"

    async def async_press(self) -> None:
        """Handle the button press to stop the job."""
        # Import the body model for the stop request
        try:
            JobStopSpec = self._import_spec_model("job_stop_spec")
            # JobStopSpec typically has no required parameters
            body = JobStopSpec()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStopSpec: %s. Cannot stop job.", e)
            return

        # Call the stop endpoint using VeeamClient
        try:
            await self._veeam_client.call(
                self._veeam_client.api("jobs").stop_job,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully stopped job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to stop job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobRetryButton(VeeamJobButtonBase):
    """Button to retry a failed Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_retry"
        self._attr_name = "Retry"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:refresh"

    async def async_press(self) -> None:
        """Handle the button press to retry the job."""
        # Import the body model for the retry request
        try:
            JobRetrySpec = self._import_spec_model("job_retry_spec")
            # JobRetrySpec typically has no required parameters
            body = JobRetrySpec()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobRetrySpec: %s. Cannot retry job.", e)
            return

        # Call the retry endpoint using VeeamClient
        try:
            await self._veeam_client.call(
                self._veeam_client.api("jobs").retry_job,
                id=self._job_id,
                body=body,
            )
            _LOGGER.info("Successfully retried job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to retry job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobEnableButton(VeeamJobButtonBase):
    """Button to enable a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_enable"
        self._attr_name = "Enable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:check-circle-outline"

    async def async_press(self) -> None:
        """Handle the button press to enable the job."""
        # Call the enable endpoint using VeeamClient
        try:
            await self._veeam_client.call(
                self._veeam_client.api("jobs").enable_job,
                id=self._job_id,
            )
            _LOGGER.info("Successfully enabled job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to enable job %s: %s",
                self._job_name,
                call_err,
            )
            raise


class VeeamJobDisableButton(VeeamJobButtonBase):
    """Button to disable a Veeam job."""

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, job_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_job_{self._job_id}_disable"
        self._attr_name = "Disable"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:cancel"

    async def async_press(self) -> None:
        """Handle the button press to disable the job."""
        # Call the disable endpoint using VeeamClient
        try:
            await self._veeam_client.call(
                self._veeam_client.api("jobs").disable_job,
                id=self._job_id,
            )
            _LOGGER.info("Successfully disabled job: %s", self._job_name)
            # Request coordinator update to refresh job state
            await self.coordinator.async_request_refresh()
        except Exception as call_err:
            _LOGGER.error(
                "Failed to disable job %s: %s",
                self._job_name,
                call_err,
            )
            raise
