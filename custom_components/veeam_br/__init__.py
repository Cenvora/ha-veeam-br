"""The Veeam Backup & Replication integration."""

from __future__ import annotations

from datetime import timedelta
import importlib
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup & Replication from a config entry."""
    from veeam_br.client import VeeamClient

    api_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

    # Import UNSET type for proper type checking
    try:
        types_module = importlib.import_module(f"veeam_br.{api_module}.types")
        UNSET = types_module.UNSET
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_br types: %s", err)
        return False

    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"

    # Create VeeamClient directly - it handles token rotation automatically
    veeam_client = VeeamClient(
        host=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        api_version=api_version,
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    # Connect to Veeam API
    try:
        await veeam_client.connect()
    except Exception as err:
        _LOGGER.error("Failed to connect to Veeam API: %s", err)
        return False

    async def async_update_data():
        """Fetch data from API."""
        # Track connection state for diagnostic sensors
        connected = False
        health_ok = False
        last_successful_poll = None

        try:
            # VeeamClient handles token refresh automatically in call() method
            # No need for manual token validation

            # Mark as connected
            connected = True

            # Fetch jobs data
            jobs_response = await veeam_client.call(veeam_client.api("jobs").get_all_jobs_states)

            if not jobs_response:
                raise UpdateFailed("Jobs API returned no data")

            # Access the .data field from JobStatesResult
            jobs_data = jobs_response.data if jobs_response else []

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
                server_data = await veeam_client.call(veeam_client.api("service").get_server_info)
                if server_data:
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
                license_data = await veeam_client.call(
                    veeam_client.api("license_").get_installed_license
                )
                if license_data:

                    # Helper function to safely get enum value from object attribute
                    def get_license_enum_attr(obj, attr_name, default="Unknown"):
                        """Extract enum value from object attribute, handling both enum types and UNSET."""
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

                    # Helper function to safely get datetime from object attribute
                    def get_license_datetime_attr(obj, attr_name):
                        """Extract datetime value from object attribute, handling UNSET."""
                        attr = getattr(obj, attr_name, None)
                        if attr is None:
                            return None
                        # Check if it's UNSET
                        if hasattr(attr, "__class__") and attr.__class__.__name__ == "Unset":
                            return None
                        return attr

                    license_info = {
                        "status": get_license_enum_attr(license_data, "status"),
                        "edition": get_license_enum_attr(license_data, "edition"),
                        "type": get_license_enum_attr(
                            license_data, "type_"
                        ),  # Note: type_ with underscore
                        "expiration_date": get_license_datetime_attr(
                            license_data, "expiration_date"
                        ),
                        "support_expiration_date": get_license_datetime_attr(
                            license_data, "support_expiration_date"
                        ),
                        "support_id": getattr(license_data, "support_id", "Unknown"),
                        "auto_update_enabled": getattr(license_data, "auto_update_enabled", False),
                        "licensed_to": getattr(license_data, "licensed_to", "Unknown"),
                        "cloud_connect": get_license_enum_attr(license_data, "cloud_connect"),
                        "free_agent_instance_consumption_enabled": getattr(
                            license_data, "free_agent_instance_consumption_enabled", False
                        ),
                    }
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse license info: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch license info: %s", err)

            # Fetch repositories information
            repositories_list = []
            try:
                # Helper to safely get UUID as string
                def get_uuid_value(uuid_val):
                    """Extract UUID value."""
                    if uuid_val is None or uuid_val is UNSET:
                        return None
                    return str(uuid_val)

                # Helper to serialize nested objects to dict
                def serialize_value(value):
                    """Recursively serialize values to JSON-compatible types."""
                    if value is None or value is UNSET:
                        return None
                    if isinstance(value, (str, int, float, bool)):
                        return value
                    if isinstance(value, dict):
                        return {k: serialize_value(v) for k, v in value.items()}
                    if isinstance(value, (list, tuple)):
                        return [serialize_value(item) for item in value]
                    # Handle objects with to_dict method
                    if hasattr(value, "to_dict"):
                        return value.to_dict()
                    # Handle enum types
                    if hasattr(value, "value"):
                        return value.value
                    # Convert remaining types to string as fallback
                    try:
                        str_value = str(value)
                        _LOGGER.debug(
                            "Serialized unexpected type %s to string: %s",
                            type(value).__name__,
                            str_value[:50],
                        )
                        return str_value
                    except Exception as err:
                        _LOGGER.warning(
                            "Failed to serialize value of type %s: %s",
                            type(value).__name__,
                            err,
                        )
                        return None

                repositories_result = await veeam_client.call(
                    veeam_client.api("repositories").get_all_repositories
                )
                repositories_states_result = await veeam_client.call(
                    veeam_client.api("repositories").get_all_repositories_states
                )

                if repositories_result:
                    repositories_data = repositories_result.data if repositories_result else []

                    _LOGGER.debug("Fetched %d repositories from API", len(repositories_data))

                    # Build states dict for quick lookup by ID
                    states_by_id = {}
                    if repositories_states_result:
                        states_data = (
                            repositories_states_result.data if repositories_states_result else []
                        )
                        for state in states_data:
                            repo_id = get_uuid_value(getattr(state, "id", None))
                            if repo_id:
                                states_by_id[repo_id] = state
                        _LOGGER.debug("Fetched %d repository states from API", len(states_by_id))

                    for repo in repositories_data:
                        try:
                            repo_dict = {
                                "id": get_uuid_value(repo.id),
                                "name": repo.name or "Unknown",
                                "description": repo.description or "",
                                "type": get_enum_value(repo.type_),
                                "unique_id": (
                                    repo.unique_id if repo.unique_id is not UNSET else None
                                ),
                            }

                            # Add state information if available
                            repo_id = repo_dict["id"]
                            if repo_id in states_by_id:
                                state = states_by_id[repo_id]
                                # Add capacity information
                                repo_dict["capacity_gb"] = getattr(state, "capacity_gb", None)
                                repo_dict["free_gb"] = getattr(state, "free_gb", None)
                                repo_dict["used_space_gb"] = getattr(state, "used_space_gb", None)
                                repo_dict["is_online"] = getattr(state, "is_online", None)
                                repo_dict["is_out_of_date"] = getattr(state, "is_out_of_date", None)

                            # Extract repository-specific fields from the repo object
                            # Immutability - from bucket.immutability for S3 repos
                            # Due to circular inheritance in OpenAPI schema, bucket is in additional_properties
                            if hasattr(repo, "additional_properties"):
                                bucket = repo.additional_properties.get("bucket")
                                _LOGGER.debug(
                                    "Repository %s: Checking additional_properties, bucket found=%s",
                                    repo_dict.get("name"),
                                    bucket is not None,
                                )
                                if bucket:
                                    # bucket is a dict from additional_properties
                                    immutability = bucket.get("immutability")
                                    if immutability:
                                        _LOGGER.debug(
                                            "Repository %s: immutability found in bucket",
                                            repo_dict.get("name"),
                                        )
                                        # immutability is a dict with isEnabled, daysCount, immutabilityMode
                                        is_enabled = immutability.get("isEnabled")
                                        if is_enabled is not None:
                                            repo_dict["is_immutable"] = bool(is_enabled)
                                            _LOGGER.info(
                                                "Repository %s: Set is_immutable=%s",
                                                repo_dict.get("name"),
                                                repo_dict["is_immutable"],
                                            )
                                            # Extract immutability days count if enabled
                                            if is_enabled:
                                                days_count = immutability.get("daysCount")
                                                if days_count is not None:
                                                    repo_dict["immutability_days"] = days_count
                                                    _LOGGER.debug(
                                                        "Repository %s: immutability_days=%s",
                                                        repo_dict.get("name"),
                                                        days_count,
                                                    )

                            # Accessible - use is_online from state as a proxy
                            repo_dict["is_accessible"] = repo_dict.get("is_online")

                            # Add all additional properties from the API response
                            if hasattr(repo, "additional_properties"):
                                for key, value in repo.additional_properties.items():
                                    repo_dict[key] = serialize_value(value)

                            repositories_list.append(repo_dict)
                            _LOGGER.debug(
                                "Successfully parsed repository: %s (type: %s)",
                                repo_dict.get("name"),
                                repo_dict.get("type"),
                            )
                        except (AttributeError, TypeError) as err:
                            _LOGGER.warning(
                                "Failed to parse repository %s: %s",
                                getattr(repo, "name", "Unknown"),
                                err,
                            )
                            continue
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse repositories: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch repositories: %s", err)

            _LOGGER.debug(
                "Total repositories added to coordinator data: %d", len(repositories_list)
            )

            # Fetch Scale-Out Backup Repositories (SOBRs)
            sobr_list = []
            try:
                sobr_result = await veeam_client.call(
                    veeam_client.api("repositories").get_all_scale_out_repositories
                )

                if sobr_result:
                    sobr_data = sobr_result.data if sobr_result else []
                    _LOGGER.debug("Fetched %d scale-out repositories from API", len(sobr_data))

                    for sobr in sobr_data:
                        try:
                            sobr_dict = {
                                "id": get_uuid_value(sobr.id),
                                "name": sobr.name or "Unknown",
                                "description": sobr.description or "",
                                "unique_id": (
                                    sobr.unique_id if sobr.unique_id is not UNSET else None
                                ),
                            }

                            # Extract performance tier extents
                            if hasattr(sobr, "performance_tier") and sobr.performance_tier:
                                extents = []
                                if (
                                    hasattr(sobr.performance_tier, "performance_extents")
                                    and sobr.performance_tier.performance_extents
                                ):
                                    for extent in sobr.performance_tier.performance_extents:
                                        extent_dict = {
                                            "id": get_uuid_value(extent.id),
                                            "name": extent.name or "Unknown",
                                            "status": (
                                                [s.value for s in extent.status]
                                                if extent.status is not UNSET
                                                else []
                                            ),
                                        }
                                        extents.append(extent_dict)
                                sobr_dict["extents"] = extents

                            # Add all additional properties from the API response
                            if hasattr(sobr, "additional_properties"):
                                for key, value in sobr.additional_properties.items():
                                    sobr_dict[key] = serialize_value(value)

                            sobr_list.append(sobr_dict)
                            _LOGGER.debug(
                                "Successfully parsed SOBR: %s (id: %s, extents: %d)",
                                sobr_dict.get("name"),
                                sobr_dict.get("id"),
                                len(sobr_dict.get("extents", [])),
                            )
                        except (AttributeError, TypeError) as err:
                            _LOGGER.warning(
                                "Failed to parse SOBR %s: %s",
                                getattr(sobr, "name", "Unknown"),
                                err,
                            )
                            continue
            except (AttributeError, KeyError, TypeError) as err:
                _LOGGER.warning("Failed to parse scale-out repositories: %s", err)
            except Exception as err:
                _LOGGER.warning("Failed to fetch scale-out repositories: %s", err)

            _LOGGER.debug("Total SOBRs added to coordinator data: %d", len(sobr_list))

            # Update diagnostic values - successful poll
            health_ok = True
            last_successful_poll = dt_util.now()

            return {
                "jobs": jobs_list,
                "server_info": server_info,
                "license_info": license_info,
                "repositories": repositories_list,
                "sobrs": sobr_list,
                "diagnostics": {
                    "connected": connected,
                    "health_ok": health_ok,
                    "last_successful_poll": last_successful_poll,
                },
            }

        except Exception as err:
            # When an update fails, the coordinator retains the last successful data,
            # so diagnostic sensors will continue to show the last successful poll time
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = {
        "coordinator": coordinator,
        "veeam_client": veeam_client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
