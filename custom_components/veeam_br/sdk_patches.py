"""Runtime patches making the veeam-br generated models tolerate null values.

Veeam's published schema declares many properties non-nullable that the server nonetheless
sends as ``null``. The generated models reproduce the schema faithfully, so each such null
raises while parsing — and because a response is parsed as a whole, one null makes the
entire endpoint's data disappear. Four shapes of this have been reported:

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
* a null enum — ``ELicensePackageType(None)`` raises
  ``ValueError: None is not a valid ELicensePackageType``. VBR sends ``"package": null`` in
  the instance license summary of a server running on the evaluation period, so a server
  with no license installed had no license data at all (issue #104).

Each patch maps a null onto ``UNSET``, the sentinel the generated code already uses for an
absent field: ``to_dict`` skips it and this integration already reads it as None. Nothing
else changes — a null was previously an exception, never a value, so no payload that parses
today parses differently afterwards.

A related shape is handled alongside: an enum value the SDK revision was not generated
with — a job type or session result added in a later VBR build — raises ``ValueError: 'X'
is not a valid EJobStatus`` and loses the whole response the same way. Such a value is kept
as an ``UnknownEnumValue``, a string that also answers ``.value``, so the rest of the
payload parses and the raw value still reaches the sensor.

Deliberately *not* done: stripping nulls from the payload before parsing. That looks
simpler but regresses fields that are required and legitimately nullable, such as the four
progress rates in SessionProgressType0 (nested in JobStateModel), where a null is valid
input and dropping the key raises KeyError instead.

Generated model modules do ``from dateutil.parser import isoparse`` / ``from uuid import
UUID`` / ``from ..models.e_thing import EThing`` and call them by those names, so rebinding
the names per module changes only that module's parsing — patching ``dateutil``, ``uuid``
or a shared enum class globally would affect every other importer of it.

Deliberately *not* made tolerant: a ``from_dict`` handed something that is not an object at
all. That is not a null, it is a payload of an unexpected shape, and swallowing it would
hide a real protocol mismatch. What is added is a name for it: the generated
``dict(src_dict)`` reports a string or a list as ``ValueError: dictionary update sequence
element #0 has length 1; 2 is required``, which identifies neither the model nor the value,
and has now been reported twice without either being recoverable from the log (issues #80
and #104). It is re-raised carrying both.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
import logging
from types import ModuleType
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Marker set on a patched module so re-running is free and idempotent
PATCH_MARKER = "_ha_veeam_br_null_tolerance_patched"

# How much of an unexpected payload to quote when from_dict is handed a non-object. Enough
# to recognize an error string or a HATEOAS link, short enough not to spill a whole
# response — or any credential inside one — into the log.
UNEXPECTED_PAYLOAD_CHARS = 200


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
    if isinstance(uuid_type, type):
        setattr(module, "UUID", _tolerant_uuid_class(uuid_type, unset))


class _TolerantUUIDMeta(type):
    """Metaclass making a stand-in for ``uuid.UUID`` behave like the real class.

    Calling the stand-in parses as UUID does, except that a null reads as absent. The name
    is also used for ``isinstance(value, UUID)`` in some generated ``to_dict`` methods (for
    example DiskPartitionModel in 1.3-rev2); a plain function there raises TypeError, so
    instance and subclass checks are delegated to the real class.
    """

    _uuid_type: type
    _unset: Any

    def __call__(cls, value: Any = None, *args: Any, **kwargs: Any) -> Any:
        if value is None and not args and not kwargs:
            return cls._unset
        return cls._uuid_type(value, *args, **kwargs)

    def __instancecheck__(cls, instance: Any) -> bool:
        return isinstance(instance, cls._uuid_type)

    def __subclasscheck__(cls, subclass: type) -> bool:
        return issubclass(subclass, cls._uuid_type)

    def __repr__(cls) -> str:
        return f"<null-tolerant {cls._uuid_type.__name__}>"


def _tolerant_uuid_class(uuid_type: type, unset: Any) -> type:
    """Build the null-tolerant stand-in for one module's UUID binding."""
    return _TolerantUUIDMeta("UUID", (), {"_uuid_type": uuid_type, "_unset": unset})


