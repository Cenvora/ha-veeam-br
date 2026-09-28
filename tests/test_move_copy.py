"""Move/copy backup sessions awaiting action, and the action that settles them (1.3-rev2).

Runs the integration against the fake server in test_behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace
from uuid import UUID

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
pytest.importorskip("veeam_br.exceptions", reason="needs veeam-br 0.5.1")

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import async_capture_events  # noqa: E402
import voluptuous as vol  # noqa: E402

from custom_components.veeam_br.const import DOMAIN  # noqa: E402
from custom_components.veeam_br.move_copy import EVENT_MOVE_COPY  # noqa: E402

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

SESSIONS = ("backups", "get_move_copy_sessions")
MANAGE = ("backups", "manage_move_copy_session")
SENSOR = "sensor.vbr_server_vbr01_move_copy_sessions_awaiting_action"
WAITING = "7c1f0a2b-3d4e-4f56-8a90-1b2c3d4e5f60"
LATER = "8d2a1b3c-4e5f-4061-9b01-2c3d4e5f6071"
NOW = datetime.now(timezone.utc)


def session(
    session_id: str, hours_ago: float = 1.0, job_id: str = "job-1", type_="PerVmMoveBackup"
):
    return SimpleNamespace(
        id=session_id,
        name="Move Backup",
        job_id=job_id,
        session_type=type_,
        creation_time=NOW - timedelta(hours=hours_ago),
        state="ActionRequired",
        progress_percent=65,
        result=SimpleNamespace(
            result="Warning",
            message="The session requires user action: retry, detach failed, or stop and undo.",
        ),
        platform_name="VMware",
    )


def calls(server, key) -> list[dict]:  # noqa: F811
    return [
        kwargs for namespace, operation, kwargs in server.calls if (namespace, operation) == key
    ]


async def manage(hass: HomeAssistant, entry, session_id: str = WAITING, action: str = "retry"):
    return await hass.services.async_call(
        DOMAIN,
        "manage_move_copy_session",
        {"config_entry_id": entry.entry_id, "session_id": session_id, "action": action},
        blocking=True,
        return_response=True,
    )


async def test_the_server_reports_sessions_awaiting_action(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    server.responses[SESSIONS] = [
        session(LATER, hours_ago=1, job_id="unknown-job", type_="PerVmCopyBackup"),
        session(WAITING, hours_ago=5),
    ]
    entry = make_entry()
    await setup(hass, entry)

    current = hass.states.get(SENSOR)
    assert current.state == "2"
    first, second = current.attributes["sessions"]
    assert first["id"] == WAITING, "the longest-waiting first"
    assert first["job_name"] == "Nightly VMs"
    assert first["type"] == "PerVmMoveBackup"
    assert first["progress"] == 65
    assert first["result"] == "Warning"
    assert first["created"] == (NOW - timedelta(hours=5)).isoformat()
    assert second["job_name"] is None
    assert calls(server, SESSIONS) == [{}], "a plain array: nothing to page"


async def test_nothing_waiting_reads_zero(hass: HomeAssistant, server) -> None:  # noqa: F811
    entry = make_entry()
    await setup(hass, entry)

    assert state(hass, SENSOR) == "0"
    assert hass.states.get(SENSOR).attributes["sessions"] == []


async def test_a_session_that_starts_waiting_fires_once(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    fired = async_capture_events(hass, EVENT_MOVE_COPY)
    server.responses[SESSIONS] = [session(WAITING, hours_ago=5)]
    entry = make_entry()
    await setup(hass, entry)
    assert fired == [], "sessions already waiting at startup are not announced"

    server.responses[SESSIONS] = [session(WAITING, hours_ago=5), session(LATER)]
    await refresh(hass, entry)
    await refresh(hass, entry)

    assert [event.data["id"] for event in fired] == [LATER]
    assert fired[0].data["entry_id"] == entry.entry_id
    assert fired[0].data["job_name"] == "Nightly VMs"


async def test_an_account_without_the_role_is_not_asked_again(
    hass: HomeAssistant, server, caplog  # noqa: F811
) -> None:
    server.responses[SESSIONS] = error(403, "Access denied")
    entry = make_entry()
    with caplog.at_level(logging.INFO, logger="custom_components.veeam_br"):
        await setup(hass, entry)
        await refresh(hass, entry)

    assert len(calls(server, SESSIONS)) == 1
    assert state(hass, SENSOR) is None, "no entity for what the account cannot see"
    assert state(hass, HEALTH_OK) == STATE_ON, "a refusal by design is not a failure"
    assert "Backup Administrator" in caplog.text
    assert "Failed to fetch" not in caplog.text


async def test_a_failure_leaves_the_sensor_unavailable(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    entry = make_entry()
    await setup(hass, entry)

    server.responses[SESSIONS] = error(500, "internal error", "UnknownError")
    await refresh(hass, entry)

    assert state(hass, SENSOR) == STATE_UNAVAILABLE
    assert state(hass, HEALTH_OK) != STATE_ON
    assert er.async_get(hass).async_get(SENSOR), "a failure deletes nothing"


@pytest.mark.parametrize("api_version", ["1.2-rev1", "1.3-rev1"])
async def test_older_revisions_neither_ask_nor_offer(
    hass: HomeAssistant, server, api_version: str  # noqa: F811
) -> None:
    entry = make_entry(api_version=api_version)
    await setup(hass, entry)

    assert calls(server, SESSIONS) == []
    assert state(hass, SENSOR) is None
    with pytest.raises(ServiceValidationError) as raised:
        await manage(hass, entry)
    assert raised.value.translation_key == "move_copy_unsupported"
    assert calls(server, MANAGE) == []


@pytest.mark.parametrize(
    ("action", "expected"),
    [("retry", "Retry"), ("forget_failed", "ForgetFailed"), ("stop_and_undo", "StopAndUndo")],
)
async def test_the_action_settles_a_session(
    hass: HomeAssistant, server, action: str, expected: str  # noqa: F811
) -> None:
    server.responses[SESSIONS] = [session(WAITING)]
    server.responses[MANAGE] = SimpleNamespace(id=LATER, state=SimpleNamespace(value="Starting"))
    entry = make_entry()
    await setup(hass, entry)
    polls = len(calls(server, SESSIONS))

    response = await manage(hass, entry, action=action)
    await hass.async_block_till_done()

    (request,) = calls(server, MANAGE)
    assert request["session_id"] == UUID(WAITING)
    assert request["body"].action.value == expected
    assert response == {"session_id": LATER, "state": "Starting"}
    assert len(calls(server, SESSIONS)) > polls, "refreshed afterwards"


async def test_a_refused_action_raises(hass: HomeAssistant, server) -> None:  # noqa: F811
    server.responses[MANAGE] = error(400, "The session is not awaiting action", "BadRequest")
    entry = make_entry()
    await setup(hass, entry)

    with pytest.raises(HomeAssistantError, match="not awaiting action") as raised:
        await manage(hass, entry)
    assert raised.value.translation_key == "action_rejected"


async def test_bad_input_is_refused_before_asking(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    entry = make_entry()
    await setup(hass, entry)

    with pytest.raises(ServiceValidationError) as raised:
        await manage(hass, entry, session_id="not-a-uuid")
    assert raised.value.translation_key == "invalid_session_id"

    with pytest.raises(vol.Invalid):
        await manage(hass, entry, action="delete_everything")

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            "manage_move_copy_session",
            {"config_entry_id": "missing", "session_id": WAITING, "action": "retry"},
            blocking=True,
        )
    assert raised.value.translation_key == "entry_not_found"

    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as raised:
        await manage(hass, entry)
    assert raised.value.translation_key == "entry_not_loaded"
    assert calls(server, MANAGE) == []
