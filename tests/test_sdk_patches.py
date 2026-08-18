"""Tests for the null-tolerance model patches (issues #82 and #83).

These run against the real installed veeam-br models, so they verify the patch actually
fixes the reported failure rather than just that the code is present. sdk_patches imports
no Home Assistant modules, so it can be loaded directly.
"""

from datetime import datetime
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

veeam_br_versions = pytest.importorskip(
    "veeam_br.versions", reason="veeam-br not installed"
).VERSION_TO_PACKAGE


def _load_sdk_patches():
    """Load sdk_patches.py standalone, without importing the integration package."""
    module_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sdk_patches.py"
    spec = importlib.util.spec_from_file_location("veeam_br_sdk_patches", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh_model_module(package, name):
    """Import a model module under a throwaway name so patches don't leak between tests."""
    spec = importlib.util.find_spec(f"{package}.models.{name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job_payload(package, module, **overrides):
    """A valid serialized job for this API version, built with the model's own to_dict()."""

    def enum(enum_name):
        cls_name = "".join(part.title() for part in enum_name.split("_"))
        cls_name = cls_name.replace("E", "E", 1)
        enum_module = importlib.import_module(f"{package}.models.{enum_name}")
        return list(getattr(enum_module, cls_name))[0]

    JobStateModel = module.JobStateModel
    kwargs = {
        "id": __import__("uuid").UUID("6f1f0f8a-0000-4000-8000-000000000001"),
        "name": "Nightly Backup",
        "type_": enum("e_job_type"),
        "description": "probe",
        "status": enum("e_job_status"),
        "last_result": enum("e_session_result"),
        "workload": enum("e_job_workload"),
        "objects_count": 2,
        "last_run": datetime(2026, 8, 11, 2, 0),
        "next_run": datetime(2026, 8, 12, 2, 0),
    }
    # Required in 1.3-rev* but absent in 1.2-rev1
    field_names = {field.name for field in JobStateModel.__attrs_attrs__}
    for extra, value in (("high_priority", False), ("progress_percent", 100)):
        if extra in field_names:
            kwargs[extra] = value

    payload = JobStateModel(**kwargs).to_dict()
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
@pytest.mark.parametrize("null_field", ["nextRun", "lastRun"])
def test_null_timestamp_fails_before_patch_and_parses_after(package, null_field):
    """The reported failure, and its absence once patched, on every API version.

    nextRun is null for a job that is Not scheduled; lastRun is null for one that has
    never run.
    """
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_state_model")
    payload = job_payload(package, module, **{null_field: None})

    # Unpatched: the exact TypeError from the issue
    with pytest.raises(TypeError, match="object of type 'NoneType' has no len"):
        module.JobStateModel.from_dict(payload)

    assert sdk_patches.patch_null_values(module, unset) is True

    job = module.JobStateModel.from_dict(payload)
    field = "next_run" if null_field == "nextRun" else "last_run"
    assert getattr(job, field) is unset, "a null timestamp should read as the absent sentinel"

    # The other timestamp must still parse normally
    other = "last_run" if field == "next_run" else "next_run"
    assert isinstance(getattr(job, other), datetime)


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
def test_whole_response_survives_one_unscheduled_job(package):
    """The blast radius behind the issue: the response is parsed as a whole."""
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_state_model")
    scheduled = job_payload(package, module)
    unscheduled = job_payload(
        package,
        module,
        id="6f1f0f8a-0000-4000-8000-000000000002",
        name="Not Scheduled Job",
        nextRun=None,
        nextRunPolicy="Not scheduled",
    )

    # JobStatesResult delegates to JobStateModel, so patch the module it actually uses
    results_module = importlib.import_module(f"{package}.models.job_states_result")
    sdk_patches.patch_null_values(
        importlib.import_module(f"{package}.models.job_state_model"), unset
    )

    parsed = results_module.JobStatesResult.from_dict(
        {
            "data": [scheduled, unscheduled],
            "pagination": {"total": 2, "count": 2, "skip": 0, "limit": 100},
        }
    )

    assert len(parsed.data) == 2, "both jobs should survive, not just the scheduled one"
    assert parsed.data[1].next_run is unset


def test_valid_timestamps_are_untouched():
    """The patch must only change the null case."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_state_model")
    before = module.JobStateModel.from_dict(job_payload(package, module))
    sdk_patches.patch_null_values(module, unset)
    after = module.JobStateModel.from_dict(job_payload(package, module))

    assert before.last_run == after.last_run
    assert before.next_run == after.next_run


def test_patch_is_idempotent():
    """Re-patching should be a no-op, not a stack of wrappers."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_state_model")
    assert sdk_patches.patch_null_values(module, unset) is True
    patched_isoparse = module.isoparse

    assert sdk_patches.patch_null_values(module, unset) is False
    assert module.isoparse is patched_isoparse


def test_module_with_nothing_to_patch_is_skipped():
    """A module that parses nothing should report no work done."""
    sdk_patches = _load_sdk_patches()

    assert sdk_patches.patch_null_values(ModuleType("nothing_to_patch"), object()) is False


# ---------------------------------------------------------------------------
# Null UUID — VBR sends "hostId": null for repositories with no host (#82)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
def test_null_uuid_fails_before_patch_and_parses_after(package):
    """The repositories error from issue #82, on the model that parses `hostId`.

    Asserted at the module's UUID binding rather than through a full RepositoryStateModel
    payload, since which fields that model requires varies by API version.
    """
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "repository_state_model")
    assert "host_id" in {
        field.name for field in module.RepositoryStateModel.__attrs_attrs__
    }, "this model should be the one carrying the optional hostId"

    with pytest.raises(TypeError, match="hex, bytes, bytes_le, fields, or int"):
        module.UUID(None)

    assert sdk_patches.patch_null_values(module, unset) is True

    assert module.UUID(None) is unset, "a null UUID should read as the absent sentinel"
    real = "6f1f0f8a-0000-4000-8000-000000000001"
    assert str(module.UUID(real)) == real, "real UUIDs must still parse"


