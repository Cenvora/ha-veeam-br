"""Removing entities and devices for objects the server no longer reports.

One sweep serves every platform. It used to be repeated per platform, and each copy scanned
the whole entity registry — so the sensor platform deleted the binary sensors and buttons of
any repository missing from one poll, and the binary sensor platform never re-created them.

The rule that matters: absence is only evidence of deletion when the fetch that should have
listed the object succeeded and listed something. A failed fetch, or one that returned an
empty collection, is indistinguishable from an outage, so that kind is left alone this cycle.
Deleting the last job of a kind is left to the device's Delete button, which
async_remove_config_entry_device allows once the server stops reporting it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Object kind (as used in unique IDs and device identifiers) -> coordinator data key
PRUNABLE_KINDS = {
    "job": "jobs",
    "repository": "repositories",
    "sobr": "sobrs",
    "proxy": "proxies",
    "wan": "wan_accelerators",
}


def reported_ids(data: dict[str, Any] | None, key: str) -> set[str] | None:
    """IDs the server reported for one kind this cycle, or None if that cannot be trusted.

    None means "do not prune": no data, a failed fetch, or an empty collection. Callers
    treat None as "keep everything".
    """
    if not data:
        return None

    fetch_ok = data.get("fetch_ok")
    if isinstance(fetch_ok, dict) and not fetch_ok.get(key, False):
        return None

    ids = {item["id"] for item in data.get(key) or [] if isinstance(item, dict) and item.get("id")}
    return ids or None


def reported_extent_ids(data: dict[str, Any] | None) -> set[tuple[str, str]] | None:
    """(sobr_id, extent_id) pairs reported this cycle, or None if that cannot be trusted."""
    sobr_ids = reported_ids(data, "sobrs")
    if sobr_ids is None:
        return None
    return {
        (sobr["id"], extent["id"])
        for sobr in data.get("sobrs") or []
        if sobr.get("id")
        for extent in sobr.get("extents") or []
        if extent.get("id")
    }


def forget_missing(tracked: set, current: set | None) -> None:
    """Drop IDs from a platform's "already added" set once they are pruned.

    Without this a platform that saw an object once never adds it again, so an object that
    disappears and comes back — or whose entities were removed — stays missing until restart.
    """
    if current is not None:
        tracked.intersection_update(current)


def _stale(unique_id: str, prefix: str, active: Iterable[str]) -> bool:
    """Whether a unique ID of this kind belongs to none of the active objects."""
    rest = unique_id[len(prefix) :]
    return not any(rest.startswith(f"{object_id}_") for object_id in active)


@callback
def async_prune_stale(hass: HomeAssistant, entry: ConfigEntry, data: dict[str, Any] | None) -> None:
    """Remove this entry's entities and devices for objects the server stopped reporting."""
    if not data:
        return

    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)
    entry_id = entry.entry_id

    current = {kind: reported_ids(data, key) for kind, key in PRUNABLE_KINDS.items()}
    extents = reported_extent_ids(data)

    for kind, ids in current.items():
        if ids is None:
            _LOGGER.debug(
                "Not pruning %s entities: the fetch failed or reported nothing this cycle",
                kind,
            )

    for entity in list(er.async_entries_for_config_entry(entity_reg, entry_id)):
        unique_id = entity.unique_id or ""
        for kind, ids in current.items():
            prefix = f"{entry_id}_{kind}_"
            if not unique_id.startswith(prefix):
                continue
            if ids is not None and _stale(unique_id, prefix, ids):
                _LOGGER.info("Removing stale %s entity: %s", kind, entity.entity_id)
                entity_reg.async_remove(entity.entity_id)
            elif kind == "sobr" and extents is not None and "_extent_" in unique_id:
                # The SOBR still exists, but this extent may have been removed from it
                rest = unique_id[len(prefix) :]
                if not any(
                    rest.startswith(f"{sobr_id}_extent_{extent_id}_")
                    for sobr_id, extent_id in extents
                ):
                    _LOGGER.info("Removing stale SOBR extent entity: %s", entity.entity_id)
                    entity_reg.async_remove(entity.entity_id)
            break

    for device in list(dr.async_entries_for_config_entry(device_reg, entry_id)):
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            kind, _, object_id = identifier.partition("_")
            ids = current.get(kind)
            if ids is None or object_id in ids:
                continue
            _LOGGER.info("Removing stale %s device: %s", kind, device.name)
            owners = set(getattr(device, "config_entries", None) or {entry_id})
            if owners <= {entry_id}:
                device_reg.async_remove_device(device.id)
            else:
                # Before Home Assistant 2026.9 a device could belong to several entries,
                # and these identifiers are not scoped to one: two entries for the same
                # server shared devices. Detach this entry and leave the device to the
                # other; Home Assistant deletes it once no entry is left.
                device_reg.async_update_device(device.id, remove_config_entry_id=entry_id)
            break
