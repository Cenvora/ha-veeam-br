"""Support for Veeam Backup & Replication buttons."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_VERSIONS,
    DEFAULT_API_MODULE,
    DOMAIN,
    FEATURE_HA_CLUSTER,
    FEATURE_HA_FAILOVER,
    FEATURE_HA_SWITCHOVER,
    FEATURE_JOB_RETRY,
    FEATURE_JOB_START,
    FEATURE_JOB_STOP,
    FEATURE_JOBS,
    FEATURE_PROXY_DISABLE,
    FEATURE_PROXY_ENABLE,
    FEATURE_REPOSITORY_RESCAN,
    FEATURE_SOBR_EXTENT_MODES,
    check_api_feature_availability,
    configured_api_version,
)
from .display import describe_error
from .entity import (
    ha_cluster_device_info,
    job_device_info,
    proxy_device_info,
    repository_device_info,
    sobr_device_info,
)
from .pruning import forget_missing, reported_extent_ids, reported_ids

_LOGGER = logging.getLogger(__name__)

# Limit parallel updates to avoid overwhelming the Veeam API
PARALLEL_UPDATES = 1

# Operation modules each button calls, with the feature that gates it. veeam-br imports them
# on first use, which would block the event loop, so they are imported up front in an
# executor — every one a button can call, or its first press trips the blocking-call check.
BUTTON_ENDPOINTS = (
    ("jobs.start_job", FEATURE_JOBS),
    ("jobs.stop_job", FEATURE_JOBS),
    ("jobs.retry_job", FEATURE_JOBS),
    ("jobs.enable_job", FEATURE_JOBS),
    ("jobs.disable_job", FEATURE_JOBS),
    ("repositories.rescan_repositories", FEATURE_REPOSITORY_RESCAN),
    ("repositories.enable_scale_out_extent_sealed_mode", FEATURE_SOBR_EXTENT_MODES),
    ("repositories.disable_scale_out_extent_sealed_mode", FEATURE_SOBR_EXTENT_MODES),
    ("repositories.enable_scale_out_extent_maintenance_mode", FEATURE_SOBR_EXTENT_MODES),
    ("repositories.disable_scale_out_extent_maintenance_mode", FEATURE_SOBR_EXTENT_MODES),
    ("proxies.enable_proxy", FEATURE_PROXY_ENABLE),
    ("proxies.disable_proxy", FEATURE_PROXY_DISABLE),
    ("high_availability_ha_cluster.switchover_high_availability_cluster", FEATURE_HA_SWITCHOVER),
    ("high_availability_ha_cluster.failover_high_availability_cluster", FEATURE_HA_FAILOVER),
)

# Request bodies the buttons build, imported alongside
BUTTON_SPECS = (
    FEATURE_JOB_START,
    FEATURE_JOB_STOP,
    FEATURE_JOB_RETRY,
    FEATURE_REPOSITORY_RESCAN,
    FEATURE_SOBR_EXTENT_MODES,
    "models.high_availability_switchover_spec",
)


def _pre_import(api_version: str, api_module: str) -> None:
    """Import every operation and spec a button can use. Blocking: run in an executor."""
    for endpoint, feature in BUTTON_ENDPOINTS:
        if not check_api_feature_availability(api_version, feature):
            continue
        try:
            importlib.import_module(f"veeam_br.{api_module}.api.{endpoint}")
        except ImportError as err:
            _LOGGER.debug("Could not pre-import %s: %s", endpoint, err)

    for spec in BUTTON_SPECS:
        if not check_api_feature_availability(api_version, spec):
            continue
        try:
            importlib.import_module(f"veeam_br.{api_module}.{spec}")
        except ImportError as err:
            _LOGGER.debug("Could not pre-import %s: %s", spec, err)


def _rejection(result: Any) -> str | None:
    """Why the server refused an operation, or None if it did not.

    The generated operations return an Error model for a documented failure rather than
    raising, so an unchecked result reported every refusal — a job already running, a
    missing permission — as success.
    """
    if not hasattr(result, "error_code"):
        return None
    message = getattr(result, "message", None)
    code = getattr(result, "error_code", None)
    code = getattr(code, "value", code)
    extras = getattr(result, "additional_properties", None) or {}
    status = extras.get("status") if isinstance(extras, dict) else None
    parts = [str(part) for part in (f"HTTP {status}" if status else None, code, message) if part]
    return ": ".join(parts) or type(result).__name__


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Veeam Backup & Replication buttons."""
    coordinator = entry.runtime_data["coordinator"]
    veeam_client = entry.runtime_data["veeam_client"]

    api_version = configured_api_version(entry)
    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)
    await hass.async_add_executor_job(_pre_import, api_version, api_module)

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
            if check_api_feature_availability(api_version, FEATURE_JOB_START):
                job_buttons.append(VeeamJobStartButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, FEATURE_JOB_STOP):
                job_buttons.append(VeeamJobStopButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, FEATURE_JOB_RETRY):
                job_buttons.append(VeeamJobRetryButton(coordinator, entry, job, veeam_client))

            if check_api_feature_availability(api_version, FEATURE_JOBS):
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
        if check_api_feature_availability(api_version, FEATURE_REPOSITORY_RESCAN):
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
        if check_api_feature_availability(api_version, FEATURE_SOBR_EXTENT_MODES):
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
        # enable_proxy/disable_proxy arrived in 1.3-rev0; api.proxies alone is not enough,
        # since older versions expose the namespace without those two operations (#104)
        if check_api_feature_availability(api_version, FEATURE_PROXY_ENABLE):
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
            and check_api_feature_availability(api_version, FEATURE_HA_CLUSTER)
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

        # Removal is the shared sweep's job (pruning.py); forgetting what it removed lets
        # an object that comes back get its buttons again
        forget_missing(added_job_ids, reported_ids(coordinator.data, "jobs"))
        forget_missing(added_repository_ids, reported_ids(coordinator.data, "repositories"))
        forget_missing(added_sobr_extent_ids, reported_extent_ids(coordinator.data))
        forget_missing(added_proxy_ids, reported_ids(coordinator.data, "proxies"))

    # First attempt (after first refresh already ran)
    _sync_entities()

    # Future updates
    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))


