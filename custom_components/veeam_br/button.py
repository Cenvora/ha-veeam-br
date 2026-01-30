"""Support for Veeam Backup & Replication buttons."""

from __future__ import annotations

import importlib
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import API_VERSIONS, CONF_API_VERSION, DEFAULT_API_VERSION, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup & Replication buttons."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    token_manager = hass.data[DOMAIN][entry.entry_id]["token_manager"]

    added_repository_ids: set[str] = set()

    @callback
    def _sync_entities() -> None:
        if not coordinator.data:
            return

        new_entities = []

        # Create rescan button for each repository
        for repository in coordinator.data.get("repositories", []):
            repo_id = repository.get("id")
            if not repo_id or repo_id in added_repository_ids:
                continue

            new_entities.append(
                VeeamRepositoryRescanButton(coordinator, entry, repository, token_manager)
            )
            added_repository_ids.add(repo_id)
            _LOGGER.debug(
                "Adding rescan button for repository: %s (id: %s)",
                repository.get("name"),
                repo_id,
            )

        if new_entities:
            _LOGGER.debug("Adding %d Veeam buttons", len(new_entities))
            async_add_entities(new_entities)

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    coordinator.async_add_listener(_sync_entities)


class VeeamRepositoryRescanButton(CoordinatorEntity, ButtonEntity):
    """Button to trigger repository rescan."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, repository_data, token_manager):
        """Initialize the rescan button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")
        self._token_manager = token_manager
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

            # Import the rescan endpoint dynamically
            try:
                rescan_repositories = importlib.import_module(
                    f"veeam_br.{api_module}.api.repositories.rescan_repositories"
                )
            except ImportError:
                _LOGGER.error("Rescan repositories API not available in version %s", api_version)
                return

            # Ensure we have a valid token
            if not await self._token_manager.ensure_valid_token(self.hass):
                _LOGGER.error("Failed to obtain valid access token for repository rescan")
                return

            client = self._token_manager.get_authenticated_client()
            if not client:
                _LOGGER.error("No authenticated client available for repository rescan")
                return

            # Trigger the rescan using veeam-br library
            def _rescan():
                # The rescan_repositories endpoint POST /api/v1/backupInfrastructure/repositories/rescan
                # Import the body model for the rescan request
                try:
                    models_module = importlib.import_module(
                        f"veeam_br.{api_module}.models.repositories_rescan_spec"
                    )
                    RepositoriesRescanSpec = models_module.RepositoriesRescanSpec
                    body = RepositoriesRescanSpec(repository_ids=[self._repo_id])
                except (ImportError, AttributeError) as e:
                    _LOGGER.error(
                        "Failed to import RepositoriesRescanSpec: %s. Cannot rescan repository.", e
                    )
                    return None

                return rescan_repositories.sync_detailed(
                    client=client,
                    body=body,
                    x_api_version=api_version,
                )

            response = await self.hass.async_add_executor_job(_rescan)

            if response is None:
                _LOGGER.error("Failed to create rescan request for repository: %s", self._repo_name)
                return

            if response.status_code in (200, 202, 204):
                _LOGGER.info("Successfully triggered rescan for repository: %s", self._repo_name)
                # Request coordinator update to refresh repository state
                await self.coordinator.async_request_refresh()
            else:
                _LOGGER.error(
                    "Failed to rescan repository %s: HTTP %s - %s",
                    self._repo_name,
                    response.status_code,
                    getattr(response, "text", "No error details"),
                )

        except Exception as err:
            _LOGGER.error("Error rescanning repository %s: %s", self._repo_name, err)
