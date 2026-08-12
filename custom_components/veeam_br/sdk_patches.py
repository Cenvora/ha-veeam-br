"""Runtime patches for the veeam-br generated models.

Only one patch so far: making timestamp parsing tolerate null.

Veeam Backup & Replication sends ``"nextRun": null`` for a job that is *Not scheduled* and
``"lastRun": null`` for a job that has never run. The OpenAPI schema declares both as
non-nullable ``date-time`` properties, so the generated model calls ``isoparse(None)`` and
raises ``TypeError: object of type 'NoneType' has no len()``. Responses are parsed as a
whole, so a single such job makes *every* job disappear.

See https://github.com/Cenvora/ha-veeam-br/issues/83. This affects every API version
(1.2-rev1 through 1.3-rev2), so selecting a different one is not a workaround.

The discrepancy is in Veeam's own published schema, which declares these properties
non-nullable while the server sends null. veeam-br's models are generated from that
schema, so they faithfully reproduce it; the mismatch has to be absorbed at runtime by
consumers, which is what this module does.

Each generated model module does ``from dateutil.parser import isoparse`` and calls it by
that name, so rebinding the name on the module makes the module's own ``from_dict``
tolerant without touching shared library state — patching ``dateutil`` itself would change
behavior for every other integration in the process.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import ModuleType
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Marker set on a patched module so re-running is free and idempotent
PATCH_MARKER = "_ha_veeam_br_null_timestamps_patched"

# Model modules whose timestamps this integration reads. Only the modules that actually
# parse a date-time need patching; add to this list when consuming a new endpoint whose
# model has an optional timestamp.
TIMESTAMP_MODEL_MODULES = (
    # jobs.get_all_jobs_states — lastRun/nextRun, the fields behind issue #83
    "job_state_model",
    # license_.get_installed_license — expiration dates, null on licenses that never expire
    "installed_license_model",
    "instance_license_summary_model",
    # login.create_token — token issue/expiry timestamps
    "token_model",
)


def patch_null_timestamps(module: ModuleType, unset: Any) -> bool:
    """Make one generated model module tolerate null timestamps.

    Rebinds the module's ``isoparse`` so a null parses as ``unset`` — the sentinel the
    generated code already uses for an absent field, which ``to_dict`` skips and this
    integration already maps to None. Returns True if the module was patched, False if it
    was already patched or has no ``isoparse`` to patch.
    """
    original = getattr(module, "isoparse", None)
    if original is None or getattr(module, PATCH_MARKER, False):
        return False

    def isoparse(value: Any) -> Any:
        """Parse a timestamp, treating null as an absent value rather than an error."""
        if value is None:
            return unset
        return original(value)

    setattr(module, "isoparse", isoparse)
    setattr(module, PATCH_MARKER, True)
    return True


def patch_models(import_module: Callable[[str], ModuleType], unset: Any) -> int:
    """Patch every model module in TIMESTAMP_MODEL_MODULES that exists.

    ``import_module`` is injected (rather than calling importlib here) so the caller can
    run the blocking imports off the event loop. Missing modules are skipped, since which
    models exist varies by API version.
    """
    patched = 0
    for name in TIMESTAMP_MODEL_MODULES:
        try:
            module = import_module(name)
        except ImportError:
            _LOGGER.debug("Model module %s not present in this API version", name)
            continue

        if patch_null_timestamps(module, unset):
            patched += 1

    return patched
