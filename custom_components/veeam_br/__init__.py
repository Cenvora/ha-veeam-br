"""The Veeam Backup & Replication integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
import importlib
import logging
import sys

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_MODULE,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    UPDATE_INTERVAL,
    check_api_feature_availability,
)
from .licensing import describe_license, unsupported_license_reason
from .sdk_patches import patch_models as patch_null_values_in_models

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

# High Availability cluster endpoints exist only from API 1.3-rev2 (VBR 13.1)
HA_CLUSTER_FEATURE = "api.high_availability_ha_cluster"


def _bool_or_none(obj, name: str) -> bool | None:
    """Read a boolean off a model, mapping UNSET and anything unexpected to None."""
    value = getattr(obj, name, None)
    return value if isinstance(value, bool) else None


def _parse_ha_cluster_node(node, get_enum_value, get_uuid_value) -> dict | None:
    """Flatten one HA cluster node into coordinator data."""
    if not node or not hasattr(node, "name"):
        return None

    external_endpoint = getattr(node, "external_endpoint", None)
    if not isinstance(external_endpoint, str):
        external_endpoint = None

    lag_mb = getattr(node, "lag_mb", None)

    return {
        "id": get_uuid_value(getattr(node, "id", None)),
        "name": getattr(node, "name", None) or "Unknown",
        "ip_address": getattr(node, "ip_address", None) or None,
        "fqdn": getattr(node, "fqdn", None) or None,
        "role": get_enum_value(getattr(node, "role", None)),
        "state": get_enum_value(getattr(node, "state", None)),
        "timeline": getattr(node, "timeline", None) or None,
        "lag_mb": lag_mb if isinstance(lag_mb, (int, float)) else None,
        "external_endpoint": external_endpoint,
    }


def _parse_ha_cluster_last_online(raw_value):
    """Parse lastOnlineTimeUtc, which Veeam's schema types as a plain string.

    Timestamp sensors need an aware datetime, and the field is documented as UTC, so a
    value carrying no offset is stamped UTC rather than assumed local.
    """
    if not isinstance(raw_value, str) or not raw_value:
        return None

    parsed = dt_util.parse_datetime(raw_value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_ha_cluster(cluster, get_enum_value, get_uuid_value, serialize_value) -> dict:
    """Flatten the HA cluster configuration and its state into coordinator data."""
    states = getattr(cluster, "states", None)

    def state_flag(name: str) -> bool | None:
        # states itself is optional in the schema
        return _bool_or_none(states, name) if states else None

    parsed = {
        "id": get_uuid_value(getattr(cluster, "id", None)),
        "name": getattr(cluster, "name", None) or "HA Cluster",
        "cluster_endpoint": getattr(cluster, "cluster_endpoint", None) or None,
        "cluster_dns_name": getattr(cluster, "cluster_dns_name", None) or None,
        "is_cross_subnet_mode": _bool_or_none(cluster, "is_cross_subnet_mode"),
        "is_online": state_flag("is_online"),
        "is_failover_in_progress": state_flag("is_failover_in_progress"),
        "is_maintenance_in_progress": state_flag("is_maintenance_in_progress"),
        "is_creation_in_progress": state_flag("is_creation_in_progress"),
        "is_removal_in_progress": state_flag("is_removal_in_progress"),
        "is_secondary_reinit_in_progress": state_flag("is_secondary_reinit_in_progress"),
        "is_first_launch_after_failover": state_flag("is_first_launch_after_failover"),
        "is_endpoint_migration_in_progress": state_flag(
            "is_cluster_endpoint_migration_in_progress"
        ),
        "last_online_time": _parse_ha_cluster_last_online(
            getattr(states, "last_online_time_utc", None) if states else None
        ),
        "primary": _parse_ha_cluster_node(
            getattr(cluster, "primary_node", None), get_enum_value, get_uuid_value
        ),
        "secondary": _parse_ha_cluster_node(
            getattr(cluster, "secondary_node", None), get_enum_value, get_uuid_value
        ),
    }

    # Anything Veeam adds later still reaches the diagnostics download
    for key, value in getattr(cluster, "additional_properties", {}).items():
        parsed.setdefault(key, serialize_value(value))

    return parsed


def _license_issue_id(entry: ConfigEntry) -> str:
    """Repair issue ID for one config entry's license warning."""
    return f"unsupported_license_{entry.entry_id}"