# ---------------------------------------------------------------------------
# Null nested object — VBR sends "instanceLicenseSummary": null (#82)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
def test_null_nested_object_fails_before_patch_and_parses_after(package):
    """The license error from issue #82: from_dict starts with dict(src_dict)."""
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "instance_license_summary_model")
    model = module.InstanceLicenseSummaryModel

    with pytest.raises(TypeError, match="'NoneType' object is not iterable"):
        model.from_dict(None)

    assert sdk_patches.patch_null_values(module, unset) is True

    assert model.from_dict(None) is unset, "a null nested object should read as absent"


def test_required_nullable_fields_still_parse():
    """Guard against 'just strip the nulls', which would break these.

    SessionProgressType0 (nested in JobStateModel) has four fields that are required and
    legitimately nullable, so a null is valid input there and dropping the key would raise
    KeyError. The patches must leave that working.
    """
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "session_progress_type_0")
    bottleneck = list(
        importlib.import_module(
            f"{package}.models.e_session_bottleneck_type"
        ).ESessionBottleneckType
    )[0]
    progress = module.SessionProgressType0(
        duration="00:10:00",
        processing_rate=None,
        bottleneck=bottleneck,
        processed_size=None,
        read_size=None,
        transferred_size=None,
    )
    payload = progress.to_dict()
    assert payload["processingRate"] is None, "the fixture should carry the valid nulls"

    before = module.SessionProgressType0.from_dict(dict(payload))
    assert sdk_patches.patch_null_values(module, unset) is True
    after = module.SessionProgressType0.from_dict(dict(payload))

    for field in ("duration", "processing_rate", "processed_size", "read_size"):
        assert getattr(after, field) == getattr(before, field), f"{field} changed"
    assert after.processing_rate is None, "a required nullable field must stay None"


# ---------------------------------------------------------------------------
# patch_models over a whole API version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
def test_patch_models_covers_the_whole_models_package(package):
    """Importing the package imports every model, so all of them get patched."""
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    models_package = f"{package}.models"
    importlib.import_module(models_package)
    modules = {
        name: module
        for name, module in list(__import__("sys").modules.items())
        if name.startswith(f"{models_package}.")
    }

    patched = sdk_patches.patch_models(models_package, unset, modules)

    assert patched > 100, f"expected the full models package, patched only {patched}"

    # Re-running is idempotent
    assert sdk_patches.patch_models(models_package, unset, modules) == 0


