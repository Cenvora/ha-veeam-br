"""Shared device naming and availability for the Veeam entities.

Every platform builds the same devices, so their names and identifiers are defined once here.
Identifiers are unchanged from earlier releases — changing them would orphan every existing
device.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST

from .const import DOMAIN

MANUFACTURER = "Veeam"


def device_name(kind: str, name: str | None) -> str:
    """Name a device after the object it represents."""
    return name or kind


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
        server_label(entry, data),
        "Backup & Replication Server",
    )


def license_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> dict[str, Any]:
    # Qualified by host: a hardcoded name is indistinguishable once a second server is
    # added (#82)
    host = entry.data.get(CONF_HOST, "Unknown")
    return _device(f"license_{entry.entry_id}", f"Veeam License ({host})", "License")


def ha_cluster_device_info(entry: ConfigEntry, data: dict[str, Any] | None) -> dict[str, Any]:
    cluster = (data or {}).get("ha_cluster") or {}
    name = cluster.get("name") or "HA Cluster"
    host = entry.data.get(CONF_HOST, "Unknown")
    return _device(f"ha_cluster_{entry.entry_id}", f"{name} ({host})", "High Availability Cluster")


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
