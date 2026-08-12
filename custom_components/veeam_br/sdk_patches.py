"""Runtime patches making the veeam-br generated models tolerate null values.

Veeam's published schema declares many properties non-nullable that the server nonetheless
sends as ``null``. The generated models reproduce the schema faithfully, so each such null
raises while parsing — and because a response is parsed as a whole, one null makes the
entire endpoint's data disappear. Three shapes of this have been reported:

* a null timestamp — ``isoparse(None)`` raises
  ``TypeError: object of type 'NoneType' has no len()``. VBR sends ``"nextRun": null`` for
  a job that is *Not scheduled* and ``"lastRun": null`` for one that has never run, so a
  single unscheduled job hid every job (issue #83).
* a null UUID — ``UUID(None)`` raises
  ``TypeError: one of the hex, bytes, bytes_le, fields, or int arguments must be given``.
  VBR sends ``"hostId": null`` for repositories not bound to a host, so one such
  repository hid every repository (issue #82).
* a null nested object — ``SomeModel.from_dict(None)`` raises
  ``TypeError: 'NoneType' object is not iterable``, because from_dict starts with
  ``dict(src_dict)``. VBR sends ``"instanceLicenseSummary": null`` when that summary does
  not apply, which lost all license data (issue #82).

Each patch maps a null onto ``UNSET``, the sentinel the generated code already uses for an
absent field: ``to_dict`` skips it and this integration already reads it as None. Nothing
else changes — a null was previously an exception, never a value, so no payload that parses
today parses differently afterwards.

Deliberately *not* done: stripping nulls from the payload before parsing. That looks
simpler but regresses fields that are required and legitimately nullable, such as the four
progress rates in SessionProgressType0 (nested in JobStateModel), where a null is valid
input and dropping the key raises KeyError instead.

Generated model modules do ``from dateutil.parser import isoparse`` / ``from uuid import
UUID`` and call them by those names, so rebinding the names per module changes only that
module's parsing — patching ``dateutil`` or ``uuid`` globally would affect every other
integration in the process.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from types import ModuleType
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Marker set on a patched module so re-running is free and idempotent
PATCH_MARKER = "_ha_veeam_br_null_tolerance_patched"


def _patch_parsers(module: ModuleType, unset: Any) -> None:
    """Rebind the module's isoparse/UUID so a null parses as absent."""
    isoparse = getattr(module, "isoparse", None)
    if isoparse is not None:

        def tolerant_isoparse(value: Any) -> Any:
            if value is None:
                return unset
            return isoparse(value)

        setattr(module, "isoparse", tolerant_isoparse)

    uuid_type = getattr(module, "UUID", None)
    if uuid_type is not None:

        def tolerant_uuid(value: Any = None, *args: Any, **kwargs: Any) -> Any:
            if value is None and not args and not kwargs:
                return unset
            return uuid_type(value, *args, **kwargs)

        setattr(module, "UUID", tolerant_uuid)


def _patch_from_dict(module: ModuleType, unset: Any) -> None:
    """Make the module's model classes read a null object as absent.

    Only the null case is intercepted; a real payload goes to the generated from_dict
    untouched.
    """
    for attribute in list(vars(module).values()):
        if not isinstance(attribute, type) or attribute.__module__ != module.__name__:
            continue

        from_dict = getattr(attribute, "from_dict", None)
        original = getattr(from_dict, "__func__", None)
        if original is None:
            continue

        def tolerant_from_dict(cls: type, src_dict: Any, _original=original) -> Any:
            if src_dict is None:
                return unset
            return _original(cls, src_dict)

        attribute.from_dict = classmethod(tolerant_from_dict)


def patch_null_values(module: ModuleType, unset: Any) -> bool:
    """Make one generated model module tolerate nulls where the schema promises a value.

    Returns True if the module was patched, False if it was already patched or holds
    nothing to patch.
    """
    if getattr(module, PATCH_MARKER, False):
        return False

    has_parsers = hasattr(module, "isoparse") or hasattr(module, "UUID")
    has_models = any(
        isinstance(attribute, type)
        and attribute.__module__ == module.__name__
        and hasattr(attribute, "from_dict")
        for attribute in vars(module).values()
    )
    if not has_parsers and not has_models:
        return False

    _patch_parsers(module, unset)
    _patch_from_dict(module, unset)
    setattr(module, PATCH_MARKER, True)
    return True


def patch_models(models_package: str, unset: Any, modules: Mapping[str, ModuleType]) -> int:
    """Patch every already-imported model module of one API version.

    ``modules`` is normally sys.modules, injected so this stays testable. Importing the
    models package imports all of its modules eagerly, so the caller only has to import
    the package itself first — every model is then reachable here.
    """
    prefix = f"{models_package}."
    patched = 0

    for name, module in list(modules.items()):
        if not name.startswith(prefix) or module is None:
            continue
        try:
            if patch_null_values(module, unset):
                patched += 1
        except Exception as err:  # noqa: BLE001 - one odd module must not stop the rest
            _LOGGER.debug("Could not patch %s: %s", name, err)

    return patched
