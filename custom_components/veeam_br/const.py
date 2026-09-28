"""Constants for the Veeam Backup & Replication integration."""

import functools
import importlib.util

from veeam_br.discovery import newest_first
from veeam_br.versions import VERSION_TO_PACKAGE

DOMAIN = "veeam_br"
DEFAULT_NAME = "Veeam Backup & Replication"

# Prefix for every device this integration creates. Entities use has_entity_name, so their
# entity IDs start with the device name; the prefix keeps them together and tells them apart
# from the Veeam Backup for Microsoft 365 integration, which uses "VB365 ".
DEVICE_NAME_PREFIX = "VBR"

# Configuration keys
CONF_VERIFY_SSL = "verify_ssl"
CONF_API_VERSION = "api_version"

# Defaults
# Veeam B&R 13.1 serves the REST API on 443 and no longer needs a dedicated port. 9419
# still answers on 13.1 for backward compatibility, but Veeam has said it will be removed in
# a future release, so new setups should start on 443. Existing entries keep whatever port
# they were configured with — this default only pre-fills the form.
DEFAULT_PORT = 443

# Pre-13.1 REST API port, still accepted by 13.1 and deprecated
LEGACY_PORT = 9419
DEFAULT_VERIFY_SSL = True

# Selector sentinel: probe the server for the newest API version it serves (see
# veeam_br.discovery). It is a user intent rather than a version and is stored on the entry
# as-is: every setup resolves it again, so a server upgrade or a veeam-br release that adds a
# newer revision is picked up on the next restart. The resolved value lives in
# entry.runtime_data (see configured_api_version).
AUTO_API_VERSION = "auto"


def _api_versions() -> dict[str, str]:
    """Map each API version veeam-br ships to its package directory, oldest first.

    The SDK's own table is the authority: a version it does not list cannot be spoken, and
    one it adds is offered without a change here.
    """
    return {
        version: VERSION_TO_PACKAGE[version].rsplit(".", 1)[-1]
        for version in reversed(newest_first(VERSION_TO_PACKAGE))
    }


# API version options, e.g. {"1.2-rev1": "v1_2_rev1", ..., "1.3-rev2": "v1_3_rev2"}
API_VERSIONS = _api_versions()

# Newest API revision veeam-br ships (1.3-rev2 is served by Veeam B&R 13.1). Used when auto
# detection cannot tell, and for entries that predate the option. Users on older servers can
# pick an older revision; the config flow validates the connection, so a revision the server
# does not serve fails at setup rather than silently.
DEFAULT_API_VERSION = next(reversed(API_VERSIONS))

# Package directory backing DEFAULT_API_VERSION
DEFAULT_API_MODULE = API_VERSIONS[DEFAULT_API_VERSION]

# Update interval
UPDATE_INTERVAL = 60  # seconds

# Seconds for one HTTP request. veeam-br applies it per request, so a hung server fails that
# call instead of stalling the integration.
REQUEST_TIMEOUT = 30.0

# Seconds for one whole poll. A poll makes a dozen requests and pages through large
# collections, so this is generous; it exists so that a poll can never run forever.
UPDATE_TIMEOUT = 240

# Seconds for logging in, during setup and in the config flow
CONNECT_TIMEOUT = 60

# Page size for every collection request, integration-wide: large, so that listing anything
# takes as few requests as possible. The 1.3 revisions default to 200 and silently drop
# anything beyond it. No maximum is documented, so collections are still paged until the
# reported total is reached, in case a server answers with fewer than asked for.
PAGE_LIMIT = 10000

