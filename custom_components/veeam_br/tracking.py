"""Telling which items are new since the last poll, for firing bus events."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def with_iso_times(item: dict[str, Any]) -> dict[str, Any]:
    """The item with its datetimes as ISO 8601 text, for attributes and bus events."""
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in item.items()
    }


def time_key(field: str):
    """A sort key on a datetime field; items without one sort as oldest."""

    def key(item: dict[str, Any]) -> float:
        value = item.get(field)
        return value.timestamp() if isinstance(value, datetime) else float("-inf")

    return key


class NewItemTracker:
    """Which items, by ``id``, have been seen across polls.

    The first poll's items were already there before startup: they are remembered, not
    announced, so a restart does not refire them. After that, an item not seen before is
    new. Only the current IDs are kept, which is all the endpoint can return again.
    """

    def __init__(self) -> None:
        self._seen: set[str] | None = None

    def update(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Take one poll's items; return those not seen before."""
        ids = {item["id"] for item in items}
        new = [] if self._seen is None else [item for item in items if item["id"] not in self._seen]
        self._seen = ids
        return new