class VeeamButtonBase(CoordinatorEntity, ButtonEntity):
    """Shared press handling: every button runs one operation and reports its outcome."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, config_entry, veeam_client):
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._veeam_client = veeam_client

    def _api_module(self) -> str:
        """Resolve the SDK package for the configured API version."""
        return API_VERSIONS.get(configured_api_version(self._config_entry), DEFAULT_API_MODULE)

    def _spec_class(self, module_name: str, class_name: str, action: str, target: str):
        """A request-body model, already imported during setup."""
        try:
            module = importlib.import_module(f"veeam_br.{self._api_module()}.models.{module_name}")
            return getattr(module, class_name)
        except (ImportError, AttributeError) as err:
            _LOGGER.error("Cannot %s %s: %s is unavailable (%s)", action, target, class_name, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={
                    "action": action,
                    "target": target,
                    "error": f"{class_name} is not available in this API version",
                },
            ) from err

    async def _async_run(
        self, namespace: str, operation: str, action: str, target: str, **kwargs: Any
    ) -> Any:
        """Call one operation, raise if it failed or was refused, then refresh.

        Raising HomeAssistantError is what makes the UI say the press failed; before, an
        error was logged while the press still looked successful.
        """
        try:
            api = self._veeam_client.api(namespace)
            result = await self._veeam_client.call(getattr(api, operation), **kwargs)
        except Exception as err:
            _LOGGER.error("Failed to %s %s: %s", action, target, describe_error(err))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={
                    "action": action,
                    "target": target,
                    "error": describe_error(err),
                },
            ) from err

        reason = _rejection(result)
        if reason is not None:
            _LOGGER.error("Veeam refused to %s %s: %s", action, target, reason)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_rejected",
                translation_placeholders={"action": action, "target": target, "error": reason},
            )

        _LOGGER.info("Requested: %s %s", action, target)
        await self.coordinator.async_request_refresh()
        return result


class VeeamRepositoryRescanButton(VeeamButtonBase):
    """Button to trigger repository rescan."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, repository_data, veeam_client):
        """Initialize the rescan button."""
        super().__init__(coordinator, config_entry, veeam_client)
        self._repo_id = repository_data.get("id")
        self._repo_name = repository_data.get("name", "Unknown Repository")
        self._attr_unique_id = f"{config_entry.entry_id}_repository_{self._repo_id}_rescan"
        self._attr_name = "Rescan"

    @property
    def device_info(self):
        """Return device info for this repository."""
        return repository_device_info(self._repo_id, self._repo_name)

    @property
    def icon(self) -> str:
        """Return the icon for the button."""
        return "mdi:magnify-scan"

    async def async_press(self) -> None:
        """Ask the server to rescan this repository, then refresh."""
        target = f"repository {self._repo_name}"
        spec = self._spec_class(
            "repositories_rescan_spec", "RepositoriesRescanSpec", "rescan", target
        )
        await self._async_run(
            "repositories",
            "rescan_repositories",
            "rescan",
            target,
            body=spec(repository_ids=[self._repo_id]),
        )