# Features gated on the API revision, named by the SDK module that provides them. Named once
# here so the fetch, the entity gating and the pre-imports cannot disagree.
FEATURE_JOBS = "api.jobs"
FEATURE_REPOSITORIES = "api.repositories"
FEATURE_SERVICE = "api.service"
FEATURE_LICENSE = "api.license_"
FEATURE_PROXIES = "api.proxies"
FEATURE_WAN_ACCELERATORS = "api.wan_accelerators"
# The proxy states endpoint arrived in API 1.3-rev0 (VBR 13). On 1.2-rev1 only the
# configuration endpoint exists, so online/disabled/out-of-date are unknown there (#104).
FEATURE_PROXY_STATES = "api.proxies.get_all_proxies_states"
# enable_proxy/disable_proxy arrived in 1.3-rev0 as well
FEATURE_PROXY_ENABLE = "api.proxies.enable_proxy"
FEATURE_PROXY_DISABLE = "api.proxies.disable_proxy"
# High Availability cluster endpoints exist only from API 1.3-rev2 (VBR 13.1)
FEATURE_HA_CLUSTER = "api.high_availability_ha_cluster"
FEATURE_HA_SWITCHOVER = "api.high_availability_ha_cluster.switchover_high_availability_cluster"
FEATURE_HA_FAILOVER = "api.high_availability_ha_cluster.failover_high_availability_cluster"
FEATURE_HA_SWITCHOVER_SPEC = "models.high_availability_switchover_spec"
# Malware events exist in every revision; detected objects arrive in 1.3-rev2 (VBR 13.1)
FEATURE_MALWARE_EVENTS = "api.malware_detection.view_suspicious_activity_events"
FEATURE_MALWARE_OBJECTS = "api.malware_detection.get_malware_detection_objects"
# Move/copy backup sessions awaiting action, and managing them: 1.3-rev2 (VBR 13.1)
FEATURE_MOVE_COPY_SESSIONS = "api.backups.get_move_copy_sessions"
FEATURE_MANAGE_MOVE_COPY = "api.backups.manage_move_copy_session"
# Agent recovery appliances: 1.3-rev2 (VBR 13.1)
FEATURE_RECOVERY_APPLIANCES = "api.agents.get_agents_recovery_appliances"
# Security & Compliance Analyzer: every revision
FEATURE_SECURITY_ANALYZER = "api.security.get_best_practices_compliance_result"
FEATURE_SECURITY_ANALYZER_LAST_RUN = "api.security.get_security_analyzer_session"
FEATURE_SECURITY_ANALYZER_START = "api.security.start_security_analyzer"
FEATURE_JOB_START = "models.job_start_spec"
FEATURE_JOB_STOP = "models.job_stop_spec"
FEATURE_JOB_RETRY = "models.job_retry_spec"
FEATURE_REPOSITORY_RESCAN = "models.repositories_rescan_spec"
FEATURE_SOBR_EXTENT_MODES = "models.scale_out_extent_maintenance_spec"

ALL_FEATURES = (
    FEATURE_JOBS,
    FEATURE_REPOSITORIES,
    FEATURE_SERVICE,
    FEATURE_LICENSE,
    FEATURE_PROXIES,
    FEATURE_WAN_ACCELERATORS,
    FEATURE_PROXY_STATES,
    FEATURE_PROXY_ENABLE,
    FEATURE_PROXY_DISABLE,
    FEATURE_HA_CLUSTER,
    FEATURE_HA_SWITCHOVER,
    FEATURE_HA_FAILOVER,
    FEATURE_HA_SWITCHOVER_SPEC,
    FEATURE_MALWARE_EVENTS,
    FEATURE_MALWARE_OBJECTS,
    FEATURE_MOVE_COPY_SESSIONS,
    FEATURE_MANAGE_MOVE_COPY,
    FEATURE_RECOVERY_APPLIANCES,
    FEATURE_SECURITY_ANALYZER,
    FEATURE_SECURITY_ANALYZER_LAST_RUN,
    FEATURE_SECURITY_ANALYZER_START,
    FEATURE_JOB_START,
    FEATURE_JOB_STOP,
    FEATURE_JOB_RETRY,
    FEATURE_REPOSITORY_RESCAN,
    FEATURE_SOBR_EXTENT_MODES,
)


