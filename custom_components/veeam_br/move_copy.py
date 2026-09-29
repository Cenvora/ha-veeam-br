"""Move/copy backup sessions awaiting action (API 1.3-rev2, VBR 13.1).

A backup move or copy that fails part-way stops in the ``ActionRequired`` state until
someone chooses to retry it, detach what failed, or stop and undo it. The server lists those
sessions at ``/backups/moveCopySessions`` — a plain array, not a paged collection — and takes
the choice at ``/backups/moveCopySessions/{id}/manage``, exposed here as the
``manage_move_copy_session`` action. A session that starts waiting is also fired on the Home
Assistant bus, so an automation can offer the choice, for example as an actionable
notification.

Both endpoints are for the Backup Administrator role only. An account without it is refused
(403); the integration then stops asking until it is reloaded, rather than reporting a
failure every poll for something the account was never meant to see.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .tracking import time_key, with_iso_times

# Fired on the Home Assistant bus for every session first seen awaiting action after startup
EVENT_MOVE_COPY = "veeam_br_move_copy_action_required"

SERVICE_MANAGE_MOVE_COPY = "manage_move_copy_session"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_SESSION_ID = "session_id"
ATTR_ACTION = "action"

# Service action values, and the EMoveCopySessionAction value each maps to
ACTIONS = {
    "retry": "Retry",
    "forget_failed": "ForgetFailed",
    "stop_and_undo": "StopAndUndo",
}

# How many sessions the sensor lists in its attributes. The count is exact; the list is for a
# notification, and an attribute holding hundreds would bloat the recorder.
SESSIONS_LISTED = 25


def parse_session(
    session: Any,
    job_names: dict[str, str | None],
    get_enum_value: Callable[..., Any],
    get_uuid_value: Callable[[Any], str | None],
    get_datetime_value: Callable[[Any], Any],
) -> dict[str, Any] | None:
    session_id = get_uuid_value(getattr(session, "id", None))
    if not session_id:
        return None
    job_id = get_uuid_value(getattr(session, "job_id", None))
    result = getattr(session, "result", None)
    progress = getattr(session, "progress_percent", None)
    return {
        "id": session_id,
        "name": _text(getattr(session, "name", None)),
        "type": get_enum_value(getattr(session, "session_type", None), None),
        "job_id": job_id,
        "job_name": job_names.get(job_id) if job_id else None,
        "created": get_datetime_value(getattr(session, "creation_time", None)),
        "progress": (
            progress if isinstance(progress, int) and not isinstance(progress, bool) else None
        ),
        "result": get_enum_value(getattr(result, "result", None), None) if result else None,
        "message": _text(getattr(result, "message", None)) if result else None,
        "platform": _text(getattr(session, "platform_name", None)),
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def summarize_sessions(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """The count, and the oldest-waiting sessions first — those most in need of a decision."""
    ordered = sorted(sessions, key=time_key("created"))
    return {
        "count": len(sessions),
        "sessions": [with_iso_times(session) for session in ordered[:SESSIONS_LISTED]],
    }