# ===========================
# SOBR EXTENT BUTTONS
# ===========================


class VeeamSOBRExtentButtonBase(VeeamButtonBase):
    """Base class for SOBR extent buttons."""

    _attr_entity_category = EntityCategory.CONFIG

    # Set by each subclass: the operation and how to describe it
    _operation: str = ""
    _action: str = ""

    def __init__(self, coordinator, config_entry, sobr_data, extent_data, veeam_client):
        """Initialize the SOBR extent button."""
        super().__init__(coordinator, config_entry, veeam_client)
        self._sobr_id = sobr_data.get("id")
        self._sobr_name = sobr_data.get("name", "Unknown SOBR")
        self._extent_id = extent_data.get("id")
        self._extent_name = extent_data.get("name", "Unknown Extent")

    @property
    def device_info(self):
        """Return device info for this SOBR."""
        return sobr_device_info(self._sobr_id, self._sobr_name)

    async def async_press(self) -> None:
        """Run this button's extent operation."""
        target = f"extent {self._extent_name} of SOBR {self._sobr_name}"
        spec = self._spec_class(
            "scale_out_extent_maintenance_spec",
            "ScaleOutExtentMaintenanceSpec",
            self._action,
            target,
        )
        await self._async_run(
            "repositories",
            self._operation,
            self._action,
            target,
            id=self._sobr_id,
            body=spec(repository_ids=[self._extent_id]),
        )


class VeeamSOBRExtentEnableSealedModeButton(VeeamSOBRExtentButtonBase):
    """Button to enable sealed mode for a SOBR extent."""

    _operation = "enable_scale_out_extent_sealed_mode"
    _action = "enable sealed mode on"

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


class VeeamSOBRExtentDisableSealedModeButton(VeeamSOBRExtentButtonBase):
    """Button to disable sealed mode for a SOBR extent."""

    _operation = "disable_scale_out_extent_sealed_mode"
    _action = "disable sealed mode on"

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


class VeeamSOBRExtentEnableMaintenanceModeButton(VeeamSOBRExtentButtonBase):
    """Button to enable maintenance mode for a SOBR extent."""

    _operation = "enable_scale_out_extent_maintenance_mode"
    _action = "enable maintenance mode on"

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


class VeeamSOBRExtentDisableMaintenanceModeButton(VeeamSOBRExtentButtonBase):
    """Button to disable maintenance mode for a SOBR extent."""

    _operation = "disable_scale_out_extent_maintenance_mode"
    _action = "disable maintenance mode on"

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


# ===========================
# JOB BUTTONS
# ===========================