class UnknownEnumValue(str):
    """An enum value this SDK revision does not know, kept as the string the server sent.

    Veeam adds enum members between releases — a job type, a repository type, a session
    result — and the generated code raises ValueError for any value it was not generated
    with, which loses the whole response over one field. Keeping the raw string lets the
    rest of the payload through. ``.value`` mirrors an enum member, so code that reads
    ``member.value`` (including the generated to_dict) keeps working.
    """

    __slots__ = ()

    @property
    def value(self) -> str:
        return str(self)


class _NullTolerantEnum:
    """Stand-in for an enum class that reads a null as absent.

    Generated code uses the enum name in three ways: as a constructor, to reach a member
    (``EJobType.BACKUP``), and in annotations — which are strings here, since every model
    module starts with ``from __future__ import annotations``. The first is intercepted and
    the second delegated, so the substitution is invisible to the rest of the module.

    ``isinstance`` against the name would break, but no generated model module does that —
    the parsed value is compared against ``Unset``, never against its own enum class. (UUID
    is different: some modules do check ``isinstance(value, UUID)``, which is why its
    stand-in is a class; see _TolerantUUIDMeta.)

    A string the enum does not know comes back as an UnknownEnumValue rather than raising.
    """

    def __init__(self, enum_class: type[Enum], unset: Any) -> None:
        self._enum_class = enum_class
        self._unset = unset

    def __call__(self, value: Any = None, *args: Any, **kwargs: Any) -> Any:
        if value is None and not args and not kwargs:
            return self._unset
        try:
            return self._enum_class(value, *args, **kwargs)
        except ValueError:
            if args or kwargs or not isinstance(value, str):
                raise
            # A value newer than this SDK revision: keep it rather than lose the response
            _LOGGER.debug(
                "Unknown %s value %r; keeping it as a plain string",
                self._enum_class.__name__,
                value,
            )
            return UnknownEnumValue(value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._enum_class, name)

    def __repr__(self) -> str:
        return f"<null-tolerant {self._enum_class.__name__}>"


def _patch_enums(module: ModuleType, unset: Any) -> None:
    """Rebind the enum classes this module parses with so a null parses as absent.

    The enum class itself is left alone: it is shared by every model module that imports
    it, and by the integration's own code, which compares real members against it. Only
    imported names are rebound — an enum's own defining module keeps the real class, so
    anything importing it afterwards still gets the enum rather than the stand-in.
    """
    for name, attribute in list(vars(module).items()):
        if _is_imported_enum(attribute, module):
            setattr(module, name, _NullTolerantEnum(attribute, unset))


def _is_imported_enum(attribute: Any, module: ModuleType) -> bool:
    """Whether this module-level name is an enum class defined somewhere else."""
    return (
        isinstance(attribute, type)
        and issubclass(attribute, Enum)
        and attribute.__module__ != module.__name__
    )


def _patch_from_dict(module: ModuleType, unset: Any) -> None:
    """Make the module's model classes read a null object as absent.

    Only the null case is intercepted; a real payload goes to the generated from_dict
    untouched. A payload that is neither null nor an object still fails, but is named.
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
            if not isinstance(src_dict, Mapping):
                raise TypeError(_describe_unexpected_payload(cls, src_dict))
            return _original(cls, src_dict)

        attribute.from_dict = classmethod(tolerant_from_dict)


def _describe_unexpected_payload(cls: type, src_dict: Any) -> str:
    """Explain a from_dict payload that is not a JSON object."""
    quoted = repr(src_dict)
    if len(quoted) > UNEXPECTED_PAYLOAD_CHARS:
        quoted = f"{quoted[:UNEXPECTED_PAYLOAD_CHARS]}..."
    return (
        f"{cls.__name__} expected a JSON object but the server sent "
        f"{type(src_dict).__name__} {quoted}"
    )


def patch_null_values(module: ModuleType, unset: Any) -> bool:
    """Make one generated model module tolerate nulls where the schema promises a value.

    Returns True if the module was patched, False if it was already patched or holds
    nothing to patch.
    """
    if getattr(module, PATCH_MARKER, False):
        return False

    has_parsers = hasattr(module, "isoparse") or hasattr(module, "UUID")
    has_enums = any(_is_imported_enum(attribute, module) for attribute in vars(module).values())
    has_models = any(
        isinstance(attribute, type)
        and attribute.__module__ == module.__name__
        and hasattr(attribute, "from_dict")
        for attribute in vars(module).values()
    )
    if not has_parsers and not has_enums and not has_models:
        return False

    _patch_parsers(module, unset)
    _patch_enums(module, unset)
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
