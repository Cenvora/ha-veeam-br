"""Tests for tolerant job-states parsing (issue #83).

jobs_raw imports no Home Assistant modules, so it can be exercised directly. The datetime
parser is injected; these tests use a stand-in with the same contract as
homeassistant.util.dt.parse_datetime (str -> datetime | None).
"""

import importlib
import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest


def _load_jobs_raw():
    """Load jobs_raw.py standalone, without importing the integration package."""
    module_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "jobs_raw.py"
    spec = importlib.util.spec_from_file_location("veeam_br_jobs_raw", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_datetime(value):
    """Stand-in for homeassistant.util.dt.parse_datetime.

    Like the real one, it truncates over-long fractional seconds (Veeam sends 7 digits,
    which fromisoformat rejects) and returns None rather than raising.
    """
    value = re.sub(r"\.(\d{6})\d+", r".\1", value)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


JOB_ID = "6f1f0f8a-0000-4000-8000-000000000001"


def job_entry(**overrides):
    """A job entry as VBR serializes it, before overrides."""
    entry = {
        "id": JOB_ID,
        "name": "Nightly Backup",
        "type": "Backup",
        "status": "inactive",
        "lastResult": "Success",
        "lastRun": "2026-08-11T02:00:00.0000000+00:00",
        "nextRun": "2026-08-12T02:00:00.0000000+00:00",
    }
    entry.update(overrides)
    return entry


def test_not_scheduled_job_is_parsed():
    """A job with nextRun=null must parse — this is the issue #83 payload."""
    jobs_raw = _load_jobs_raw()

    jobs, skipped = jobs_raw.jobs_from_payload(
        {"data": [job_entry(nextRun=None, nextRunPolicy="Not scheduled")]},
        parse_datetime,
    )

    assert skipped == 0
    assert len(jobs) == 1
    assert jobs[0]["next_run"] is None, "a null nextRun should read as absent, not fail"
    assert jobs[0]["last_run"] == datetime(2026, 8, 11, 2, 0, tzinfo=timezone.utc)


def test_never_run_job_is_parsed():
    """A job that has never run sends lastRun=null."""
    jobs_raw = _load_jobs_raw()

    jobs, skipped = jobs_raw.jobs_from_payload({"data": [job_entry(lastRun=None)]}, parse_datetime)

    assert skipped == 0
    assert jobs[0]["last_run"] is None
    assert jobs[0]["next_run"] == datetime(2026, 8, 12, 2, 0, tzinfo=timezone.utc)


def test_one_bad_job_does_not_hide_the_others():
    """The regression behind issue #83: a single bad entry lost every job."""
    jobs_raw = _load_jobs_raw()

    payload = {
        "data": [
            job_entry(),
            job_entry(id="6f1f0f8a-0000-4000-8000-000000000002", nextRun=None),
            "not-a-dict",
            job_entry(id=None, name="No ID"),
            job_entry(id="6f1f0f8a-0000-4000-8000-000000000003", name=None, type=None),
        ]
    }
    jobs, skipped = jobs_raw.jobs_from_payload(payload, parse_datetime)

    assert len(jobs) == 3, "usable jobs should survive alongside unusable entries"
    assert skipped == 2, "the non-dict entry and the ID-less entry should be counted"


def test_field_names_and_defaults_match_the_typed_path():
    """Sensors must not be able to tell which parse path produced the data."""
    jobs_raw = _load_jobs_raw()

    jobs, _ = jobs_raw.jobs_from_payload(
        {"data": [{"id": JOB_ID}]},
        parse_datetime,
    )

    assert jobs[0] == {
        "id": JOB_ID,
        "name": "Unknown",
        "type": "unknown",
        "status": "unknown",
        "last_result": "unknown",
        "last_run": None,
        "next_run": None,
    }


def test_job_id_casing_matches_typed_path():
    """Unique IDs derive from this, so casing must match str(UUID) from the models."""
    jobs_raw = _load_jobs_raw()

    jobs, _ = jobs_raw.jobs_from_payload({"data": [job_entry(id=JOB_ID.upper())]}, parse_datetime)

    assert jobs[0]["id"] == JOB_ID, "an uppercase UUID must normalize, or entities duplicate"


def test_non_uuid_id_is_preserved():
    """An unexpected ID format should still yield a usable job rather than vanish."""
    jobs_raw = _load_jobs_raw()

    jobs, skipped = jobs_raw.jobs_from_payload({"data": [job_entry(id="job-7")]}, parse_datetime)

    assert skipped == 0
    assert jobs[0]["id"] == "job-7"


def test_unparseable_timestamp_is_absent_not_fatal():
    """A timestamp the parser rejects should not lose the job."""
    jobs_raw = _load_jobs_raw()

    jobs, skipped = jobs_raw.jobs_from_payload(
        {"data": [job_entry(lastRun="not a timestamp", nextRun=12345)]}, parse_datetime
    )

    assert skipped == 0
    assert jobs[0]["last_run"] is None
    assert jobs[0]["next_run"] is None


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"data": None}, {"data": {}}, "unexpected", {"pagination": {"total": 0}}],
)
def test_unusable_payloads_yield_no_jobs(payload):
    """A malformed payload should degrade to empty, not raise."""
    jobs_raw = _load_jobs_raw()

    assert jobs_raw.jobs_from_payload(payload, parse_datetime) == ([], 0)


def test_endpoint_url_matches_the_generated_client():
    """The hardcoded URL is only a fallback, but it must still be correct."""
    jobs_raw = _load_jobs_raw()

    try:
        from veeam_br.versions import VERSION_TO_PACKAGE
    except ImportError:
        pytest.skip("veeam-br not installed")

    for package in VERSION_TO_PACKAGE.values():
        module = importlib.import_module(f"{package}.api.jobs.get_all_jobs_states")
        kwargs = module._get_kwargs()
        assert (
            kwargs["url"] == jobs_raw.JOBS_STATES_URL
        ), f"{package} uses {kwargs['url']}, not {jobs_raw.JOBS_STATES_URL}"