class VeeamJobButtonBase(VeeamButtonBase):
    """Base class for Veeam job buttons."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, job_data, veeam_client):
        """Initialize the job button."""
        super().__init__(coordinator, config_entry, veeam_client)
        self._job_id = job_data.get("id")
        self._job_name = job_data.get("name", "Unknown Job")

    @property
    def device_info(self):
        """Return device info for this job."""
        return job_device_info(self._job_id, self._job_name)

    @property
    def _target(self) -> str:
        return f"job {self._job_name}"

    def _spec(self, spec_name: str, action: str):
        """A job spec model, e.g. job_start_spec -> JobStartSpec."""
        class_name = "".join(word.capitalize() for word in spec_name.split("_"))
        return self._spec_class(spec_name, class_name, action, self._target)


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
        body = self._spec("job_start_spec", "start")(perform_active_full=False)
        await self._async_run(
            "jobs", "start_job", "start", self._target, id=self._job_id, body=body
        )


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
        # JobStopSpec has no required parameters
        body = self._spec("job_stop_spec", "stop")()
        await self._async_run("jobs", "stop_job", "stop", self._target, id=self._job_id, body=body)


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
        # JobRetrySpec has no required parameters
        body = self._spec("job_retry_spec", "retry")()
        await self._async_run(
            "jobs", "retry_job", "retry", self._target, id=self._job_id, body=body
        )


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
        await self._async_run("jobs", "enable_job", "enable", self._target, id=self._job_id)


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
        await self._async_run("jobs", "disable_job", "disable", self._target, id=self._job_id)


# ===========================
# HIGH AVAILABILITY CLUSTER BUTTONS
#
# Switchover is the planned, graceful role swap; failover is the unplanned one for when the
# primary is already gone. Both are disruptive, and Home Assistant buttons fire with no
# confirmation step, so failover is disabled by default — a user automating it has to enable
# the entity deliberately. See the README for the distinction.
# ===========================


class VeeamHAClusterButtonBase(VeeamButtonBase):
    """Base class for HA cluster buttons."""

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
        return ha_cluster_device_info(self._config_entry, self.coordinator.data)


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
        # The spec is optional; sending it explicitly keeps the lag check in force, so a
        # switchover with a badly lagging secondary is refused by the server instead of
        # silently promoting a stale node.
        kwargs: dict[str, Any] = {}
        try:
            models_module = importlib.import_module(
                f"veeam_br.{self._api_module()}.models.high_availability_switchover_spec"
            )
            kwargs["body"] = models_module.HighAvailabilitySwitchoverSpec(ignore_lag=False)
        except (ImportError, AttributeError) as err:
            _LOGGER.debug(
                "HighAvailabilitySwitchoverSpec unavailable (%s); "
                "requesting switchover without a body",
                err,
            )

        await self._async_run(
            "high_availability_ha_cluster",
            "switchover_high_availability_cluster",
            "switch over",
            "the HA cluster",
            **kwargs,
        )


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
        _LOGGER.warning("Requesting HA cluster failover")
        await self._async_run(
            "high_availability_ha_cluster",
            "failover_high_availability_cluster",
            "fail over",
            "the HA cluster",
        )


# ===========================
# PROXY BUTTONS
#
# Disabling a proxy takes it out of service without deleting it, which is what an operator
# reaches for during maintenance on the proxy host.
# ===========================


class VeeamProxyButtonBase(VeeamButtonBase):
    """Base class for proxy buttons."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry, proxy_data, veeam_client):
        """Initialize the button."""
        super().__init__(coordinator, config_entry, veeam_client)
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
        return proxy_device_info(self._proxy_id, self._proxy_name)

    async def _call(self, operation_name: str, action: str) -> None:
        """Run one proxy operation and refresh."""
        await self._async_run(
            "proxies", operation_name, action, f"proxy {self._proxy_name}", id=self._proxy_id
        )


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
        await self._call("enable_proxy", "enable")


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
        await self._call("disable_proxy", "disable")
