"""The Veeam Backup & Replication integration."""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone
import importlib
import logging
import sys
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.ssl import get_default_context, get_default_no_verify_context
import httpx
from veeam_br.client import VeeamClient
from veeam_br.exceptions import VeeamAuthenticationError, VeeamSessionError

from .api_version import ServerUnreachableError, async_resolve_api_version
from .const import (
    API_VERSIONS,
    AUTO_API_VERSION,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    CONNECT_TIMEOUT,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    FEATURE_HA_CLUSTER,
    FEATURE_MALWARE_EVENTS,
    FEATURE_MALWARE_OBJECTS,
    FEATURE_MANAGE_MOVE_COPY,
    FEATURE_MOVE_COPY_SESSIONS,
    FEATURE_PROXY_STATES,
    FEATURE_RECOVERY_APPLIANCES,
    FEATURE_SECURITY_ANALYZER,
    FEATURE_SECURITY_ANALYZER_LAST_RUN,
    FEATURE_SECURITY_ANALYZER_START,
    PAGE_LIMIT,
    REQUEST_TIMEOUT,
    UPDATE_INTERVAL,
    UPDATE_TIMEOUT,
    check_api_feature_availability,
    warm_feature_cache,
)
from .display import describe_error, humanize
from .licensing import describe_license, unsupported_license_reason
from .malware import (
    DETECTION_TIME_ONLY,
    EVENT_MALWARE,
    RECENT_HOURS,
    SEVERITIES_OF_CONCERN,
    MalwareEventTracker,
    count_by_severity,
    parse_event,
    parse_object,
    summarize_objects,
)
from .move_copy import EVENT_MOVE_COPY, parse_session, summarize_sessions
from .pruning import async_prune_stale
from .recovery_appliances import EVENT_RECOVERY_APPLIANCE, parse_appliance, summarize_appliances
from .sdk_patches import patch_models as patch_null_values_in_models
from .security_analyzer import (
    EVENT_BEST_PRACTICE_VIOLATION,
    ViolationTracker,
    parse_best_practice,
    parse_last_run,
    summarize as summarize_security_analyzer,
)
from .services import async_setup_services
from .tracking import NewItemTracker, with_iso_times

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Failures to reach the server at all, as veeam-br lets them through. TimeoutError is an
# OSError, so it is covered too.
TRANSPORT_ERRORS = (httpx.HTTPError, OSError)

# Safety stop for paging: a server whose pagination never adds up must not loop forever
MAX_PAGES = 100

# Coordinator data keys, one per endpoint, as reported in data["fetch_ok"]. The platforms
# and the pruning read these to tell "the server no longer reports it" from "this cycle's
# fetch failed".
ENDPOINT_LABELS = {
    "jobs": "jobs",
    "server_info": "server info",
    "license_info": "license info",
    "repositories": "repositories",
    "repository_states": "repository states",
    "sobrs": "scale-out repositories",
    "proxies": "proxies",
    "wan_accelerators": "WAN accelerators",
    "ha_cluster": "HA cluster",
    "malware_events": "malware events",
    "malware_objects": "malware detection objects",
    "move_copy_sessions": "move/copy sessions awaiting action",
    "recovery_appliances": "recovery appliances",
    "security_analyzer": "Security & Compliance Analyzer results",
}

# Repository types that have no immutability of their own. Their Immutable sensor reads
# "off" instead of unknown. Types not listed — deduplicating appliances, object storage — are
# only reported when the server says so. Compared case-insensitively against the raw type.
NON_IMMUTABLE_REPOSITORY_TYPES = frozenset({"winlocal", "smb", "nfs"})


class UnexpectedResponseError(Exception):
    """An endpoint answered, but not with the data it documents.

    The generated clients return an Error model for a documented failure and None for an
    undocumented status rather than raising, so without this those read as an empty list.
    """


def _refused_by_role(result: Any) -> bool:
    """Whether an Error model is the server refusing this account's role (HTTP 403)."""
    extras = getattr(result, "additional_properties", None) or {}
    return isinstance(extras, dict) and extras.get("status") == 403


def _not_found(result: Any) -> bool:
    extras = getattr(result, "additional_properties", None) or {}
    return isinstance(extras, dict) and extras.get("status") == 404


def _describe_response(result: Any) -> str:
    """Explain an endpoint result that is not the documented data."""
    if result is None:
        return "no data (the server answered with an undocumented status)"

    message = getattr(result, "message", None)
    code = getattr(result, "error_code", None)
    code = getattr(code, "value", code)
    extras = getattr(result, "additional_properties", None) or {}
    status = extras.get("status") if isinstance(extras, dict) else None

    parts = [type(result).__name__]
    if status:
        parts.append(f"HTTP {status}")
    if isinstance(code, str) and code:
        parts.append(code)
    if isinstance(message, str) and message:
        parts.append(message)
    return ": ".join(parts)


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
        "role": humanize(get_enum_value(getattr(node, "role", None)), "Unknown"),
        "role_raw": get_enum_value(getattr(node, "role", None)),
        "state": humanize(get_enum_value(getattr(node, "state", None)), "Unknown"),
        "state_raw": get_enum_value(getattr(node, "state", None)),
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


def _is_unset(value) -> bool:
    """Whether a generated model returned its "absent" sentinel."""
    return value is None or getattr(type(value), "__name__", "") == "Unset"


# The per-package summaries, in the order to trust them. Capacity carries no dates.
LICENSE_SUMMARIES = (
    "instance_license_summary",
    "socket_license_summary",
    "capacity_license_summary",
)


def _license_datetime(license_data, field: str):
    """Read a license date, wherever this API revision keeps it.

    1.2-rev1 exposes expirationDate and supportExpirationDate on the license itself. In
    1.3-rev* those fields are gone from the top level and live only inside the per-package
    summary (instanceLicenseSummary, socketLicenseSummary), so reading the top level alone
    leaves the sensor unknown on any 13.x server.

    A 1.3 server that still sends a top-level date has it land in additional_properties as a
    raw string, since the 1.3 model no longer declares the field; that is read last.

    None means the server reported the date nowhere — normal for a license with no support
    contract, such as NFR or evaluation, which has no support expiration at all.
    """
    direct = getattr(license_data, field, None)
    if not _is_unset(direct):
        return direct

    for summary_name in LICENSE_SUMMARIES:
        summary = getattr(license_data, summary_name, None)
        if _is_unset(summary):
            continue
        value = getattr(summary, field, None)
        if not _is_unset(value):
            return value

    extras = getattr(license_data, "additional_properties", None)
    if isinstance(extras, dict):
        head, *rest = field.split("_")
        raw = extras.get(head + "".join(word.capitalize() for word in rest))
        if isinstance(raw, str) and raw:
            parsed = dt_util.parse_datetime(raw)
            if parsed is not None:
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    return None


