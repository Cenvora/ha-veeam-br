"""Diagnostics support for Veeam Backup & Replication."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_API_VERSION, DEFAULT_API_VERSION, configured_api_version
from .licensing import unsupported_license_reason


def _veeam_br_version() -> str:
    """Version of the installed veeam-br library, or why it could not be read."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("veeam-br")
    except PackageNotFoundError:
        return "not installed"
    except Exception as err:  # noqa: BLE001 - diagnostics must never raise
        return f"unknown ({err})"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data["coordinator"]

    # Get the coordinator data
    data = coordinator.data if coordinator.data else {}

    # Build diagnostics data
    diagnostics_data = {
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "unique_id": entry.unique_id,
            # First questions in any bug report: which API revision, and which library.
            # Both are reported because "auto" is a valid stored value, and knowing what it
            # resolved to is the whole point.
            "api_version_configured": entry.options.get(
                CONF_API_VERSION, entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
            ),
            "api_version": configured_api_version(entry),
            "veeam_br_version": _veeam_br_version(),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_success_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
        },
        "data": {
            "jobs_count": len(data.get("jobs", [])),
            "repositories_count": len(data.get("repositories", [])),
            "sobrs_count": len(data.get("sobrs", [])),
            "has_server_info": data.get("server_info") is not None,
            "has_license_info": data.get("license_info") is not None,
        },
    }

    # Add server info (without sensitive data)
    if data.get("server_info"):
        server_info = data["server_info"]
        diagnostics_data["server"] = {
            "build_version": server_info.get("build_version"),
            "platform": server_info.get("platform"),
            "database_vendor": server_info.get("database_vendor"),
            "sql_server_edition": server_info.get("sql_server_edition"),
        }

    # Add license info (without sensitive data)
    if data.get("license_info"):
        license_info = data["license_info"]
        diagnostics_data["license"] = {
            "status": license_info.get("status"),
            "edition": license_info.get("edition"),
            "type": license_info.get("type"),
            # Names the licensing limitation instead of leaving it to be inferred
            "unsupported_reason": unsupported_license_reason(license_info),
        }

    # Add job summaries (without sensitive details)
    if data.get("jobs"):
        jobs_summary = {}
        for job in data["jobs"]:
            status = job.get("status", "unknown")
            jobs_summary[status] = jobs_summary.get(status, 0) + 1
        diagnostics_data["jobs_summary"] = jobs_summary

    # Add repository summaries (without sensitive details)
    if data.get("repositories"):
        repos_summary = {}
        for repo in data["repositories"]:
            repo_type = repo.get("type", "unknown")
            repos_summary[repo_type] = repos_summary.get(repo_type, 0) + 1
        diagnostics_data["repositories_summary"] = repos_summary

    # Add diagnostics info
    if data.get("diagnostics"):
        diagnostics_data["integration_diagnostics"] = {
            "connected": data["diagnostics"].get("connected"),
            "health_ok": data["diagnostics"].get("health_ok"),
            "last_successful_poll": (
                data["diagnostics"]["last_successful_poll"].isoformat()
                if data["diagnostics"].get("last_successful_poll")
                else None
            ),
        }

    return diagnostics_data