def test_patch_models_ignores_other_packages_and_none_entries():
    """Only the requested version's models are touched, and stale None entries are safe."""
    sdk_patches = _load_sdk_patches()

    other = ModuleType("some.other.models.thing")
    other.isoparse = lambda value: value
    modules = {
        "wanted.models.a": ModuleType("wanted.models.a"),
        "some.other.models.thing": other,
        "wanted.models.stale": None,
    }
    modules["wanted.models.a"].isoparse = lambda value: value

    patched = sdk_patches.patch_models("wanted.models", object(), modules)

    assert patched == 1
    assert not getattr(other, sdk_patches.PATCH_MARKER, False), "other packages untouched"


# ---------------------------------------------------------------------------
# Null enum — VBR sends "package": null on an unlicensed server (#104)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
def test_null_enum_fails_before_patch_and_parses_after(package):
    """The license error from issue #104, reported by a server in evaluation mode."""
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "instance_license_summary_model")
    real = list(module.ELicensePackageType)[0]

    with pytest.raises(ValueError, match="None is not a valid ELicensePackageType"):
        module.ELicensePackageType(None)

    assert sdk_patches.patch_null_values(module, unset) is True

    assert module.ELicensePackageType(None) is unset, "a null enum should read as absent"
    assert module.ELicensePackageType(real.value) is real, "real values must still parse"


@pytest.mark.parametrize("package", sorted(set(veeam_br_versions.values())))
def test_null_package_no_longer_empties_the_license_summary(package):
    """The blast radius: one null enum lost every license figure on the server."""
    sdk_patches = _load_sdk_patches()
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "instance_license_summary_model")
    payload = {
        "licensedInstancesNumber": 10,
        "usedInstancesNumber": 4,
        "newInstancesNumber": 0,
        "rentalInstancesNumber": 0,
        "package": None,
    }

    with pytest.raises(ValueError, match="None is not a valid ELicensePackageType"):
        module.InstanceLicenseSummaryModel.from_dict(dict(payload))

    assert sdk_patches.patch_null_values(module, unset) is True

    summary = module.InstanceLicenseSummaryModel.from_dict(dict(payload))
    assert summary.package is unset
    assert summary.licensed_instances_number == 10, "the rest of the summary should survive"


def test_enum_members_are_still_reachable_through_the_patched_name():
    """Generated code reaches members off the module-level name, not only the class."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_state_model")
    before = module.EJobType.BACKUP

    assert sdk_patches.patch_null_values(module, unset) is True

    assert module.EJobType.BACKUP is before, "member access must pass through unchanged"


def test_the_enum_class_itself_is_left_alone():
    """The stand-in must not leak: the enum is shared by every module that imports it."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    defining_module = _fresh_model_module(package, "e_job_type")
    real = defining_module.EJobType

    sdk_patches.patch_null_values(defining_module, unset)

    assert defining_module.EJobType is real, "an enum's own module keeps the real class"


# ---------------------------------------------------------------------------
# A payload that is not an object at all — named rather than made tolerant (#80, #104)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    ["Unauthorized", ["a"], 42],
    ids=["error-string", "list", "number"],
)
def test_a_non_object_payload_is_reported_with_the_model_and_the_value(payload):
    """dict("Unauthorized") says only "sequence element #0 has length 1" (#80, #104)."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_states_result")
    assert sdk_patches.patch_null_values(module, unset) is True

    with pytest.raises(TypeError) as raised:
        module.JobStatesResult.from_dict(payload)

    message = str(raised.value)
    assert "JobStatesResult" in message, "the model must be named"
    assert type(payload).__name__ in message
    assert repr(payload) in message, "the offending value must be quoted"


def test_a_huge_non_object_payload_is_truncated():
    """A whole response body — or a credential inside one — must not reach the log."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_states_result")
    sdk_patches.patch_null_values(module, unset)

    with pytest.raises(TypeError) as raised:
        module.JobStatesResult.from_dict("x" * 10_000)

    assert len(str(raised.value)) < sdk_patches.UNEXPECTED_PAYLOAD_CHARS + 200
    assert str(raised.value).endswith("...")
