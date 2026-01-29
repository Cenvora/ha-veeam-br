"""The Veeam Backup & Replication integration."""

from __future__ import annotations

from datetime import timedelta
import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    UPDATE_INTERVAL,
)
from .token_manager import VeeamTokenManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup & Replication from a config entry."""
    # Get API version from options with fallback to data, then default
    api_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

    # Import the veeam_br library dynamically based on API version
    try:
        get_all_jobs_module = importlib.import_module(
            f"veeam_br.{api_module}.api.jobs.get_all_jobs"
        )
        get_all_jobs = get_all_jobs_module
    except ImportError as err:
        # Provide more specific feedback about what went wrong during import
        error_message: str
        if isinstance(err, ModuleNotFoundError):
            missing_name = getattr(err, "name", "") or ""
            if missing_name == "veeam_br":
                error_message = "veeam_br library is not installed"
            elif missing_name.startswith(f"veeam_br.{api_module}"):
                error_message = f"API version {api_version} is not supported or not available"
            else:
                error_message = "A required veeam_br module could not be found"
        else:
            error_message = "An unexpected import error occurred while loading the veeam_br library"

        _LOGGER.error(
            "Failed to import veeam_br library for API version %s: %s (%s)",
            api_version,
            error_message,
            err,
        )
        return False

    # Construct base URL
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    # Create token manager for handling token refresh
    token_manager = VeeamTokenManager(
        base_url=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        api_version=api_version,
    )

    # Create update coordinator
    async def async_update_data():
        """Fetch data from API."""
        try:
            # Ensure we have a valid token before making API calls
            if not await token_manager.ensure_valid_token(hass):
                raise UpdateFailed("Failed to obtain valid access token")

            # Get authenticated client
            client = token_manager.get_authenticated_client()
            if not client:
                raise UpdateFailed("No authenticated client available")

            # Get backup jobs and their status using the veeam-br library
            def _get_jobs():
                return get_all_jobs.sync(client=client, x_api_version=api_version)

            jobs_response = await hass.async_add_executor_job(_get_jobs)

            # Process the response
            if not jobs_response or not hasattr(jobs_response, "data"):
                return []

            # Convert jobs to a list of dictionaries for easier processing
            jobs = []
            for job in jobs_response.data:
                jobs.append(
                    {
                        "id": getattr(job, "id", None),
                        "name": getattr(job, "name", "Unknown"),
                        "status": getattr(job, "status", "unknown"),
                        "type": getattr(job, "type", None),
                        "last_run": getattr(job, "last_run", None),
                        "next_run": getattr(job, "next_run", None),
                        "last_result": getattr(job, "last_result", None),
                    }
                )

            return jobs

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator and token manager
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "token_manager": token_manager,
    }

    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for config entry options
    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    # Reload the integration when options are updated
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
