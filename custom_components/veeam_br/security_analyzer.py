"""Security & Compliance Analyzer: best practice checks and the analyzer's last run.

Every API revision has it. The analyzer runs on the server's schedule, or when started from
the Security device's button, and marks each best practice OK, Violation, Unable to check or
Suppressed. A check that falls into violation is also fired on the Home Assistant bus.

Only the Backup Administrator and Security Administrator roles may read it. Another account
is refused (403); the integration then stops asking until it is reloaded, rather than
reporting a failure every poll for something the account was never meant to see.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .tracking import with_iso_times

# Fired on the Home Assistant bus for a best practice first seen in violation after startup
EVENT_BEST_PRACTICE_VIOLATION = "veeam_br_best_practice_violation"

VIOLATION = "Violation"
ANALYZING = "Analyzing"

# Session states in which the analyzer is not running
FINISHED_STATES = frozenset({"Stopped", "Idle"})


def parse_best_practice(
    item: Any, get_enum_value: Callable[..., Any], get_uuid_value: Callable[[Any], str | None]
) -> dict[str, Any] | None:
    practice_id = get_uuid_value(getattr(item, "id", None))
    if not practice_id:
        return None
    return {
        "id": practice_id,
        "name": _text(getattr(item, "best_practice", None)),
        "status": get_enum_value(getattr(item, "status", None), None),
        "note": _text(getattr(item, "note", None)),
    }


def parse_last_run(
    session: Any, get_enum_value: Callable[..., Any], get_datetime_value: Callable[[Any], Any]
) -> dict[str, Any]:
    result = getattr(session, "result", None)
    return {
        "state": get_enum_value(getattr(session, "state", None), None),
        "result": get_enum_value(getattr(result, "result", None), None) if result else None,
        "message": _text(getattr(result, "message", None)) if result else None,
        "started": get_datetime_value(getattr(session, "creation_time", None)),
        "ended": get_datetime_value(getattr(session, "end_time", None)),
        "initiated_by": _text(getattr(session, "initiated_by", None)),
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def summarize(practices: list[dict[str, Any]], last_run: dict[str, Any] | None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for practice in practices:
        status = practice.get("status") or "Unknown"
        counts[status] = counts.get(status, 0) + 1
    violating = [p for p in practices if p.get("status") == VIOLATION]
    return {
        "violations": len(violating),
        "counts": counts,
        "violating": [{"id": p["id"], "name": p["name"], "note": p["note"]} for p in violating],
        "last_run": last_run,
    }


def is_running(last_run: dict[str, Any] | None) -> bool:
    state = (last_run or {}).get("state")
    return state is not None and state not in FINISHED_STATES


def last_run_attributes(last_run: dict[str, Any] | None) -> dict[str, Any]:
    return with_iso_times(last_run or {})


class ViolationTracker:
    """Which best practices have been seen in violation, across polls.

    The first poll's violations were there before startup: remembered, not announced. After
    that, a practice newly in violation is new. While the analyzer runs, checks read
    Analyzing; one that was in violation before is still counted as seen, so a run that
    finds the same violations again does not announce them again.
    """

    def __init__(self) -> None:
        self._seen: set[str] | None = None

    def update(self, practices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        violating = [p for p in practices if p.get("status") == VIOLATION]
        analyzing = {p["id"] for p in practices if p.get("status") == ANALYZING}
        ids = {p["id"] for p in violating}
        if self._seen is None:
            new = []
            self._seen = ids
        else:
            new = [p for p in violating if p["id"] not in self._seen]
            self._seen = ids | (self._seen & analyzing)
        return new
