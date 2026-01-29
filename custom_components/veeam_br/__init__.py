"""The Veeam Backup & Replication integration."""

from __future__ import annotations

from datetime import timedelta
import json
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
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

            # Make a direct HTTP request to avoid importing broken veeam-br models
            # The veeam-br package has missing model files (backup_copy_job_model, etc.)
            # that cause import errors when using the API wrapper functions
            def _get_jobs():
                httpx_client = client.get_httpx_client()
                response = httpx_client.get(
                    "/api/v1/jobs",
                    params={"limit": 200},
                    headers={"x-api-version": api_version},
                )
                return response

            response = await hass.async_add_executor_job(_get_jobs)

            # Check response is valid
            if response is None:
                raise UpdateFailed("API returned None response")

            # Check response status
            if response.status_code != 200:
                raise UpdateFailed(f"API returned status {response.status_code}")

            # Parse the JSON response directly
            try:
                data = json.loads(response.text)
            except json.JSONDecodeError as err:
                raise UpdateFailed(f"Failed to parse API response: {err}") from err

            # Extract jobs from the response
            jobs_data = data.get("data", [])
            if not isinstance(jobs_data, list):
                return []

            # Convert jobs to a list of dictionaries for easier processing
            jobs = []
            for job in jobs_data:
                if not isinstance(job, dict):
                    continue
                jobs.append(
                    {
                        "id": job.get("id"),
                        "name": job.get("name", "Unknown"),
                        "status": job.get("status", "unknown"),
                        "type": job.get("type"),
                        "last_run": job.get("lastRun"),
                        "next_run": job.get("nextRun"),
                        "last_result": job.get("lastResult"),
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
