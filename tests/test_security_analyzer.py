"""Security & Compliance Analyzer: best practice checks, the last run, and starting a run.

Runs the integration against the fake server in test_behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
pytest.importorskip("veeam_br.exceptions", reason="needs veeam-br 0.5.1")

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import async_capture_events  # noqa: E402

from custom_components.veeam_br.security_analyzer import (  # noqa: E402
    EVENT_BEST_PRACTICE_VIOLATION,
)

from .test_behaviour import (  # noqa: E402
    HEALTH_OK,
    error,
    make_entry,
    refresh,
    setup,
    state,
)
from .test_behaviour import auto_enable_custom_integrations  # noqa: F401 - the fixture
from .test_behaviour import server  # noqa: F401 - the fixture

PRACTICES = ("security", "get_best_practices_compliance_result")
LAST_RUN = ("security", "get_security_analyzer_session")
START = ("security", "start_security_analyzer")
VIOLATIONS = "sensor.vbr_security_vbr01_best_practice_violations"
LAST = "sensor.vbr_security_vbr01_last_analyzer_run"
PROBLEM = "binary_sensor.vbr_security_vbr01_best_practices"
BUTTON = "button.vbr_security_vbr01_run_security_analyzer"
NOW = datetime.now(timezone.utc).replace(microsecond=0)

IMMUTABLE = "1f8b6d34-9e02-47c5-a3d7-5b9c0e4f8a12"
MFA = "6a0c3e58-2b71-4d96-8f43-7c1e9a5d0b88"
ENCRYPTION = "0c1d2e3f-4a5b-4c6d-8e7f-8091a2b3c4d5"


def practice(practice_id: str, name: str, status: str, note: str = ""):
    return SimpleNamespace(id=practice_id, best_practice=name, status=status, note=note)


def practices(*items):
    return SimpleNamespace(items=list(items))


def run(state_: str = "Stopped", result: str = "Warning", ended: bool = True):
    return SimpleNamespace(
        id="run-1",
        name="Security & Compliance Analyzer",
        session_type="SecurityComplianceAnalyzer",
        state=state_,
        creation_time=NOW - timedelta(minutes=5),
        end_time=NOW if ended else None,
        result=SimpleNamespace(result=result, message="2 best practices violated"),
        initiated_by="VBR01\\veeam",
    )


def calls(server, key) -> list[dict]:  # noqa: F811
    return [
        kwargs for namespace, operation, kwargs in server.calls if (namespace, operation) == key
    ]


async def test_violations_and_the_last_run_are_reported(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    server.responses[PRACTICES] = practices(
        practice(IMMUTABLE, "Immutable backups are used", "Violation", "Remediation planned"),
        practice(MFA, "Multi-factor authentication is enabled", "OK"),
        practice(ENCRYPTION, "Backup encryption is enabled", "Suppressed", "Accepted risk"),
    )
    server.responses[LAST_RUN] = run()
    entry = make_entry()
    await setup(hass, entry)

    violations = hass.states.get(VIOLATIONS)
    assert violations.state == "1"
    assert violations.attributes["violating"] == [
        {"id": IMMUTABLE, "name": "Immutable backups are used", "note": "Remediation planned"}
    ]
    assert violations.attributes["by_status"] == {"Violation": 1, "OK": 1, "Suppressed": 1}
    assert state(hass, PROBLEM) == STATE_ON
    assert hass.states.get(PROBLEM).attributes["violating"] == ["Immutable backups are used"]

    last = hass.states.get(LAST)
    assert last.state == NOW.isoformat()
    assert last.attributes["result"] == "Warning"
    assert last.attributes["started"] == (NOW - timedelta(minutes=5)).isoformat()
    assert last.attributes["initiated_by"] == "VBR01\\veeam"

    device = er.async_get(hass).async_get(VIOLATIONS).device_id
    assert device == er.async_get(hass).async_get(BUTTON).device_id, "all on the Security device"


async def test_a_clean_server_is_ok(hass: HomeAssistant, server) -> None:  # noqa: F811
    server.responses[PRACTICES] = practices(practice(MFA, "MFA", "OK"))
    entry = make_entry()
    await setup(hass, entry)

    assert state(hass, VIOLATIONS) == "0"
    assert state(hass, PROBLEM) == STATE_OFF


async def test_an_analyzer_that_never_ran_is_unknown_not_a_failure(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    entry = make_entry()
    await setup(hass, entry)

    assert state(hass, LAST) == "unknown"
    assert state(hass, HEALTH_OK) == STATE_ON
    assert state(hass, BUTTON) != STATE_UNAVAILABLE


async def test_while_running_the_last_run_shows_its_start_and_the_button_waits(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    server.responses[LAST_RUN] = run(state_="Working", result="None", ended=False)
    entry = make_entry()
    await setup(hass, entry)

    assert state(hass, LAST) == (NOW - timedelta(minutes=5)).isoformat()
    assert state(hass, BUTTON) == STATE_UNAVAILABLE


async def test_the_button_starts_a_run(hass: HomeAssistant, server) -> None:  # noqa: F811
    server.responses[START] = SimpleNamespace(id="run-2", state="Starting")
    entry = make_entry()
    await setup(hass, entry)
    polls = len(calls(server, PRACTICES))

    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await hass.async_block_till_done()

    assert calls(server, START) == [{}]
    assert len(calls(server, PRACTICES)) > polls, "refreshed afterwards"


async def test_a_refused_start_raises(hass: HomeAssistant, server) -> None:  # noqa: F811
    server.responses[START] = error(403, "Access denied")
    entry = make_entry()
    await setup(hass, entry)

    with pytest.raises(HomeAssistantError, match="Access denied"):
        await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)


async def test_a_new_violation_fires_once_and_a_rerun_does_not_refire(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    fired = async_capture_events(hass, EVENT_BEST_PRACTICE_VIOLATION)
    server.responses[PRACTICES] = practices(
        practice(IMMUTABLE, "Immutable", "Violation"), practice(MFA, "MFA", "OK")
    )
    entry = make_entry()
    await setup(hass, entry)
    assert fired == [], "violations already there at startup are not announced"

    server.responses[PRACTICES] = practices(
        practice(IMMUTABLE, "Immutable", "Violation"), practice(MFA, "MFA", "Violation")
    )
    await refresh(hass, entry)
    assert [event.data["id"] for event in fired] == [MFA]
    assert fired[0].data == {"entry_id": entry.entry_id, "id": MFA, "name": "MFA", "note": None}

    # A new run: every check reads Analyzing, then the same violations come back
    server.responses[PRACTICES] = practices(
        practice(IMMUTABLE, "Immutable", "Analyzing"), practice(MFA, "MFA", "Analyzing")
    )
    await refresh(hass, entry)
    server.responses[PRACTICES] = practices(
        practice(IMMUTABLE, "Immutable", "Violation"), practice(MFA, "MFA", "Violation")
    )
    await refresh(hass, entry)
    assert len(fired) == 1

    # Fixed, then broken again: that is new
    server.responses[PRACTICES] = practices(practice(MFA, "MFA", "OK"))
    await refresh(hass, entry)
    server.responses[PRACTICES] = practices(practice(MFA, "MFA", "Violation"))
    await refresh(hass, entry)
    assert [event.data["id"] for event in fired] == [MFA, MFA]


async def test_an_account_without_the_role_is_not_asked_again(
    hass: HomeAssistant, server, caplog  # noqa: F811
) -> None:
    server.responses[PRACTICES] = error(403, "Access denied")
    entry = make_entry()
    with caplog.at_level(logging.INFO, logger="custom_components.veeam_br"):
        await setup(hass, entry)
        await refresh(hass, entry)

    assert len(calls(server, PRACTICES)) == 1
    assert calls(server, LAST_RUN) == []
    for entity_id in (VIOLATIONS, LAST, PROBLEM, BUTTON):
        assert state(hass, entity_id) is None, entity_id
    assert state(hass, HEALTH_OK) == STATE_ON
    assert "Security Administrator" in caplog.text
    assert "Failed to fetch" not in caplog.text


@pytest.mark.parametrize(
    "failure", ["practices", "last_run"], ids=["best practices fail", "last run fails"]
)
async def test_a_failure_leaves_the_analyzer_entities_unavailable(
    hass: HomeAssistant, server, failure: str  # noqa: F811
) -> None:
    entry = make_entry()
    await setup(hass, entry)

    key = PRACTICES if failure == "practices" else LAST_RUN
    server.responses[key] = error(500, "internal error", "UnknownError")
    await refresh(hass, entry)

    for entity_id in (VIOLATIONS, LAST, PROBLEM, BUTTON):
        assert state(hass, entity_id) == STATE_UNAVAILABLE, entity_id
    assert state(hass, HEALTH_OK) != STATE_ON
    assert state(hass, "sensor.vbr_security_vbr01_malware_events_24h") == "0", "malware unaffected"


@pytest.mark.parametrize("api_version", ["1.2-rev1", "1.3-rev0"])
async def test_every_revision_has_it(
    hass: HomeAssistant, server, api_version: str  # noqa: F811
) -> None:
    server.responses[PRACTICES] = practices(practice(IMMUTABLE, "Immutable", "Violation"))
    entry = make_entry(api_version=api_version)
    await setup(hass, entry)

    assert state(hass, VIOLATIONS) == "1"
    assert state(hass, BUTTON) is not None