def _license_text(license_data, field: str, default: str = "Unknown") -> str:
    """Read a license string, treating blank as absent.

    A license with no support contract reports supportId as "", which would otherwise show as
    an empty sensor rather than as unknown.
    """
    value = getattr(license_data, field, None)
    if _is_unset(value):
        return default
    text = str(value).strip()
    return text or default


def _number_or_none(obj, name: str):
    """Read a numeric field, mapping UNSET and non-numbers to None."""
    value = getattr(obj, name, None)
    if _is_unset(value) or isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _license_instance_usage(license_data) -> dict:
    """Instance-based licensing figures, or an empty dict for other license types.

    Socket and capacity licenses count different units, so instance sensors would be
    meaningless for them and are not created at all.
    """
    summary = getattr(license_data, "instance_license_summary", None)
    if _is_unset(summary):
        return {}

    licensed = _number_or_none(summary, "licensed_instances_number")
    used = _number_or_none(summary, "used_instances_number")

    usage = {
        "package": _license_text(summary, "package", default=None),
        "instances_licensed": licensed,
        "instances_used": used,
        "instances_new": _number_or_none(summary, "new_instances_number"),
        "instances_rental": _number_or_none(summary, "rental_instances_number"),
    }

    if licensed and used is not None:
        usage["instances_used_percent"] = round(used / licensed * 100, 1)

    # The per-type breakdown is small and stable. The full workload list is not — a large
    # estate has thousands of entries, which have no business in a state attribute.
    objects = getattr(summary, "objects", None)
    if not _is_unset(objects):
        usage["instance_objects"] = [
            {
                "type": _license_text(item, "type_", default="Unknown"),
                "count": _number_or_none(item, "count"),
                "used": _number_or_none(item, "used_instances_number"),
            }
            for item in objects
        ]

    workload = getattr(summary, "workload", None)
    if not _is_unset(workload):
        usage["instance_workload_count"] = len(workload)

    return usage


def _cluster_absent_reason(result) -> tuple[bool, str]:
    """Whether an HA cluster response means "not clustered", and what to say about it.

    A server with no cluster answers 400 with an Error saying exactly that, which is the
    normal case and not worth a warning. But 401, 403 and 500 come back as an Error too, and
    treating those as "no cluster" would hide a real failure behind a debug line.
    """
    message = _license_text(result, "message", default="")
    extras = getattr(result, "additional_properties", {}) or {}
    status_code = extras.get("status")

    if status_code == 400 or "not configured" in message.lower():
        return True, message or "no cluster configured"

    detail = f"HTTP {status_code}: {message}" if status_code else message
    return False, detail or type(result).__name__


def _repository_immutability(type_raw, extras) -> tuple[bool | None, int | None]:
    """Whether a repository keeps backups immutable, and for how many days.

    Veeam reports this in a different place for each kind of repository, all of them in the
    polymorphic part of the payload that lands in additional_properties:

    * object storage: ``bucket.immutability`` (or ``container.immutability``) with
      ``isEnabled`` and ``daysCount``
    * Linux hardened: ``repository.makeRecentBackupsImmutableDays``
    * Linux local on 13.x: ``repository.enableGovernanceMode`` and
      ``governanceModeRetentionDays``
    * Data Domain and StoreOnce: ``repository.immutability``

    Windows, SMB and NFS repositories have no immutability at all, so they report False
    rather than unknown. Anything else unreported stays unknown.
    """
    extras = extras if isinstance(extras, dict) else {}

    def from_block(block) -> tuple[bool | None, int | None]:
        if not isinstance(block, dict) or block.get("isEnabled") is None:
            return None, None
        enabled = bool(block["isEnabled"])
        days = block.get("daysCount") if enabled else None
        return enabled, days if isinstance(days, int) and not isinstance(days, bool) else None

    for storage_key in ("bucket", "container"):
        storage = extras.get(storage_key)
        if isinstance(storage, dict):
            enabled, days = from_block(storage.get("immutability"))
            if enabled is not None:
                return enabled, days

    settings = extras.get("repository")
    if isinstance(settings, dict):
        hardened_days = settings.get("makeRecentBackupsImmutableDays")
        if isinstance(hardened_days, (int, float)) and not isinstance(hardened_days, bool):
            days = int(hardened_days)
            return days > 0, days if days > 0 else None

        governance = settings.get("enableGovernanceMode")
        if isinstance(governance, bool):
            days = settings.get("governanceModeRetentionDays") if governance else None
            return governance, days if isinstance(days, int) else None

        enabled, days = from_block(settings.get("immutability"))
        if enabled is not None:
            return enabled, days

    type_key = type_raw.lower() if isinstance(type_raw, str) else ""
    if type_key in NON_IMMUTABLE_REPOSITORY_TYPES:
        return False, None
    if type_key == "linuxlocal":
        # Before 13.x a plain Linux repository had no immutability; from 13.x it reports
        # enableGovernanceMode, which is handled above
        return False, None

    return None, None


# Device identifier prefixes, mapped to the coordinator data that keeps them alive
DEVICE_KINDS = {
    "job_": "jobs",
    "repository_": "repositories",
    "sobr_": "sobrs",
    "proxy_": "proxies",
    "wan_": "wan_accelerators",
}
SINGLETON_KINDS = {
    "server_": "server_info",
    "license_": "license_info",
    "ha_cluster_": "ha_cluster",
    "security_": "malware_events",
}


def device_is_current(identifiers, data: dict | None, entry_id: str) -> bool:
    """Whether a device still corresponds to something the server reports.

    Used to decide whether Home Assistant should let the user delete a device. A device that
    is still being reported would simply reappear on the next poll, so refusing is kinder than
    letting someone delete it twice.

    An identifier this version does not recognise counts as stale: it cannot be something the
    current code maintains.
    """
    if not data:
        # Nothing to compare against — let the user clean up rather than blocking them
        return False

    for domain, identifier in identifiers:
        if domain != DOMAIN:
            continue

        for prefix, key in DEVICE_KINDS.items():
            if identifier.startswith(prefix):
                wanted = identifier[len(prefix) :]
                return any(item.get("id") == wanted for item in data.get(key) or [])

        for prefix, key in SINGLETON_KINDS.items():
            if identifier.startswith(prefix):
                # These are one per config entry, so the suffix is the entry id
                return identifier == f"{prefix}{entry_id}" and data.get(key) is not None

    return False


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry, device) -> bool:
    """Allow deleting a device from the UI once the server stops reporting it.

    Without this, Home Assistant offers no Delete button at all and a job or repository that
    no longer exists can only be disabled. Deletion is refused while the object is still
    being reported, because the next poll would recreate it.
    """
    runtime = getattr(entry, "runtime_data", None) or {}
    coordinator = runtime.get("coordinator")
    data = getattr(coordinator, "data", None)

    if device_is_current(device.identifiers, data, entry.entry_id):
        _LOGGER.debug(
            "Refusing to remove %s: the server still reports it, so it would come back",
            device.name,
        )
        return False

    _LOGGER.info("Removing device %s at the user's request", device.name)
    return True


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