@functools.cache
def check_api_feature_availability(api_version: str, feature_path: str) -> bool:
    """Check if a specific API feature (endpoint/spec model) is available in the given API version.

    Cached per (version, feature): the answer cannot change while Home Assistant runs, and
    find_spec touches the filesystem, which the entity platforms would otherwise repeat on the
    event loop on every poll. Setup warms the cache from an executor (warm_feature_cache), so
    the first lookup does not land on the loop either.

    Args:
        api_version: The API version to check (e.g., "1.3-rev1")
        feature_path: The import path to check (e.g., "models.job_start_spec" or "api.jobs")

    Returns:
        bool: True if the feature is available in the API version, False otherwise
    """
    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)

    try:
        # Try to import the module/feature
        import_path = f"veeam_br.{api_module}.{feature_path}"
        spec = importlib.util.find_spec(import_path)
        return spec is not None
    except (ImportError, ModuleNotFoundError, ValueError, AttributeError):
        return False


def warm_feature_cache(api_version: str) -> None:
    """Answer every feature question for this version up front. Blocking: run in an executor."""
    for feature in ALL_FEATURES:
        check_api_feature_availability(api_version, feature)


# API feature requirements mapping
# This mapping documents which API features (models/endpoints) are required for each entity type.
# It serves as reference documentation for developers - the FEATURE_* constants above are what
# the code passes to check_api_feature_availability().
API_FEATURE_REQUIREMENTS = {
    # Button features
    "job_start_button": FEATURE_JOB_START,
    "job_stop_button": FEATURE_JOB_STOP,
    "job_retry_button": FEATURE_JOB_RETRY,
    "job_enable_button": FEATURE_JOBS,  # Uses enable_job endpoint
    "job_disable_button": FEATURE_JOBS,  # Uses disable_job endpoint
    "repository_rescan_button": FEATURE_REPOSITORY_RESCAN,
    "sobr_extent_sealed_mode_button": FEATURE_SOBR_EXTENT_MODES,
    "sobr_extent_maintenance_mode_button": FEATURE_SOBR_EXTENT_MODES,
    # Data sources (for sensors)
    "jobs_data": FEATURE_JOBS,
    "repositories_data": FEATURE_REPOSITORIES,
    "sobr_data": FEATURE_REPOSITORIES,  # SOBRs use repositories API
    "license_data": FEATURE_LICENSE,
    "server_data": FEATURE_SERVICE,
    "proxy_data": FEATURE_PROXIES,
    # Proxy state (online/disabled/out-of-date) and the enable/disable operations:
    # API 1.3-rev0 (VBR 13) and newer only
    "proxy_state_data": FEATURE_PROXY_STATES,
    "proxy_enable_button": FEATURE_PROXY_ENABLE,
    "proxy_disable_button": FEATURE_PROXY_DISABLE,
    "wan_accelerator_data": FEATURE_WAN_ACCELERATORS,
    # High Availability cluster: API 1.3-rev2 (VBR 13.1) and newer only
    "ha_cluster_data": FEATURE_HA_CLUSTER,
    "ha_cluster_switchover_button": FEATURE_HA_SWITCHOVER,
    "ha_cluster_failover_button": FEATURE_HA_FAILOVER,
    # Malware detection: events in every revision, detected objects from 1.3-rev2
    "malware_events_data": FEATURE_MALWARE_EVENTS,
    "malware_objects_data": FEATURE_MALWARE_OBJECTS,
}


def configured_api_version(entry) -> str:
    """The API version to talk to this server with.

    CONF_API_VERSION may hold AUTO_API_VERSION, which is a user intent rather than a version:
    it means "use the newest revision this server serves", resolved during setup so a server
    upgrade or a newer veeam-br is picked up on the next restart. The resolved value is kept in
    entry.runtime_data, so platforms and entities read it from there rather than re-detecting.

    Falls back to DEFAULT_API_VERSION when asked before setup has resolved anything, which is
    the same answer detection would give if probing found nothing.
    """
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, dict):
        resolved = runtime.get("api_version")
        if resolved and resolved != AUTO_API_VERSION:
            return resolved

    stored = entry.options.get(
        CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    )
    return DEFAULT_API_VERSION if stored == AUTO_API_VERSION else stored
