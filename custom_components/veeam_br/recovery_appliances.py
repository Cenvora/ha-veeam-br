"""Agent recovery appliances connected to the backup server (API 1.3-rev2, VBR 13.1).

A recovery appliance is a machine booted from Veeam Recovery Media that connects to the
backup server to restore from it, typically for a bare-metal recovery. They come and go with
restores, so they are one sensor on the Server device rather than devices of their own. One
connecting is also fired on the Home Assistant bus: on most servers that is rare, and an
unexpected or unverified one is worth a notification.

The verification phrase each appliance shows is deliberately left out: it is how an
administrator confirms the appliance is the one in front of them, and does not belong in
Home Assistant's history.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .tracking import time_key, with_iso_times

# Fired on the Home Assistant bus for every appliance first seen connected after startup
EVENT_RECOVERY_APPLIANCE = "veeam_br_recovery_appliance_connected"

# How many appliances the sensor lists in its attributes; the counts are exact
APPLIANCES_LISTED = 25


def parse_appliance(
    appliance: Any,
    get_enum_value: Callable[..., Any],
    get_uuid_value: Callable[[Any], str | None],
    get_datetime_value: Callable[[Any], Any],
) -> dict[str, Any] | None:
    appliance_id = get_uuid_value(getattr(appliance, "id", None))
    if not appliance_id:
        return None
    detached = getattr(appliance, "is_detached", None)
    verified = getattr(appliance, "is_verified", None)
    addresses = getattr(appliance, "addresses", None)
    return {
        "id": appliance_id,
        "host": _text(getattr(appliance, "host_name", None))
        or _text(getattr(appliance, "last_known_host_name", None)),
        "connected": (not detached) if isinstance(detached, bool) else None,
        "verified": verified if isinstance(verified, bool) else None,
        "endpoint": _text(getattr(appliance, "endpoint", None)),
        "addresses": (
            [a for a in addresses if isinstance(a, str)] if isinstance(addresses, list) else []
        ),
        "version": _text(getattr(appliance, "version", None)),
        "platform": get_enum_value(getattr(appliance, "platform_type", None), None),
        "connected_since": get_datetime_value(getattr(appliance, "creation_time", None)),
        "last_contact": get_datetime_value(getattr(appliance, "last_contact_time", None)),
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def summarize_appliances(appliances: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, and the connected appliances first, most recently in contact first."""
    connected = [a for a in appliances if a.get("connected")]
    last_contact = time_key("last_contact")
    ordered = sorted(
        appliances, key=lambda a: (bool(a.get("connected")), last_contact(a)), reverse=True
    )
    return {
        "connected": len(connected),
        "disconnected": len(appliances) - len(connected),
        "unverified": sum(1 for a in connected if a.get("verified") is False),
        "appliances": [with_iso_times(a) for a in ordered[:APPLIANCES_LISTED]],
    }
