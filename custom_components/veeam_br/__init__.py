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
    api_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

    # Import API endpoints dynamically
    try:
        get_all_jobs_states = importlib.import_module(
            f"veeam_br.{api_module}.api.jobs.get_all_jobs_states"
        )
        get_server_info = importlib.import_module(
            f"veeam_br.{api_module}.api.service.get_server_info"
        )
        get_installed_license = importlib.import_module(
            f"veeam_br.{api_module}.api.license_.get_installed_license"
        )
        # Import UNSET type for proper type checking
        types_module = importlib.import_module(f"veeam_br.{api_module}.types")
        UNSET = types_module.UNSET
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_br API: %s", err)
        return False

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    token_manager = VeeamTokenManager(
        base_url=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        api_version=api_version,
    )

    async def async_update_data():
        """Fetch data from API."""
        try:
            if not await token_manager.ensure_valid_token(hass):
                raise UpdateFailed("Failed to obtain valid access token")

            client = token_manager.get_authenticated_client()
            if not client:
                raise UpdateFailed("No authenticated client available")

            def _get_jobs():
                return get_all_jobs_states.sync_detailed(
                    client=client,
                    x_api_version=api_version,
                )

            def _get_server_info():
                return get_server_info.sync_detailed(
                    client=client,
                    x_api_version=api_version,
                )

            def _get_license():
                return get_installed_license.sync_detailed(
                    client=client,
                    x_api_version=api_version,
                )

            jobs_response = await hass.async_add_executor_job(_get_jobs)

            if jobs_response.status_code != 200:
                raise UpdateFailed(f"Jobs API returned status {jobs_response.status_code}")

            # Access the .data field from JobStatesResult
            jobs_result = jobs_response.parsed
            jobs_data = jobs_result.data if jobs_result else []

            # Helper function to safely get enum value
            def get_enum_value(enum_val, default="unknown"):
                """Extract enum value, handling both enum types and UNSET."""
                if enum_val is None or enum_val is UNSET:
                    return default
                # Try to get enum value
                if hasattr(enum_val, "value"):
                    return enum_val.value
                return str(enum_val)

            # Helper function to safely get datetime
            def get_datetime_value(dt_val):
                """Extract datetime value, handling UNSET."""
                if dt_val is None or dt_val is UNSET:
                    return None
                return dt_val

            jobs_list = []
            for job in jobs_data:
                try:
                    job_dict = {
                        "id": str(job.id),
                        "name": job.name or "Unknown",
                        "type": get_enum_value(job.type_),
                        "status": get_enum_value(job.status),
                        "last_result": get_enum_value(job.last_result),
                        "last_run": get_datetime_value(job.last_run),
                        "next_run": get_datetime_value(job.next_run),
                    }
                    jobs_list.append(job_dict)
                except (AttributeError, TypeError) as err:
                    _LOGGER.warning("Failed to parse job: %s", err)
                    continue

            # Fetch server information
            server_info = None
            try:
                server_response = await hass.async_add_executor_job(_get_server_info)
                if server_response.status_code == 200 and server_response.parsed:
                    server_data = server_response.parsed
                    server_info = {
                        "vbr_id": getattr(server_data, "vbr_id", "Unknown"),
                        "name": getattr(server_data, "name", "Unknown"),
                        "build_version": getattr(server_data, "build_version", "Unknown"),
                        "patches": getattr(server_data, "patches", []),
                        "database_vendor": getattr(server_data, "database_vendor", "Unknown"),
                        "sql_server_edition": getattr(server_data, "sql_server_edition", "Unknown"),
                        "sql_server_version": getattr(server_data, "sql_server_version", "Unknown"),
                        "database_schema_version": getattr(
                            server_data, "database_schema_version", "Unknown"
                        ),
                        "database_content_version": getattr(
                            server_data, "database_content_version", "Unknown"
                        ),
                        "platform": (
                            server_data.platform.value
                            if hasattr(server_data, "platform")
                            and hasattr(server_data.platform, "value")
                            else str(getattr(server_data, "platform", "Unknown"))
                        ),
                    }
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse server info: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch server info: %s", err)

            # Fetch license information
            license_info = None
            try:
                license_response = await hass.async_add_executor_job(_get_license)
                if license_response.status_code == 200 and license_response.parsed:
                    license_data = license_response.parsed

                    # Helper function to safely get enum value
                    def get_enum_value(obj, attr_name, default="Unknown"):
                        """Extract enum value, handling both enum types and UNSET."""
                        attr = getattr(obj, attr_name, None)
                        if attr is None:
                            return default
                        # Check if it's UNSET (from veeam-br library)
                        if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                            return default
                        # Try to get enum value
                        if hasattr(attr, "value"):
                            return attr.value
                        return str(attr)

                    # Helper function to safely get datetime
                    def get_datetime_value(obj, attr_name):
                        """Extract datetime value, handling UNSET."""
                        attr = getattr(obj, attr_name, None)
                        if attr is None:
                            return None
                        # Check if it's UNSET
                        if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                            return None
                        return attr

                    license_info = {
                        "status": get_enum_value(license_data, "status"),
                        "edition": get_enum_value(license_data, "edition"),
                        "type": get_enum_value(
                            license_data, "type_"
                        ),  # Note: type_ with underscore
                        "expiration_date": get_datetime_value(license_data, "expiration_date"),
                        "support_expiration_date": get_datetime_value(
                            license_data, "support_expiration_date"
                        ),
                        "support_id": getattr(license_data, "support_id", "Unknown"),
                        "auto_update_enabled": getattr(license_data, "auto_update_enabled", False),
                        "licensed_to": getattr(license_data, "licensed_to", "Unknown"),
                        "cloud_connect": get_enum_value(license_data, "cloud_connect"),
                        "free_agent_instance_consumption_enabled": getattr(
                            license_data, "free_agent_instance_consumption_enabled", False
                        ),
                    }
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse license info: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch license info: %s", err)

            return {
                "jobs": jobs_list,
                "server_info": server_info,
                "license_info": license_info,
            }

        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "token_manager": token_manager,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
