"""Shared device naming and availability for the Veeam entities.

Every platform builds the same devices, so their names and identifiers are defined once here.
Identifiers are unchanged from earlier releases — changing them would orphan every existing
device — and only the display names follow the scheme below.

Device names are "VBR <kind> <name>", e.g. "VBR Job Nightly VMs" or "VBR Server vbr01",
with the kind left out when the name already contains it ("VBR Default Backup Repository").
Entities use has_entity_name, so a new entity's ID starts with the device name
(sensor.vbr_job_nightly_vms_last_result). Existing entities keep the IDs already in the
registry; only their friendly names change.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST

from .const import DEVICE_NAME_PREFIX, DOMAIN

MANUFACTURER = "Veeam"


def device_name(kind: str, name: str | None) -> str:
    """Name a device "VBR <kind> <name>", leaving out the kind when the name says it.

    "Nightly VMs" becomes "VBR Job Nightly VMs", but "Default Backup Repository" becomes
    "VBR Default Backup Repository" rather than "VBR Repository Default Backup Repository".
    The VB365 integration names its devices by the same rule.
    """
    if not name:
        return f"{DEVICE_NAME_PREFIX} {kind}"
    if re.search(rf"\b{re.escape(kind)}\b", name, re.IGNORECASE):
        return f"{DEVICE_NAME_PREFIX} {name}"
    return f"{DEVICE_NAME_PREFIX} {kind} {name}"


def _device(identifier: str, name: str, model: str) -> dict[str, Any]:
    return {
        "identifiers": {(DOMAIN, identifier)},
        "name": name,
        "manufacturer": MANUFACTURER,
        # The dashboard strategy groups devices by model, so these strings are stable
        "model": model,
    }


def server_label(entry: ConfigEntry, data: dict[str, Any] | None) -> str:
    """The server's own name, or the configured host while that is unknown.

    Falling back to the host rather than a bare "Unknown" keeps two entries' devices apart
    when server info fails to fetch (#82).
    """
    server_info = (data or {}).get("server_info") or {}
    name = server_info.get("name")
    if isinstance(name, str) and name and name != "Unknown":
        return name
    return str(entry.data.get(CONF_HOST, "Unknown"))


def job_device_info(job_id: str, job_name: str) -> dict[str, Any]:
    return _device(f"job_{job_id}", device_name("Job", job_name), "Backup Job")


def repository_device_info(repo_id: str, repo_name: str) -> dict[str, Any]:
    return _device(
        f"repository_{repo_id}",
        device_name("Repository", repo_name),
        "Backup Repository",
    )


def sobr_device_info(sobr_id: str, sobr_name: str) -> dict[str, Any]:
    return _device(f"sobr_{sobr_id}", device_name("SOBR", sobr_name), "Scale-Out Backup Repository")


def proxy_device_info(proxy_id: str, proxy_name: str) -> dict[str, Any]:
    return _device(f"proxy_{proxy_id}", device_name("Proxy", proxy_name), "Backup Proxy")


def wan_device_info(wan_id: str, wan_name: str) -> dict[str, Any]:
    return _device(f"wan_{wan_id}", device_name("WAN Accelerator", wan_name), "WAN Accelerator")


def server_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> dict[str, Any]:
    return _device(
        f"server_{entry.entry_id}",
        device_name("Server", server_label(entry, data)),
        "Backup & Replication Server",
    )


def license_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> dict[str, Any]:
    # Named after the server, so a second server's license device is told apart (#82)
    return _device(
        f"license_{entry.entry_id}",
        device_name("License", server_label(entry, data)),
        "License",
    )


def ha_cluster_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> dict[str, Any]:
    cluster = (data or {}).get("ha_cluster") or {}
    name = cluster.get("name")
    if not name or name == "HA Cluster":
        name = server_label(entry, data)
    return _device(
        f"ha_cluster_{entry.entry_id}",
        device_name("HA Cluster", name),
        "High Availability Cluster",
    )


def security_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> dict[str, Any]:
    """Malware detection, one per server."""
    return _device(
        f"security_{entry.entry_id}",
        device_name("Security", server_label(entry, data)),
        "Security",
    )


def endpoint_ok(data: dict[str, Any] | None, key: str) -> bool:
    """Whether this cycle's fetch of one endpoint succeeded.

    A failed endpoint keeps its previous data, so its entities would otherwise go on showing
    stale values as if they were current.
    """
    if not data:
        return False
    fetch_ok = data.get("fetch_ok")
    if not isinstance(fetch_ok, dict):
        return True
    return bool(fetch_ok.get(key, True))