def _check_license_support(hass: HomeAssistant, entry: ConfigEntry, data: dict | None) -> None:
    """Warn when the server's license is outside what this integration supports.

    Raised as a repair issue rather than only a log line, so it is visible without digging
    through logs, and cleared automatically once the server reports a supported license.
    Never blocks setup: a Community Edition server that works is not worth refusing.
    """
    license_info = (data or {}).get("license_info")
    reason = unsupported_license_reason(license_info)
    issue_id = _license_issue_id(entry)

    if reason is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return

    _LOGGER.warning(
        "Veeam server %s reports license %s, which this integration does not support (%s). "
        "Setup will continue, but entities may be missing or unreliable. Please include the "
        "license edition when reporting problems",
        entry.data.get(CONF_HOST, "unknown"),
        describe_license(license_info),
        reason,
    )

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unsupported_license",
        translation_placeholders={
            "host": str(entry.data.get(CONF_HOST, "unknown")),
            "license": describe_license(license_info),
        },
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup & Replication from a config entry."""
    from veeam_br.client import VeeamClient

    api_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

    # Import UNSET type for proper type checking
    try:
        types_module = await asyncio.to_thread(
            importlib.import_module, f"veeam_br.{api_module}.types"
        )
        UNSET = types_module.UNSET
    except ImportError as err:
        _LOGGER.error("Failed to import veeam_br types: %s", err)
        return False

    # Teach the generated models to tolerate nulls where Veeam's schema promises a value,
    # before anything parses a response. Without this a single null timestamp, UUID or
    # nested object empties a whole endpoint (issues #82 and #83). Importing the models
    # package blocks, so this runs off the event loop.
    def patch_models() -> int:
        models_package = f"veeam_br.{api_module}.models"
        importlib.import_module(models_package)  # imports every model module eagerly
        return patch_null_values_in_models(models_package, UNSET, sys.modules)

    try:
        patched = await asyncio.to_thread(patch_models)
        _LOGGER.debug("Patched %d model modules to tolerate null values", patched)
    except Exception as err:  # noqa: BLE001 - never block setup over a resilience patch
        _LOGGER.warning(
            "Could not patch veeam_br models for null values (%s); jobs, repositories or "
            "license data may fail to load. See "
            "https://github.com/Cenvora/ha-veeam-br/issues/83",
            err,
        )

    # Pre-import API modules to avoid blocking calls in event loop
    # The veeam_br library uses dynamic imports which can block the event loop
    try:
        await asyncio.to_thread(
            importlib.import_module, f"veeam_br.{api_module}.api.login.create_token"
        )
    except ImportError as err:
        _LOGGER.warning("Failed to pre-import login module: %s", err)

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

    # Pre-import API endpoint modules to avoid blocking calls in event loop
    # The veeam_br library dynamically imports these modules during API calls
    # High Availability arrived in 1.3-rev2; on older versions the endpoints are not in the
    # SDK at all, so skip the call rather than fail it on every poll
    ha_cluster_supported = check_api_feature_availability(api_version, HA_CLUSTER_FEATURE)
    _LOGGER.debug("HA cluster endpoints available on %s: %s", api_version, ha_cluster_supported)

    api_endpoints = [
        "jobs.get_all_jobs_states",
        "service.get_server_info",
        "license_.get_installed_license",
        "repositories.get_all_repositories",
        "repositories.get_all_repositories_states",
        "repositories.get_all_scale_out_repositories",
    ]
    if ha_cluster_supported:
        api_endpoints.append("high_availability_ha_cluster.get_high_availability_cluster")
    for endpoint in api_endpoints:
        try:
            await asyncio.to_thread(
                importlib.import_module, f"veeam_br.{api_module}.api.{endpoint}"
            )
        except ImportError as err:
            _LOGGER.debug("Could not pre-import %s: %s", endpoint, err)

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

            # Helper to safely get UUID as string
            def get_uuid_value(uuid_val):
                """Extract UUID value."""
                if uuid_val is None or uuid_val is UNSET:
                    return None
                return str(uuid_val)

            # Fetch jobs data — wrapped in try/except so a parsing failure (e.g.
            # an API-version mismatch in the veeam_br library causing a ValueError
            # from dict()) degrades gracefully rather than aborting the whole setup.
            jobs_list = []
            try:
                jobs_api = await asyncio.to_thread(veeam_client.api, "jobs")
                jobs_response = await veeam_client.call(jobs_api.get_all_jobs_states)

                if not jobs_response or not hasattr(jobs_response, "data"):
                    _LOGGER.warning(
                        "Jobs API returned no data or an unexpected response object (%s); "
                        "job sensors will be unavailable",
                        type(jobs_response).__name__,
                    )
                else:
                    jobs_data = jobs_response.data

                    for job in jobs_data:
                        try:
                            # A null id parses as UNSET (see sdk_patches); entity unique
                            # IDs are built from it, so skip rather than emit "None" ones
                            job_id = get_uuid_value(job.id)
                            if not job_id:
                                _LOGGER.warning(
                                    "Skipping job %s: no usable ID",
                                    getattr(job, "name", "Unknown"),
                                )
                                continue

                            job_dict = {
                                "id": job_id,
                                "name": job.name or "Unknown",
                                "type": get_enum_value(job.type_),
                                "status": get_enum_value(job.status),
                                "last_result": get_enum_value(job.last_result),
                                "last_run": get_datetime_value(job.last_run),
                                "next_run": get_datetime_value(job.next_run),
                            }
                            jobs_list.append(job_dict)
                        except (ValueError, KeyError, AttributeError, TypeError) as err:
                            job_id = getattr(job, "id", "Unknown")
                            job_name = getattr(job, "name", "Unknown")
                            _LOGGER.warning(
                                "Failed to parse job (id=%s, name=%s): %s",
                                job_id,
                                job_name,
                                err,
                            )
                            continue
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to parse jobs API response (API version %s may not be fully "
                    "compatible): %s",
                    api_version,
                    err,
                )
            except Exception:
                # Let the top-level coordinator handler wrap this in UpdateFailed once.
                raise

            # Fetch server information
            server_info = None
            try:
                service_api = await asyncio.to_thread(veeam_client.api, "service")
                server_data = await veeam_client.call(service_api.get_server_info)
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
                license_api = await asyncio.to_thread(veeam_client.api, "license_")
                license_data = await veeam_client.call(license_api.get_installed_license)
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

                repositories_api = await asyncio.to_thread(veeam_client.api, "repositories")
                repositories_result = await veeam_client.call(repositories_api.get_all_repositories)
                repositories_states_result = await veeam_client.call(
                    repositories_api.get_all_repositories_states
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
                            # See the job loop: entity unique IDs need a usable ID
                            if not get_uuid_value(repo.id):
                                _LOGGER.warning(
                                    "Skipping repository %s: no usable ID",
                                    getattr(repo, "name", "Unknown"),
                                )
                                continue

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

                                # Immutability for Linux Hardened repos
                                # stored in additional_properties["repository"]["makeRecentBackupsImmutableDays"]
                                if "is_immutable" not in repo_dict:
                                    hlr_repo = repo.additional_properties.get("repository")
                                    if isinstance(hlr_repo, dict):
                                        hlr_days = hlr_repo.get("makeRecentBackupsImmutableDays")
                                        if hlr_days is not None:
                                            is_immutable = int(hlr_days) > 0
                                            repo_dict["is_immutable"] = is_immutable
                                            _LOGGER.info(
                                                "Repository %s: HLR immutability, makeRecentBackupsImmutableDays=%s, is_immutable=%s",
                                                repo_dict.get("name"),
                                                hlr_days,
                                                is_immutable,
                                            )
                                            if is_immutable:
                                                repo_dict["immutability_days"] = int(hlr_days)
                                                _LOGGER.debug(
                                                    "Repository %s: immutability_days=%s",
                                                    repo_dict.get("name"),
                                                    hlr_days,
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
                        except (ValueError, KeyError, AttributeError, TypeError) as err:
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
                sobr_api = await asyncio.to_thread(veeam_client.api, "repositories")
                sobr_result = await veeam_client.call(sobr_api.get_all_scale_out_repositories)

                if sobr_result:
                    sobr_data = sobr_result.data if sobr_result else []
                    _LOGGER.debug("Fetched %d scale-out repositories from API", len(sobr_data))

                    for sobr in sobr_data:
                        try:
                            # See the job loop: entity unique IDs need a usable ID
                            if not get_uuid_value(sobr.id):
                                _LOGGER.warning(
                                    "Skipping scale-out repository %s: no usable ID",
                                    getattr(sobr, "name", "Unknown"),
                                )
                                continue

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
                                        # In API v1.2-rev1, extent.status is a single
                                        # ERepositoryExtentStatusType (a str-subclass enum),
                                        # not a list.  In v1.3-rev1+ it is a list.
                                        # Handle both forms so iterating over the enum's
                                        # string characters (which would raise AttributeError
                                        # on .value for each char) is avoided.
                                        raw_status = (
                                            extent.status if extent.status is not UNSET else []
                                        )
                                        if isinstance(raw_status, list):
                                            status_values = [s.value for s in raw_status]
                                        elif hasattr(raw_status, "value"):
                                            status_values = [raw_status.value]
                                        else:
                                            status_values = []
                                        extent_dict = {
                                            "id": get_uuid_value(extent.id),
                                            "name": extent.name or "Unknown",
                                            "status": status_values,
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
                        except (ValueError, KeyError, AttributeError, TypeError) as err:
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

            # Fetch High Availability cluster configuration and state. Only reachable on
            # 1.3-rev2 and newer, and only answered by a server that is actually clustered —
            # an unclustered server is the common case, not an error.
            ha_cluster = None
            if ha_cluster_supported:
                try:
                    ha_api = await asyncio.to_thread(
                        veeam_client.api, "high_availability_ha_cluster"
                    )
                    cluster = await veeam_client.call(ha_api.get_high_availability_cluster)

                    if cluster is None or not hasattr(cluster, "cluster_endpoint"):
                        # None, or an Error model: this server is not clustered
                        _LOGGER.debug(
                            "No HA cluster reported by this server (%s)",
                            type(cluster).__name__,
                        )
                    else:
                        ha_cluster = _parse_ha_cluster(
                            cluster, get_enum_value, get_uuid_value, serialize_value
                        )
                        _LOGGER.debug(
                            "Parsed HA cluster %s (online=%s)",
                            ha_cluster.get("name"),
                            ha_cluster.get("is_online"),
                        )
                except (AttributeError, KeyError, TypeError, ValueError) as err:
                    _LOGGER.warning("Failed to parse HA cluster: %s", err)
                except Exception as err:
                    _LOGGER.warning("Failed to fetch HA cluster: %s", err)

            # Update diagnostic values - successful poll
            health_ok = True
            last_successful_poll = dt_util.now()

            return {
                "jobs": jobs_list,
                "server_info": server_info,
                "license_info": license_info,
                "repositories": repositories_list,
                "sobrs": sobr_list,
                "ha_cluster": ha_cluster,
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

    # Runs on every setup, so a reload re-reports it and a newly licensed server clears it
    _check_license_support(hass, entry, coordinator.data)

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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a removed config entry.

    Deleted here rather than on unload, which also runs on every reload — the warning would
    otherwise disappear and come back on each restart.
    """
    ir.async_delete_issue(hass, DOMAIN, _license_issue_id(entry))
