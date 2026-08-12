"""Tests for the null-timestamp model patch (issue #83).

These run against the real installed veeam-br models, so they verify the patch actually
fixes the reported failure rather than just that the code is present. sdk_patches imports
no Home Assistant modules, so it can be loaded directly.
"""

import importlib
import importlib.util
from datetime import datetime
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

    assert sdk_patches.patch_null_timestamps(module, unset) is True

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
    sdk_patches.patch_null_timestamps(
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
    sdk_patches.patch_null_timestamps(module, unset)
    after = module.JobStateModel.from_dict(job_payload(package, module))

    assert before.last_run == after.last_run
    assert before.next_run == after.next_run


def test_patch_is_idempotent():
    """Re-patching should be a no-op, not a stack of wrappers."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    module = _fresh_model_module(package, "job_state_model")
    assert sdk_patches.patch_null_timestamps(module, unset) is True
    patched_isoparse = module.isoparse

    assert sdk_patches.patch_null_timestamps(module, unset) is False
    assert module.isoparse is patched_isoparse


def test_module_without_isoparse_is_skipped():
    """Patching a module that parses no timestamps should report no work done."""
    sdk_patches = _load_sdk_patches()

    assert sdk_patches.patch_null_timestamps(ModuleType("no_timestamps"), object()) is False


def test_patch_models_reports_count_and_skips_missing():
    """patch_models should patch what exists and ignore what does not."""
    sdk_patches = _load_sdk_patches()
    package = sorted(set(veeam_br_versions.values()))[0]
    unset = importlib.import_module(f"{package}.types").UNSET

    loaded = {}

    def import_module(name):
        if name == "definitely_not_a_model":
            raise ImportError(name)
        module = _fresh_model_module(package, name)
        loaded[name] = module
        return module

    patched = sdk_patches.patch_models(import_module, unset)

    assert patched == len(loaded), "every importable listed module should be patched"
    assert patched > 0
    assert all(getattr(m, sdk_patches.PATCH_MARKER, False) for m in loaded.values())

    # A module that cannot be imported must not fail the whole run
    sdk_patches.TIMESTAMP_MODEL_MODULES = ("definitely_not_a_model",)
    assert sdk_patches.patch_models(import_module, unset) == 0


def test_listed_modules_exist_and_parse_timestamps():
    """Every listed module should exist in some version and actually use isoparse.

    A typo would otherwise sit here silently doing nothing.
    """
    sdk_patches = _load_sdk_patches()

    for name in sdk_patches.TIMESTAMP_MODEL_MODULES:
        found = []
        for package in sorted(set(veeam_br_versions.values())):
            spec = importlib.util.find_spec(f"{package}.models.{name}")
            if spec is None:
                continue
            module = importlib.import_module(f"{package}.models.{name}")
            found.append(hasattr(module, "isoparse"))

        assert found, f"{name} exists in no installed API version"
        assert any(found), f"{name} never parses a timestamp, so patching it does nothing"