def _prepare_sdk(api_version: str, api_module: str) -> Any:
    """Import everything the coordinator and the platforms use, then patch the models.

    Blocking — imports read the filesystem — so it runs in an executor, as one job to keep
    setup lean. veeam-br resolves modules with importlib at call time, which would otherwise
    land on the event loop. Returns the SDK's UNSET sentinel.

    Importing any endpoint imports the whole models package, so patching every model costs
    nothing extra. The patch teaches them to tolerate nulls and unknown enum values where
    Veeam's schema promises otherwise; without it a single null timestamp, UUID or nested
    object empties a whole endpoint (issues #82 and #83).
    """
    package = f"veeam_br.{api_module}"
    unset = importlib.import_module(f"{package}.types").UNSET

    models_package = f"{package}.models"
    importlib.import_module(models_package)  # imports every model module eagerly
    try:
        patched = patch_null_values_in_models(models_package, unset, sys.modules)
        _LOGGER.debug("Patched %d model modules to tolerate null values", patched)
    except Exception as err:  # noqa: BLE001 - never block setup over a resilience patch
        _LOGGER.warning(
            "Could not patch veeam_br models for null values (%s); jobs, repositories or "
            "license data may fail to load. See "
            "https://github.com/Cenvora/ha-veeam-br/issues/83",
            err,
        )

    warm_feature_cache(api_version)
    proxies_endpoint = (
        "get_all_proxies_states"
        if check_api_feature_availability(api_version, FEATURE_PROXY_STATES)
        else "get_all_proxies"
    )

    modules = [
        f"{package}.client",
        f"{package}.api.login.create_token",
        f"{package}.models.token_login_spec",
        f"{package}.models.e_login_grant_type",
        f"{package}.api.jobs.get_all_jobs_states",
        f"{package}.api.service.get_server_info",
        f"{package}.api.license_.get_installed_license",
        f"{package}.api.repositories.get_all_repositories",
        f"{package}.api.repositories.get_all_repositories_states",
        f"{package}.api.repositories.get_all_scale_out_repositories",
        f"{package}.api.proxies.{proxies_endpoint}",
        f"{package}.api.wan_accelerators.get_all_wan_accelerators",
    ]
    if check_api_feature_availability(api_version, FEATURE_HA_CLUSTER):
        modules.append(f"{package}.api.high_availability_ha_cluster.get_high_availability_cluster")
    for feature in (
        FEATURE_MALWARE_EVENTS,
        FEATURE_MALWARE_OBJECTS,
        FEATURE_MOVE_COPY_SESSIONS,
        FEATURE_MANAGE_MOVE_COPY,
        FEATURE_RECOVERY_APPLIANCES,
        FEATURE_SECURITY_ANALYZER,
        FEATURE_SECURITY_ANALYZER_LAST_RUN,
        FEATURE_SECURITY_ANALYZER_START,
    ):
        if check_api_feature_availability(api_version, feature):
            modules.append(f"{package}.{feature}")

    for module in modules:
        try:
            importlib.import_module(module)
        except ImportError as err:
            _LOGGER.debug("Could not pre-import %s: %s", module, err)

    return unset


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service actions, which name the config entry they act on."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Veeam Backup & Replication from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    base_url = f"https://{host}:{port}"
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    # "auto" is stored as the user's intent, not a version, so it is resolved on every setup
    # — which means a restart picks up a server upgrade or a newer veeam-br automatically.
    stored_version = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    if stored_version == AUTO_API_VERSION:
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                api_version = await async_resolve_api_version(
                    hass, {**entry.data, CONF_API_VERSION: AUTO_API_VERSION}
                )
        except (ServerUnreachableError, TimeoutError) as err:
            # Falling back to the default here would pin the entry to a revision chosen while
            # the server was down; retrying is the only answer that detects the right one
            raise ConfigEntryNotReady(
                f"Could not reach {host}:{port} to detect its API version: "
                f"{describe_error(err)}"
            ) from err
        _LOGGER.info("API version is set to auto; using %s for %s", api_version, host)
    else:
        api_version = stored_version

    if api_version not in API_VERSIONS:
        # A pinned revision the installed veeam-br no longer ships. Retrying cannot help;
        # the options flow lists what is available.
        raise ConfigEntryError(
            f"API version {api_version} is not supported by the installed veeam-br "
            f"(supported: {', '.join(API_VERSIONS)}). Choose another in the integration's "
            "options"
        )
    api_module = API_VERSIONS[api_version]

    try:
        UNSET = await hass.async_add_executor_job(_prepare_sdk, api_version, api_module)
    except ImportError as err:
        raise ConfigEntryError(f"Failed to import veeam_br {api_module}: {err}") from err

    # A ready-made context: building one loads the CA bundle, which blocks the event loop
    ssl_context = get_default_context() if verify_ssl else get_default_no_verify_context()

    # VeeamClient handles token refresh itself, and applies the timeout to every request
    veeam_client = VeeamClient(
        host=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        api_version=api_version,
        verify_ssl=ssl_context,
        timeout=REQUEST_TIMEOUT,
    )

    try:
        async with asyncio.timeout(CONNECT_TIMEOUT):
            await veeam_client.connect()
    except VeeamAuthenticationError as err:
        await veeam_client.close()
        raise ConfigEntryAuthFailed(f"Veeam rejected the credentials for {host}: {err}") from err
    except Exception as err:  # transport errors, timeouts, anything else: try again later
        await veeam_client.close()
        raise ConfigEntryNotReady(
            f"Could not log in to {host}:{port}: {describe_error(err)}"
        ) from err

    ha_cluster_supported = check_api_feature_availability(api_version, FEATURE_HA_CLUSTER)
    _LOGGER.debug("HA cluster endpoints available on %s: %s", api_version, ha_cluster_supported)

    malware_events_supported = check_api_feature_availability(api_version, FEATURE_MALWARE_EVENTS)
    malware_objects_supported = check_api_feature_availability(api_version, FEATURE_MALWARE_OBJECTS)
    # Already imported by _prepare_sdk, so this is a lookup rather than a disk read
    models = importlib.import_module(f"veeam_br.{api_module}.models")
    malware_tracker = MalwareEventTracker()

    move_copy_supported = check_api_feature_availability(api_version, FEATURE_MOVE_COPY_SESSIONS)
    move_copy_tracker = NewItemTracker()
    # Cleared when the server refuses the account (Backup Administrator only), until a reload
    move_copy_permitted = True

    recovery_appliances_supported = check_api_feature_availability(
        api_version, FEATURE_RECOVERY_APPLIANCES
    )
    recovery_appliance_tracker = NewItemTracker()

    security_analyzer_supported = check_api_feature_availability(
        api_version, FEATURE_SECURITY_ANALYZER
    ) and check_api_feature_availability(api_version, FEATURE_SECURITY_ANALYZER_LAST_RUN)
    violation_tracker = ViolationTracker()
    # Cleared when the server refuses the account (Backup or Security Administrator only)
    security_analyzer_permitted = True

    proxy_states_supported = check_api_feature_availability(api_version, FEATURE_PROXY_STATES)
    proxies_endpoint = "get_all_proxies_states" if proxy_states_supported else "get_all_proxies"
    _LOGGER.debug("Proxy states endpoint available on %s: %s", api_version, proxy_states_supported)

    # Endpoints currently failing, so a persistent failure is logged when it starts and when
    # it ends rather than on every poll
    failing_endpoints: dict[str, str] = {}
    # Repositories already reported as missing from the states response
    stateless_repositories: set[str] = set()

    # Helper function to safely get enum value
    def get_enum_value(enum_val, default="unknown"):
        """Extract enum value, handling enum members, unknown values kept as str, and UNSET."""
        if enum_val is None or enum_val is UNSET:
            return default
        # Enum members, and UnknownEnumValue from sdk_patches, both answer .value
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

    async def fetch_all(operation, what: str, **filters) -> list:
        """Every item of a collection endpoint, following pagination.

        The 1.3 revisions return at most 200 items unless asked for more, and say how many
        there are in pagination.total. Raises UnexpectedResponseError when a page is not a
        result at all, so a refused request is not mistaken for an empty collection.
        """
        items: list = []
        skip = 0
        for _ in range(MAX_PAGES):
            result = await veeam_client.call(operation, skip=skip, limit=PAGE_LIMIT, **filters)
            page = getattr(result, "data", None)
            if not isinstance(page, list):
                raise UnexpectedResponseError(
                    f"{what} endpoint answered {_describe_response(result)}"
                )
            items.extend(page)

            total = getattr(getattr(result, "pagination", None), "total", None)
            if not page:
                break
            if isinstance(total, int) and not isinstance(total, bool):
                if len(items) >= total:
                    break
            elif len(page) < PAGE_LIMIT:
                break
            skip += len(page)
        else:
            _LOGGER.warning(
                "Stopped paging %s after %d pages (%d items); the list may be incomplete",
                what,
                MAX_PAGES,
                len(items),
            )
        return items

    def note_failure(key: str, err: BaseException) -> None:
        """Log an endpoint failure when it starts; stay quiet while it persists."""
        detail = describe_error(err)
        if key not in failing_endpoints:
            _LOGGER.warning(
                "Failed to fetch %s: %s. Its entities are unavailable until it recovers",
                ENDPOINT_LABELS[key],
                detail,
            )
        else:
            _LOGGER.debug("Still failing to fetch %s: %s", ENDPOINT_LABELS[key], detail)
        _LOGGER.debug("Fetching %s failed", ENDPOINT_LABELS[key], exc_info=err)
        failing_endpoints[key] = detail

    def note_success(key: str) -> None:
        if failing_endpoints.pop(key, None) is not None:
            _LOGGER.info("Fetching %s works again", ENDPOINT_LABELS[key])

    async def fetch_jobs() -> list[dict]:
        jobs_api = veeam_client.api("jobs")
        jobs_list = []
        for job in await fetch_all(jobs_api.get_all_jobs_states, "jobs"):
            try:
                # A null id parses as UNSET (see sdk_patches); entity unique IDs are built
                # from it, so skip rather than emit "None" ones
                job_id = get_uuid_value(job.id)
                if not job_id:
                    _LOGGER.warning(
                        "Skipping job %s: no usable ID", getattr(job, "name", "Unknown")
                    )
                    continue

                jobs_list.append(
                    {
                        "id": job_id,
                        "name": job.name or "Unknown",
                        "type": humanize(get_enum_value(job.type_), "Unknown"),
                        "type_raw": get_enum_value(job.type_),
                        "status": humanize(get_enum_value(job.status), "Unknown"),
                        "status_raw": get_enum_value(job.status),
                        "last_result": humanize(get_enum_value(job.last_result), "Unknown"),
                        "last_result_raw": get_enum_value(job.last_result),
                        "last_run": get_datetime_value(job.last_run),
                        "next_run": get_datetime_value(job.next_run),
                    }
                )
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to parse job (id=%s, name=%s): %s",
                    getattr(job, "id", "Unknown"),
                    getattr(job, "name", "Unknown"),
                    describe_error(err),
                )
        return jobs_list

    async def fetch_server_info() -> dict:
        service_api = veeam_client.api("service")
        server_data = await veeam_client.call(service_api.get_server_info)
        if not hasattr(server_data, "build_version"):
            # An Error model is truthy, and reading it with defaults produced a server
            # device full of "Unknown"
            raise UnexpectedResponseError(
                f"server info endpoint answered {_describe_response(server_data)}"
            )
        platform = getattr(server_data, "platform", None)
        return {
            "vbr_id": getattr(server_data, "vbr_id", "Unknown"),
            "name": getattr(server_data, "name", "Unknown"),
            "build_version": getattr(server_data, "build_version", "Unknown"),
            "patches": getattr(server_data, "patches", []),
            "database_vendor": getattr(server_data, "database_vendor", "Unknown"),
            "sql_server_edition": getattr(server_data, "sql_server_edition", "Unknown"),
            "sql_server_version": getattr(server_data, "sql_server_version", "Unknown"),
            "database_schema_version": getattr(server_data, "database_schema_version", "Unknown"),
            "database_content_version": getattr(server_data, "database_content_version", "Unknown"),
            "platform": (
                platform.value if hasattr(platform, "value") else str(platform or "Unknown")
            ),
        }

    async def fetch_license_info() -> dict:
        license_api = veeam_client.api("license_")
        license_data = await veeam_client.call(license_api.get_installed_license)
        if not hasattr(license_data, "status") or not hasattr(license_data, "edition"):
            raise UnexpectedResponseError(
                f"license endpoint answered {_describe_response(license_data)}"
            )

        def get_license_enum_attr(obj, attr_name, default="Unknown"):
            """Extract enum value from object attribute, handling both enum types and UNSET."""
            attr = getattr(obj, attr_name, None)
            if attr is None or _is_unset(attr):
                return default
            if hasattr(attr, "value"):
                return attr.value
            return str(attr)

        return {
            "status": humanize(get_license_enum_attr(license_data, "status"), "Unknown"),
            "status_raw": get_license_enum_attr(license_data, "status"),
            "edition": humanize(get_license_enum_attr(license_data, "edition"), "Unknown"),
            "edition_raw": get_license_enum_attr(license_data, "edition"),
            # Note: type_ with underscore
            "type": humanize(get_license_enum_attr(license_data, "type_"), "Unknown"),
            "type_raw": get_license_enum_attr(license_data, "type_"),
            "expiration_date": _license_datetime(license_data, "expiration_date"),
            "support_expiration_date": _license_datetime(license_data, "support_expiration_date"),
            "support_id": _license_text(license_data, "support_id"),
            "auto_update_enabled": getattr(license_data, "auto_update_enabled", False),
            "licensed_to": _license_text(license_data, "licensed_to"),
            "cloud_connect": get_license_enum_attr(license_data, "cloud_connect"),
            "free_agent_instance_consumption_enabled": getattr(
                license_data, "free_agent_instance_consumption_enabled", False
            ),
            **_license_instance_usage(license_data),
        }

    async def fetch_repository_states(repositories_api) -> dict | None:
        """Repository states by ID, or None when they could not be fetched."""
        try:
            states = await fetch_all(
                repositories_api.get_all_repositories_states, "repository states"
            )
        except (VeeamAuthenticationError, VeeamSessionError):
            raise
        except Exception as err:  # noqa: BLE001 - the repositories themselves still load
            note_failure("repository_states", err)
            return None

        note_success("repository_states")
        states_by_id = {}
        for state in states:
            repo_id = get_uuid_value(getattr(state, "id", None))
            if repo_id:
                states_by_id[repo_id] = state
        _LOGGER.debug("Fetched %d repository states from API", len(states_by_id))
        return states_by_id

    async def fetch_repositories() -> tuple[list[dict], bool]:
        repositories_api = veeam_client.api("repositories")
        repositories_data = await fetch_all(repositories_api.get_all_repositories, "repositories")
        _LOGGER.debug("Fetched %d repositories from API", len(repositories_data))
        states_by_id = await fetch_repository_states(repositories_api)

        repositories_list = []
        for repo in repositories_data:
            try:
                # See the job loop: entity unique IDs need a usable ID
                repo_id = get_uuid_value(repo.id)
                if not repo_id:
                    _LOGGER.warning(
                        "Skipping repository %s: no usable ID", getattr(repo, "name", "Unknown")
                    )
                    continue

                type_raw = get_enum_value(repo.type_)
                repo_dict = {
                    "id": repo_id,
                    "name": repo.name or "Unknown",
                    "description": repo.description or "",
                    "type": humanize(type_raw, "Unknown"),
                    "type_raw": type_raw,
                    "unique_id": (repo.unique_id if repo.unique_id is not UNSET else None),
                    # Whether capacity and online state are known this cycle. False when the
                    # states endpoint failed or did not list this repository; the entities
                    # that depend on it are then unavailable rather than silently unknown.
                    "has_state": False,
                }

                state = states_by_id.get(repo_id) if states_by_id is not None else None
                if state is not None:
                    repo_dict["has_state"] = True
                    repo_dict["capacity_gb"] = _number_or_none(state, "capacity_gb")
                    repo_dict["free_gb"] = _number_or_none(state, "free_gb")
                    repo_dict["used_space_gb"] = _number_or_none(state, "used_space_gb")
                    repo_dict["is_online"] = _bool_or_none(state, "is_online")
                    repo_dict["is_out_of_date"] = _bool_or_none(state, "is_out_of_date")
                    stateless_repositories.discard(repo_id)
                elif states_by_id is not None and repo_id not in stateless_repositories:
                    # Reported once per repository: it persists, and says nothing new later
                    stateless_repositories.add(repo_id)
                    _LOGGER.warning(
                        "Repository %s (id %s, type %s) is not in the repository states "
                        "response, which listed %d repositories. Its capacity, free space "
                        "and online state are unavailable. Veeam does not report states "
                        "for every repository type, or for a repository it cannot reach",
                        repo_dict["name"],
                        repo_id,
                        type_raw,
                        len(states_by_id),
                    )

                extras = getattr(repo, "additional_properties", None) or {}
                is_immutable, immutability_days = _repository_immutability(type_raw, extras)
                if is_immutable is not None:
                    repo_dict["is_immutable"] = is_immutable
                if immutability_days is not None:
                    repo_dict["immutability_days"] = immutability_days
                _LOGGER.debug(
                    "Repository %s: type=%s immutable=%s days=%s",
                    repo_dict["name"],
                    type_raw,
                    is_immutable,
                    immutability_days,
                )

                # Accessible - use is_online from state as a proxy
                repo_dict["is_accessible"] = repo_dict.get("is_online")

                # Add all additional properties from the API response, without letting them
                # replace the fields parsed above
                for key, value in extras.items():
                    repo_dict.setdefault(key, serialize_value(value))

                repositories_list.append(repo_dict)
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to parse repository %s: %s",
                    getattr(repo, "name", "Unknown"),
                    describe_error(err),
                )

        _LOGGER.debug("Total repositories added to coordinator data: %d", len(repositories_list))
        return repositories_list, states_by_id is not None

    async def fetch_sobrs() -> list[dict]:
        sobr_api = veeam_client.api("repositories")
        sobr_list = []
        for sobr in await fetch_all(
            sobr_api.get_all_scale_out_repositories, "scale-out repositories"
        ):
            try:
                # See the job loop: entity unique IDs need a usable ID
                sobr_id = get_uuid_value(sobr.id)
                if not sobr_id:
                    _LOGGER.warning(
                        "Skipping scale-out repository %s: no usable ID",
                        getattr(sobr, "name", "Unknown"),
                    )
                    continue

                sobr_dict = {
                    "id": sobr_id,
                    "name": sobr.name or "Unknown",
                    "description": sobr.description or "",
                    "unique_id": (sobr.unique_id if sobr.unique_id is not UNSET else None),
                }

                # Extract performance tier extents
                performance_tier = getattr(sobr, "performance_tier", None)
                if performance_tier and not _is_unset(performance_tier):
                    extents = []
                    for extent in getattr(performance_tier, "performance_extents", None) or []:
                        # In API v1.2-rev1, extent.status is a single enum (a str
                        # subclass), not a list; in v1.3-rev1+ it is a list. Iterating the
                        # enum would walk its characters, so both forms are handled.
                        raw_status = extent.status if extent.status is not UNSET else []
                        if isinstance(raw_status, list):
                            status_values = [get_enum_value(s) for s in raw_status]
                        elif hasattr(raw_status, "value"):
                            status_values = [raw_status.value]
                        else:
                            status_values = []
                        extents.append(
                            {
                                "id": get_uuid_value(extent.id),
                                "name": extent.name or "Unknown",
                                "status": status_values,
                            }
                        )
                    sobr_dict["extents"] = extents

                # Add all additional properties from the API response
                for key, value in (getattr(sobr, "additional_properties", None) or {}).items():
                    sobr_dict.setdefault(key, serialize_value(value))

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
                    describe_error(err),
                )
        return sobr_list

    async def fetch_proxies() -> list[dict]:
        # The states endpoint carries the configuration this needs as well as online/
        # disabled/out-of-date, so one call covers both — where it exists. Before 1.3-rev0
        # there is only the configuration endpoint, and the three state fields read back as
        # None (issue #104).
        proxies_api = veeam_client.api("proxies")
        proxies_list = []
        for proxy in await fetch_all(getattr(proxies_api, proxies_endpoint), "proxies"):
            try:
                proxy_id = get_uuid_value(proxy.id)
                if not proxy_id:
                    _LOGGER.warning(
                        "Skipping proxy %s: no usable ID", getattr(proxy, "name", "Unknown")
                    )
                    continue

                proxies_list.append(
                    {
                        "id": proxy_id,
                        "name": proxy.name or "Unknown",
                        "description": getattr(proxy, "description", "") or "",
                        "type": humanize(get_enum_value(proxy.type_), "Unknown"),
                        "type_raw": get_enum_value(proxy.type_),
                        "host_id": get_uuid_value(getattr(proxy, "host_id", None)),
                        "host_name": getattr(proxy, "host_name", None) or None,
                        "is_online": _bool_or_none(proxy, "is_online"),
                        "is_disabled": _bool_or_none(proxy, "is_disabled"),
                        "is_out_of_date": _bool_or_none(proxy, "is_out_of_date"),
                    }
                )
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to parse proxy %s: %s",
                    getattr(proxy, "name", "Unknown"),
                    describe_error(err),
                )
        return proxies_list

    async def fetch_wan_accelerators() -> list[dict]:
        # There is no states endpoint for these, so this is configuration only: cache
        # location and size, and the traffic settings.
        wan_api = veeam_client.api("wan_accelerators")
        wan_accelerators_list = []
        for accelerator in await fetch_all(wan_api.get_all_wan_accelerators, "WAN accelerators"):
            try:
                wan_id = get_uuid_value(getattr(accelerator, "id", None))
                if not wan_id:
                    _LOGGER.warning(
                        "Skipping WAN accelerator %s: no usable ID",
                        getattr(accelerator, "name", "Unknown"),
                    )
                    continue

                server = getattr(accelerator, "server", None)
                cache = getattr(accelerator, "cache", None)
                has_server = not _is_unset(server)
                has_cache = not _is_unset(cache)

                wan_accelerators_list.append(
                    {
                        "id": wan_id,
                        "name": getattr(accelerator, "name", None) or "Unknown",
                        "host_id": (
                            get_uuid_value(getattr(server, "host_id", None)) if has_server else None
                        ),
                        "description": (
                            _license_text(server, "description", default="") if has_server else ""
                        ),
                        "traffic_port": (
                            _number_or_none(server, "traffic_port") if has_server else None
                        ),
                        "streams_count": (
                            _number_or_none(server, "streams_count") if has_server else None
                        ),
                        "high_bandwidth_mode": (
                            _bool_or_none(server, "high_bandwidth_mode_enabled")
                            if has_server
                            else None
                        ),
                        "cache_folder": (
                            _license_text(cache, "cache_folder", default=None)
                            if has_cache
                            else None
                        ),
                        "cache_size": _number_or_none(cache, "cache_size") if has_cache else None,
                        "cache_size_unit": (
                            get_enum_value(getattr(cache, "cache_size_unit", None), "Unknown")
                            if has_cache
                            else None
                        ),
                    }
                )
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning(
                    "Failed to parse WAN accelerator %s: %s",
                    getattr(accelerator, "name", "Unknown"),
                    describe_error(err),
                )
        return wan_accelerators_list

    async def fetch_ha_cluster() -> dict | None:
        # Only called on 1.3-rev2 and newer, and only answered by a server that is actually
        # clustered — an unclustered server is the common case, not an error.
        ha_api = veeam_client.api("high_availability_ha_cluster")
        cluster = await veeam_client.call(ha_api.get_high_availability_cluster)

        if cluster is None:
            _LOGGER.debug("HA cluster endpoint returned no data")
            return None
        if not hasattr(cluster, "cluster_endpoint"):
            # An Error model. "Not configured" is the normal answer from an unclustered
            # server; anything else is a failure worth reporting.
            unclustered, detail = _cluster_absent_reason(cluster)
            if unclustered:
                _LOGGER.debug("No HA cluster on this server: %s", detail)
                return None
            _LOGGER.debug("Could not read the HA cluster: %s", detail)
            raise UnexpectedResponseError(f"HA cluster endpoint answered {detail}")

        ha_cluster = _parse_ha_cluster(cluster, get_enum_value, get_uuid_value, serialize_value)
        _LOGGER.debug(
            "Parsed HA cluster %s (online=%s)", ha_cluster.get("name"), ha_cluster.get("is_online")
        )
        return ha_cluster

    def parse_malware(items: list, parser, what: str) -> list[dict]:
        parsed = []
        for item in items:
            try:
                result = parser(item, get_enum_value, get_uuid_value, get_datetime_value)
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning("Failed to parse a %s: %s", what, describe_error(err))
                continue
            if result is not None:
                parsed.append(result)
        return parsed

    async def fetch_malware_events() -> dict:
        """The last day's malware events, the latest one, and the ones new since last poll."""
        malware_api = veeam_client.api("malware_detection")
        order = models.ESuspiciousActivityEventsFiltersOrderColumn
        since = dt_util.utcnow() - timedelta(hours=RECENT_HOURS)
        if api_version in DETECTION_TIME_ONLY:
            window = {
                "detected_after_time_utc_filter": since,
                "order_column": order.DETECTIONTIMEUTC,
            }
        else:
            window = {"created_after_time_utc_filter": since, "order_column": order.CREATIONTIMEUTC}
        items = await fetch_all(
            malware_api.view_suspicious_activity_events, "malware events", order_asc=False, **window
        )
        recent = parse_malware(items, parse_event, "malware event")

        if (
            not recent
            and malware_tracker.last_event is None
            and not malware_tracker.looked_up_older
        ):
            # A quiet day at startup: the latest event overall, so Last Malware Event is not
            # unknown. One page, newest first, of which only the first item is needed; paging
            # the whole history to read it would cost more requests, not fewer.
            result = await veeam_client.call(
                malware_api.view_suspicious_activity_events,
                skip=0,
                limit=PAGE_LIMIT,
                order_column=window["order_column"],
                order_asc=False,
            )
            older = getattr(result, "data", None)
            if not isinstance(older, list):
                raise UnexpectedResponseError(
                    f"malware events endpoint answered {_describe_response(result)}"
                )
            # Newest first: the first one that parses is the latest
            malware_tracker.last_event = next(
                (
                    parsed
                    for item in older
                    for parsed in parse_malware([item], parse_event, "malware event")
                ),
                None,
            )
            malware_tracker.looked_up_older = True

        for event in malware_tracker.update(recent):
            hass.bus.async_fire(
                EVENT_MALWARE,
                {
                    "entry_id": entry.entry_id,
                    **{
                        key: value.isoformat() if hasattr(value, "isoformat") else value
                        for key, value in event.items()
                    },
                },
            )

        return {
            "last_event": malware_tracker.last_event,
            "recent_count": len(recent),
            "recent_by_severity": count_by_severity(recent),
        }

    async def fetch_move_copy_sessions(jobs: list[dict]) -> dict | None:
        """Move/copy sessions awaiting a decision (1.3-rev2); None if the account may not ask.

        A plain array, not a paged collection, so there is no limit to set.
        """
        nonlocal move_copy_permitted
        backups_api = veeam_client.api("backups")
        result = await veeam_client.call(backups_api.get_move_copy_sessions)
        if not isinstance(result, list):
            if _refused_by_role(result):
                move_copy_permitted = False
                _LOGGER.info(
                    "Not monitoring move/copy sessions awaiting action on %s: the server "
                    "allows that to the Backup Administrator role only. Reload the "
                    "integration after changing the account's role",
                    host,
                )
                return None
            raise UnexpectedResponseError(
                f"move/copy sessions endpoint answered {_describe_response(result)}"
            )

        job_names = {job["id"]: job.get("name") for job in jobs if job.get("id")}
        sessions = []
        for session in result:
            try:
                parsed = parse_session(
                    session, job_names, get_enum_value, get_uuid_value, get_datetime_value
                )
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning("Failed to parse a move/copy session: %s", describe_error(err))
                continue
            if parsed is not None:
                sessions.append(parsed)

        for session in move_copy_tracker.update(sessions):
            hass.bus.async_fire(
                EVENT_MOVE_COPY, {"entry_id": entry.entry_id, **with_iso_times(session)}
            )
        return summarize_sessions(sessions)

    async def fetch_recovery_appliances() -> dict:
        """Agent recovery appliances known to the server (1.3-rev2), connected or not."""
        agents_api = veeam_client.api("agents")
        appliances = []
        for item in await fetch_all(
            agents_api.get_agents_recovery_appliances, "recovery appliances"
        ):
            try:
                parsed = parse_appliance(item, get_enum_value, get_uuid_value, get_datetime_value)
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning("Failed to parse a recovery appliance: %s", describe_error(err))
                continue
            if parsed is not None:
                appliances.append(parsed)

        connected = [appliance for appliance in appliances if appliance["connected"]]
        for appliance in recovery_appliance_tracker.update(connected):
            hass.bus.async_fire(
                EVENT_RECOVERY_APPLIANCE,
                {"entry_id": entry.entry_id, **with_iso_times(appliance)},
            )
        return summarize_appliances(appliances)

    async def fetch_security_analyzer() -> dict | None:
        """Best practice checks and the analyzer's last run; None if the account may not ask."""
        nonlocal security_analyzer_permitted
        security_api = veeam_client.api("security")
        result = await veeam_client.call(security_api.get_best_practices_compliance_result)
        items = getattr(result, "items", None)
        if not isinstance(items, list):
            if _refused_by_role(result):
                security_analyzer_permitted = False
                _LOGGER.info(
                    "Not monitoring the Security & Compliance Analyzer on %s: the server "
                    "allows that to the Backup Administrator and Security Administrator "
                    "roles only. Reload the integration after changing the account's role",
                    host,
                )
                return None
            raise UnexpectedResponseError(
                f"best practices endpoint answered {_describe_response(result)}"
            )

        practices = []
        for item in items:
            try:
                parsed = parse_best_practice(item, get_enum_value, get_uuid_value)
            except (ValueError, KeyError, AttributeError, TypeError) as err:
                _LOGGER.warning("Failed to parse a best practice: %s", describe_error(err))
                continue
            if parsed is not None:
                practices.append(parsed)

        session = await veeam_client.call(security_api.get_security_analyzer_session)
        if _not_found(session):
            # The analyzer has never run
            last_run = None
        elif session is None or hasattr(session, "error_code"):
            raise UnexpectedResponseError(
                f"analyzer last run endpoint answered {_describe_response(session)}"
            )
        else:
            last_run = parse_last_run(session, get_enum_value, get_datetime_value)

        for practice in violation_tracker.update(practices):
            hass.bus.async_fire(
                EVENT_BEST_PRACTICE_VIOLATION,
                {
                    "entry_id": entry.entry_id,
                    "id": practice["id"],
                    "name": practice["name"],
                    "note": practice["note"],
                },
            )
        return summarize_security_analyzer(practices, last_run)

    async def fetch_malware_objects() -> dict:
        """The objects currently marked Infected or Suspicious (1.3-rev2)."""
        malware_api = veeam_client.api("malware_detection")
        severity = models.ESuspiciousActivitySeverity
        items = await fetch_all(
            malware_api.get_malware_detection_objects,
            "malware detection objects",
            severity_filter=[severity(value) for value in SEVERITIES_OF_CONCERN],
        )
        return summarize_objects(parse_malware(items, parse_object, "malware detection object"))

    async def poll() -> dict:
        """One pass over every endpoint.

        Each endpoint fails on its own: its previous data is kept, it is marked in
        fetch_ok, and its entities go unavailable — nothing is deleted over one failed
        fetch. Only when every endpoint fails does the whole poll fail. An authentication
        or session error is never absorbed here; it concerns every endpoint alike.
        """
        previous = coordinator.data or {}
        fetch_ok: dict[str, bool] = {}
        errors: list[tuple[str, BaseException]] = []
        requested = 0

        async def run(key: str, fetcher, default):
            nonlocal requested
            requested += 1
            try:
                value = await fetcher()
            except (VeeamAuthenticationError, VeeamSessionError):
                raise
            except Exception as err:  # noqa: BLE001 - one endpoint must not sink the rest
                fetch_ok[key] = False
                errors.append((key, err))
                return previous.get(key, default)
            fetch_ok[key] = True
            return value

        jobs = await run("jobs", fetch_jobs, [])
        server_info = await run("server_info", fetch_server_info, None)
        license_info = await run("license_info", fetch_license_info, None)
        repositories_result = await run("repositories", fetch_repositories, None)
        sobrs = await run("sobrs", fetch_sobrs, [])
        proxies = await run("proxies", fetch_proxies, [])
        wan_accelerators = await run("wan_accelerators", fetch_wan_accelerators, [])
        if ha_cluster_supported:
            ha_cluster = await run("ha_cluster", fetch_ha_cluster, None)
        else:
            # Not in this API revision: nothing was asked, so nothing failed
            ha_cluster, fetch_ok["ha_cluster"] = None, True

        # Not in this API revision: nothing was asked, so nothing failed
        if malware_events_supported:
            malware_events = await run("malware_events", fetch_malware_events, None)
        else:
            malware_events, fetch_ok["malware_events"] = None, True
        if malware_objects_supported:
            malware_objects = await run("malware_objects", fetch_malware_objects, None)
        else:
            malware_objects, fetch_ok["malware_objects"] = None, True
        if move_copy_supported and move_copy_permitted:
            move_copy_sessions = await run(
                "move_copy_sessions", lambda: fetch_move_copy_sessions(jobs), None
            )
        else:
            move_copy_sessions, fetch_ok["move_copy_sessions"] = None, True
        if recovery_appliances_supported:
            recovery_appliances = await run("recovery_appliances", fetch_recovery_appliances, None)
        else:
            recovery_appliances, fetch_ok["recovery_appliances"] = None, True
        if security_analyzer_supported and security_analyzer_permitted:
            security_analyzer = await run("security_analyzer", fetch_security_analyzer, None)
        else:
            security_analyzer, fetch_ok["security_analyzer"] = None, True

        if isinstance(repositories_result, tuple):
            repositories, fetch_ok["repository_states"] = repositories_result
        else:
            # The repositories fetch itself failed; `previous` held the parsed list
            repositories = repositories_result or []
            fetch_ok["repository_states"] = False

        if len(errors) == requested:
            # Nothing answered: the server is unreachable, not merely misbehaving. One
            # failed update says so; eight endpoint warnings would only repeat it.
            raise UpdateFailed(f"Error communicating with API: {describe_error(errors[0][1])}")

        for key, err in errors:
            note_failure(key, err)
        for key, ok in fetch_ok.items():
            if ok and key != "repository_states":
                note_success(key)

        return {
            "jobs": jobs,
            "server_info": server_info,
            "license_info": license_info,
            "repositories": repositories,
            "sobrs": sobrs,
            "proxies": proxies,
            "wan_accelerators": wan_accelerators,
            "ha_cluster": ha_cluster,
            "malware_events": malware_events,
            "malware_objects": malware_objects,
            "move_copy_sessions": move_copy_sessions,
            "recovery_appliances": recovery_appliances,
            "security_analyzer": security_analyzer,
            "fetch_ok": fetch_ok,
            "diagnostics": {
                "connected": True,
                # Healthy only when every endpoint answered
                "health_ok": all(fetch_ok.values()),
                "failed_endpoints": sorted(k for k, ok in fetch_ok.items() if not ok),
                "last_successful_poll": dt_util.now(),
            },
        }

    async def async_update_data():
        """Fetch data from API, within a time limit."""
        try:
            try:
                async with asyncio.timeout(UPDATE_TIMEOUT):
                    return await poll()
            except VeeamSessionError as err:
                # The SDK has already dropped the session, so the retry logs in afresh. A
                # second rejection in a row is reported as an ordinary failed update.
                _LOGGER.debug("Session rejected mid-poll (%s); retrying once", err)
                async with asyncio.timeout(UPDATE_TIMEOUT):
                    return await poll()
        except VeeamAuthenticationError as err:
            # Reaching here means the password login itself was refused: ask for new ones
            raise ConfigEntryAuthFailed(f"Veeam rejected the credentials: {err}") from err
        except UpdateFailed:
            raise
        except TimeoutError as err:
            raise UpdateFailed(f"Poll did not complete within {UPDATE_TIMEOUT} seconds") from err
        except Exception as err:
            # When an update fails, the coordinator retains the last successful data,
            # so diagnostic sensors will continue to show the last successful poll time
            raise UpdateFailed(f"Error communicating with API: {describe_error(err)}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=UPDATE_INTERVAL),
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await veeam_client.close()
        raise

    # Runs on every setup, so a reload re-reports it and a newly licensed server clears it
    _check_license_support(hass, entry, coordinator.data)
    _warn_about_duplicate_entries(hass, entry)

    entry.runtime_data = {
        "coordinator": coordinator,
        "veeam_client": veeam_client,
        # Platforms and entities read the resolved version from here rather than re-reading
        # the entry, which may only hold "auto"
        "api_version": api_version,
    }
    entry.async_on_unload(veeam_client.close)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # One sweep for every platform, after they have added this cycle's entities. It skips
    # any kind whose fetch failed or came back empty, so an outage never deletes anything.
    @callback
    def _prune() -> None:
        async_prune_stale(hass, entry, coordinator.data)

    _prune()
    entry.async_on_unload(coordinator.async_add_listener(_prune))

    return True


def _warn_about_duplicate_entries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Say so when another entry points at the same server.

    Entry unique IDs are host:port, and 13.1 answers on both 443 and 9419, so one server can
    be added twice. Both entries then create a full set of entities on the same devices, and
    Home Assistant suffixes the second set with _2.
    """
    host = str(entry.data.get(CONF_HOST, "")).lower()
    others = [
        other.title
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id and str(other.data.get(CONF_HOST, "")).lower() == host
    ]
    if others:
        _LOGGER.warning(
            "%s is also configured as %s. Each entry creates its own entities for the same "
            "server, so they appear twice (the second with a _2 suffix); remove one entry",
            entry.data.get(CONF_HOST),
            ", ".join(others),
        )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry. The client is closed by the unload callback set up above."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up after a removed config entry.

    Deleted here rather than on unload, which also runs on every reload — the warning would
    otherwise disappear and come back on each restart.
    """
    ir.async_delete_issue(hass, DOMAIN, _license_issue_id(entry))
