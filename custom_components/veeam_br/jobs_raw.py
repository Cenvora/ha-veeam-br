"""Tolerant parsing of the raw job-states payload.

The veeam-br generated models reject null timestamps. Veeam Backup & Replication sends
``"nextRun": null`` for a job that is *Not scheduled* and ``"lastRun": null`` for a job
that has never run, but the OpenAPI schema declares both as non-nullable ``date-time``
properties, so the generated model calls ``isoparse(None)`` and raises
``TypeError: object of type 'NoneType' has no len()``. The jobs response is parsed as a
whole, so a single such job makes *every* job disappear.

See https://github.com/Cenvora/ha-veeam-br/issues/83. This affects every API version
(1.2-rev1 through 1.3-rev2), so selecting a different one is not a workaround.

This module parses the same payload straight from JSON, tolerating nulls. It is kept free
of Home Assistant imports, and takes its datetime parser as an argument, so it can be
tested without Home Assistant installed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

# Path of the job-states endpoint, identical in every API version the library ships.
# Only used if the generated module stops exposing _get_kwargs().
JOBS_STATES_URL = "/api/v1/jobs/states"


def _job_id(raw_id: Any) -> str | None:
    """Normalize a job ID to match ``str(model.id)`` from the typed path.

    Entity unique IDs are derived from this, so the two paths must agree on casing or a
    server switching between them would duplicate every job entity.
    """
    if not raw_id:
        return None
    try:
        return str(UUID(str(raw_id)))
    except (ValueError, AttributeError, TypeError):
        return str(raw_id)


def _timestamp(raw_value: Any, parse_datetime: Callable[[str], datetime | None]) -> datetime | None:
    """Parse a timestamp, treating null and unparseable values as absent."""
    if not raw_value or not isinstance(raw_value, str):
        return None
    return parse_datetime(raw_value)


def job_from_entry(
    entry: dict[str, Any], parse_datetime: Callable[[str], datetime | None]
) -> dict[str, Any] | None:
    """Build a coordinator job dict from one raw payload entry.

    Returns None if the entry has no usable ID, since everything downstream keys off it.
    Field names and fallback values match the typed path so sensors cannot tell which
    path produced the data.
    """
    job_id = _job_id(entry.get("id"))
    if job_id is None:
        return None

    return {
        "id": job_id,
        "name": entry.get("name") or "Unknown",
        "type": entry.get("type") or "unknown",
        "status": entry.get("status") or "unknown",
        "last_result": entry.get("lastResult") or "unknown",
        "last_run": _timestamp(entry.get("lastRun"), parse_datetime),
        "next_run": _timestamp(entry.get("nextRun"), parse_datetime),
    }


def jobs_from_payload(
    payload: Any, parse_datetime: Callable[[str], datetime | None]
) -> tuple[list[dict[str, Any]], int]:
    """Build coordinator job dicts from a raw job-states payload.

    Returns the parsed jobs and the number of entries that had to be skipped, so the
    caller can log a skip without this module needing a logger.
    """
    if not isinstance(payload, dict):
        return [], 0

    entries = payload.get("data")
    if not isinstance(entries, list):
        return [], 0

    jobs: list[dict[str, Any]] = []
    skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        job = job_from_entry(entry, parse_datetime)
        if job is None:
            skipped += 1
            continue
        jobs.append(job)

    return jobs, skipped
