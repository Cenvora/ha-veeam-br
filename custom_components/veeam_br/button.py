"""Support for Veeam Backup & Replication buttons."""

from __future__ import annotations

import asyncio
import importlib
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    DEFAULT_API_MODULE,
    DEFAULT_API_VERSION,
    DOMAIN,
    check_api_feature_availability,
    configured_api_version,
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

    # Pre-import API endpoint modules to avoid blocking calls in event loop
    # Get the configured API version for proper module path
    api_version = configured_api_version(entry)
    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

    # Pre-import button API endpoints
    button_endpoints = [
        "jobs.start_job",
        "jobs.stop_job",
        "jobs.retry_job",
        "jobs.enable_job",
        "jobs.disable_job",
        "repositories.rescan_repositories",
        "repositories.enable_scale_out_extent_sealed_mode",
        "repositories.disable_scale_out_extent_sealed_mode",
        "repositories.enable_scale_out_extent_maintenance_mode",
        "repositories.disable_scale_out_extent_maintenance_mode",
    ]
    for endpoint in button_endpoints:
        try:
            await asyncio.to_thread(
                importlib.import_module, f"veeam_br.{api_module}.api.{endpoint}"
            )
        except ImportError as err:
            _LOGGER.debug("Could not pre-import %s: %s", endpoint, err)

    added_repository_ids: set[str] = set()
    added_sobr_extent_ids: set[tuple[str, str]] = set()  # (sobr_id, extent_id) tuples
    added_job_ids: set[str] = set()
    added_proxy_ids: set[str] = set()
    ha_cluster_added = False

    @callback
    def _sync_entities() -> None:
        nonlocal ha_cluster_added

        if not coordinator.data:
            return

        # Get the configured API version
        api_version = configured_api_version(entry)

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

        # ---- PROXY BUTTONS - a proxy can be taken out of service without deleting it ----
        if check_api_feature_availability(api_version, "api.proxies"):
            for proxy in coordinator.data.get("proxies", []):
                proxy_id = proxy.get("id")
                if not proxy_id or proxy_id in added_proxy_ids:
                    continue

                new_entities.extend(
                    [
                        VeeamProxyEnableButton(coordinator, entry, proxy, veeam_client),
                        VeeamProxyDisableButton(coordinator, entry, proxy, veeam_client),
                    ]
                )
                added_proxy_ids.add(proxy_id)
                _LOGGER.debug("Adding proxy buttons for: %s", proxy.get("name"))

        # ---- HA CLUSTER BUTTONS (once) - clustered servers on 1.3-rev2 and newer ----
        if (
            not ha_cluster_added
            and coordinator.data.get("ha_cluster")
            and check_api_feature_availability(api_version, "api.high_availability_ha_cluster")
        ):
            new_entities.extend(
                [
                    VeeamHAClusterSwitchoverButton(coordinator, entry, veeam_client),
                    VeeamHAClusterFailoverButton(coordinator, entry, veeam_client),
                ]
            )
            ha_cluster_added = True
            _LOGGER.debug("Adding HA cluster buttons")

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
        """Remove button entities for repos/sobrs/jobs that no longer exist.

        Scans the entity registry directly so that button entities persisted
        from previous HA sessions are also cleaned up, not only those added in
        the current session.
        """
        if not coordinator.data:
            return

        entity_reg = er.async_get(hass)
        entry_id = entry.entry_id

        # Get current IDs from coordinator data
        current_repos_in_data = {
            repo.get("id") for repo in coordinator.data.get("repositories", []) if repo.get("id")
        }
        current_jobs_in_data = {
            job.get("id") for job in coordinator.data.get("jobs", []) if job.get("id")
        }

        # Track current SOBR extents in data
        current_sobr_extents_in_data: set[tuple[str, str]] = set()
        for sobr in coordinator.data.get("sobrs", []):
            sobr_id = sobr.get("id")
            if sobr_id:
                for extent in sobr.get("extents", []):
                    extent_id = extent.get("id")
                    if extent_id:
                        current_sobr_extents_in_data.add((sobr_id, extent_id))

        # Build unique_id prefixes for active entities
        active_job_prefixes = {f"{entry_id}_job_{job_id}_" for job_id in current_jobs_in_data}
        active_repo_prefixes = {
            f"{entry_id}_repository_{repo_id}_" for repo_id in current_repos_in_data
        }
        active_sobr_extent_prefixes = {
            f"{entry_id}_sobr_{sobr_id}_extent_{extent_id}_"
            for sobr_id, extent_id in current_sobr_extents_in_data
        }

        # Scan all registered button entities for this config entry and remove stale ones.
        # Using list() to avoid mutating the iterable while iterating.
        for entity in list(er.async_entries_for_config_entry(entity_reg, entry_id)):
            if not entity.unique_id:
                continue
            unique_id = entity.unique_id

            if unique_id.startswith(f"{entry_id}_job_"):
                if not any(unique_id.startswith(p) for p in active_job_prefixes):
                    _LOGGER.info("Removing stale job button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)

            elif unique_id.startswith(f"{entry_id}_repository_"):
                if not any(unique_id.startswith(p) for p in active_repo_prefixes):
                    _LOGGER.info("Removing stale repository button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)

            elif unique_id.startswith(f"{entry_id}_sobr_") and "_extent_" in unique_id:
                if not any(unique_id.startswith(p) for p in active_sobr_extent_prefixes):
                    _LOGGER.info("Removing stale SOBR extent button: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)

        # Update tracking sets to reflect only IDs still present in the API
        current_job_ids.intersection_update(current_jobs_in_data)
        current_repo_ids.intersection_update(current_repos_in_data)
        current_sobr_extent_ids.intersection_update(current_sobr_extents_in_data)

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
            api_version = configured_api_version(self._config_entry)
            api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

            # VeeamClient handles token refresh automatically - no manual check needed

            # Trigger the rescan using veeam-br library VeeamClient
            try:
                # Import the body model for the rescan request
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_br.{api_module}.models.repositories_rescan_spec",
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
                repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
                await self._veeam_client.call(
                    repositories_api.rescan_repositories,
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
            api_version = configured_api_version(self._config_entry)
            api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

            # Import the body model for the request
            try:
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
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
                repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
                await self._veeam_client.call(
                    repositories_api.enable_scale_out_extent_sealed_mode,
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
            api_version = configured_api_version(self._config_entry)
            api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

            # Import the body model for the request
            try:
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
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
                repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
                await self._veeam_client.call(
                    repositories_api.disable_scale_out_extent_sealed_mode,
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
            api_version = configured_api_version(self._config_entry)
            api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

            # Import the body model for the request
            try:
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
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
                repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
                await self._veeam_client.call(
                    repositories_api.enable_scale_out_extent_maintenance_mode,
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
            api_version = configured_api_version(self._config_entry)
            api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

            # Import the body model for the request
            try:
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_br.{api_module}.models.scale_out_extent_maintenance_spec",
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
                repositories_api = await asyncio.to_thread(self._veeam_client.api, "repositories")
                await self._veeam_client.call(
                    repositories_api.disable_scale_out_extent_maintenance_mode,
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
        api_version = configured_api_version(self._config_entry)
        return API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

    async def _import_spec_model(self, spec_name: str):
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
        models_module = await asyncio.to_thread(
            importlib.import_module, f"veeam_br.{api_module}.models.{spec_name}"
        )
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
            JobStartSpec = await self._import_spec_model("job_start_spec")
            body = JobStartSpec(perform_active_full=False)
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStartSpec: %s. Cannot start job.", e)
            return

        # Call the start endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            result = await self._veeam_client.call(
                jobs_api.start_job,
                id=self._job_id,
                body=body,
            )
            if hasattr(result, "error_code"):
                raise HomeAssistantError(f"API error: {result.message}")
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
            JobStopSpec = await self._import_spec_model("job_stop_spec")
            # JobStopSpec typically has no required parameters
            body = JobStopSpec()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobStopSpec: %s. Cannot stop job.", e)
            return

        # Call the stop endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            result = await self._veeam_client.call(
                jobs_api.stop_job,
                id=self._job_id,
                body=body,
            )
            if hasattr(result, "error_code"):
                raise HomeAssistantError(f"API error: {result.message}")
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
            JobRetrySpec = await self._import_spec_model("job_retry_spec")
            # JobRetrySpec typically has no required parameters
            body = JobRetrySpec()
        except (ImportError, AttributeError) as e:
            _LOGGER.error("Failed to import JobRetrySpec: %s. Cannot retry job.", e)
            return

        # Call the retry endpoint using VeeamClient
        try:
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            result = await self._veeam_client.call(
                jobs_api.retry_job,
                id=self._job_id,
                body=body,
            )
            if hasattr(result, "error_code"):
                raise HomeAssistantError(f"API error: {result.message}")
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
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            await self._veeam_client.call(
                jobs_api.enable_job,
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
            jobs_api = await asyncio.to_thread(self._veeam_client.api, "jobs")
            await self._veeam_client.call(
                jobs_api.disable_job,
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


# ===========================
# HIGH AVAILABILITY CLUSTER BUTTONS
#
# Switchover is the planned, graceful role swap; failover is the unplanned one for when the
# primary is already gone. Both are disruptive, and Home Assistant buttons fire with no
# confirmation step, so failover is disabled by default — a user automating it has to enable
# the entity deliberately. See the README for the distinction.
# ===========================


class VeeamHAClusterButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for HA cluster buttons."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._veeam_client = veeam_client

    def _cluster(self) -> dict | None:
        """Get HA cluster data from coordinator data."""
        return self.coordinator.data.get("ha_cluster") if self.coordinator.data else None

    @property
    def available(self) -> bool:
        """A cluster mid-failover cannot take another switchover or failover."""
        cluster = self._cluster()
        if cluster is None:
            return False
        in_progress = cluster.get("is_failover_in_progress") or cluster.get(
            "is_endpoint_migration_in_progress"
        )
        return super().available and not in_progress

    @property
    def device_info(self):
        """Return device info for the HA cluster."""
        cluster = self._cluster()
        name = (cluster.get("name") if cluster else None) or "HA Cluster"
        host = self._config_entry.data.get(CONF_HOST, "Unknown")
        return {
            "identifiers": {(DOMAIN, f"ha_cluster_{self._config_entry.entry_id}")},
            "name": f"{name} ({host})",
            "manufacturer": "Veeam",
            "model": "High Availability Cluster",
        }

    def _api_module(self) -> str:
        """Resolve the SDK package for the configured API version."""
        api_version = configured_api_version(self._config_entry)
        return API_VERSIONS.get(api_version, DEFAULT_API_MODULE)


class VeeamHAClusterSwitchoverButton(VeeamHAClusterButtonBase):
    """Button to switch the cluster over to its secondary node."""

    def __init__(self, coordinator, config_entry, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_ha_cluster_switchover"
        self._attr_name = "Switchover"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:swap-horizontal"

    async def async_press(self) -> None:
        """Handle the button press to switch the cluster over."""
        try:
            api_module = self._api_module()

            # The spec is optional; sending it explicitly keeps the lag check in force, so a
            # switchover with a badly lagging secondary is refused by the server instead of
            # silently promoting a stale node.
            body = None
            try:
                models_module = await asyncio.to_thread(
                    importlib.import_module,
                    f"veeam_br.{api_module}.models.high_availability_switchover_spec",
                )
                body = models_module.HighAvailabilitySwitchoverSpec(ignore_lag=False)
            except (ImportError, AttributeError) as err:
                _LOGGER.debug(
                    "HighAvailabilitySwitchoverSpec unavailable (%s); "
                    "requesting switchover without a body",
                    err,
                )

            try:
                ha_api = await asyncio.to_thread(
                    self._veeam_client.api, "high_availability_ha_cluster"
                )
                kwargs = {"body": body} if body is not None else {}
                await self._veeam_client.call(ha_api.switchover_high_availability_cluster, **kwargs)
                _LOGGER.info("Requested HA cluster switchover")
                await self.coordinator.async_request_refresh()
            except Exception as call_err:
                _LOGGER.error("Failed to switch over the HA cluster: %s", call_err)
                raise

        except Exception as err:
            _LOGGER.error("Error switching over the HA cluster: %s", err)
            raise


class VeeamHAClusterFailoverButton(VeeamHAClusterButtonBase):
    """Button to fail the cluster over to its secondary node."""

    # Unplanned failover promotes the secondary without waiting for the primary. It is an
    # emergency action, so it ships disabled and the user enables it consciously.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, config_entry, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_ha_cluster_failover"
        self._attr_name = "Failover"

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:alert-octagon"

    async def async_press(self) -> None:
        """Handle the button press to fail the cluster over."""
        try:
            ha_api = await asyncio.to_thread(self._veeam_client.api, "high_availability_ha_cluster")
            await self._veeam_client.call(ha_api.failover_high_availability_cluster)
            _LOGGER.warning("Requested HA cluster failover")
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to fail over the HA cluster: %s", err)
            raise


# ===========================
# PROXY BUTTONS
#
# Disabling a proxy takes it out of service without deleting it, which is what an operator
# reaches for during maintenance on the proxy host.
# ===========================


class VeeamProxyButtonBase(CoordinatorEntity, ButtonEntity):
    """Base class for proxy buttons."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, proxy_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._veeam_client = veeam_client
        self._proxy_id = proxy_data.get("id")
        self._proxy_name = proxy_data.get("name", "Unknown")

    def _proxy(self) -> dict | None:
        if not self.coordinator.data:
            return None
        for proxy in self.coordinator.data.get("proxies", []):
            if proxy.get("id") == self._proxy_id:
                return proxy
        return None

    @property
    def available(self) -> bool:
        return super().available and self._proxy() is not None

    @property
    def device_info(self):
        """Return device info for this proxy."""
        return {
            "identifiers": {(DOMAIN, f"proxy_{self._proxy_id}")},
            "name": self._proxy_name,
            "manufacturer": "Veeam",
            "model": "Backup Proxy",
        }

    async def _call(self, operation_name: str, action: str) -> None:
        """Run one proxy operation and refresh."""
        try:
            proxies_api = await asyncio.to_thread(self._veeam_client.api, "proxies")
            await self._veeam_client.call(getattr(proxies_api, operation_name), id=self._proxy_id)
            _LOGGER.info("%s proxy %s", action, self._proxy_name)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to %s proxy %s: %s", action.lower(), self._proxy_name, err)
            raise


class VeeamProxyEnableButton(VeeamProxyButtonBase):
    """Button to put a proxy back into service."""

    def __init__(self, coordinator, config_entry, proxy_data, veeam_client):
        super().__init__(coordinator, config_entry, proxy_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_proxy_{self._proxy_id}_enable"
        self._attr_name = "Enable"

    @property
    def icon(self) -> str:
        return "mdi:play"

    async def async_press(self) -> None:
        """Handle the button press to enable the proxy."""
        await self._call("enable_proxy", "Enabled")


class VeeamProxyDisableButton(VeeamProxyButtonBase):
    """Button to take a proxy out of service."""

    def __init__(self, coordinator, config_entry, proxy_data, veeam_client):
        super().__init__(coordinator, config_entry, proxy_data, veeam_client)
        self._attr_unique_id = f"{config_entry.entry_id}_proxy_{self._proxy_id}_disable"
        self._attr_name = "Disable"

    @property
    def icon(self) -> str:
        return "mdi:pause"

    async def async_press(self) -> None:
        """Handle the button press to disable the proxy."""
        await self._call("disable_proxy", "Disabled")
