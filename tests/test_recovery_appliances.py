"""Agent recovery appliances connected to the backup server (1.3-rev2).

Runs the integration against the fake server in test_behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
pytest.importorskip("veeam_br.exceptions", reason="needs veeam-br 0.5.1")

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import async_capture_events  # noqa: E402

from custom_components.veeam_br.recovery_appliances import (  # noqa: E402
    EVENT_RECOVERY_APPLIANCE,
)

from .test_behaviour import (  # noqa: E402
    HEALTH_OK,
    error,
    make_entry,
    paged,
    refresh,
    setup,
    state,
)
from .test_behaviour import auto_enable_custom_integrations  # noqa: F401 - the fixture
from .test_behaviour import server  # noqa: F401 - the fixture

APPLIANCES = ("agents", "get_agents_recovery_appliances")
SENSOR = "sensor.vbr_server_vbr01_recovery_appliances_connected"
NOW = datetime.now(timezone.utc)
# Another offset, so ordering cannot pass by comparing text
PLUS_TWO = timezone(timedelta(hours=2))


def appliance(
    appliance_id: str,
    host: str,
    detached: bool = False,
    verified: bool = True,
    contact: datetime = NOW,
):
    return SimpleNamespace(
        id=appliance_id,
        host_name=host,
        version="13.0.1.23",
        endpoint="172.1.1.1",
        addresses=["172.1.1.1", "172.1.1.2"],
        port=9123,
        creation_time=contact - timedelta(hours=1),
        is_verified=verified,
        is_detached=detached,
        can_expire=False,
        verification_phrase="ASD-123-DSA-321",
        used_personal_certificate=True,
        last_contact_time=contact,
        platform_id="00000000-0000-0000-0000-000000000000",
        platform_type="WindowsPhysical",
        operations=[],
    )


async def test_connected_appliances_are_counted_and_listed(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    server.responses[APPLIANCES] = paged(
        [
            appliance("a-gone", "old-laptop", detached=True, contact=NOW - timedelta(days=3)),
            # Later than a-recent, though its text sorts earlier
            appliance(
                "a-plus2", "fs01", contact=(NOW + timedelta(minutes=30)).astimezone(PLUS_TWO)
            ),
            appliance("a-recent", "db01", verified=False, contact=NOW),
        ]
    )
    entry = make_entry()
    await setup(hass, entry)

    current = hass.states.get(SENSOR)
    assert current.state == "2"
    assert current.attributes["disconnected"] == 1
    assert current.attributes["unverified"] == 1
    listed = current.attributes["appliances"]
    assert [a["id"] for a in listed] == ["a-plus2", "a-recent", "a-gone"]
    assert listed[1]["verified"] is False
    assert listed[0]["addresses"] == ["172.1.1.1", "172.1.1.2"]
    assert listed[0]["platform"] == "WindowsPhysical"
    assert isinstance(listed[0]["last_contact"], str)
    assert all("verification_phrase" not in a for a in listed), "kept out of history"
    assert "ASD-123-DSA-321" not in str(current.attributes)

    (request,) = [kw for ns, op, kw in server.calls if (ns, op) == APPLIANCES]
    assert request["limit"] == 10000


async def test_none_connected_reads_zero(hass: HomeAssistant, server) -> None:  # noqa: F811
    entry = make_entry()
    await setup(hass, entry)

    assert state(hass, SENSOR) == "0"


async def test_an_appliance_connecting_fires_once(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    fired = async_capture_events(hass, EVENT_RECOVERY_APPLIANCE)
    server.responses[APPLIANCES] = paged(
        [appliance("a-1", "fs01"), appliance("a-gone", "old", detached=True)]
    )
    entry = make_entry()
    await setup(hass, entry)
    assert fired == [], "appliances already connected at startup are not announced"

    server.responses[APPLIANCES] = paged(
        [
            appliance("a-1", "fs01"),
            appliance("a-gone", "old", detached=True),
            appliance("a-2", "db01", verified=False),
        ]
    )
    await refresh(hass, entry)
    await refresh(hass, entry)

    assert [event.data["id"] for event in fired] == ["a-2"]
    data = fired[0].data
    assert data["entry_id"] == entry.entry_id
    assert data["host"] == "db01"
    assert data["verified"] is False
    assert isinstance(data["connected_since"], str)


async def test_reconnecting_fires_again(hass: HomeAssistant, server) -> None:  # noqa: F811
    fired = async_capture_events(hass, EVENT_RECOVERY_APPLIANCE)
    server.responses[APPLIANCES] = paged([appliance("a-1", "fs01")])
    entry = make_entry()
    await setup(hass, entry)

    server.responses[APPLIANCES] = paged([appliance("a-1", "fs01", detached=True)])
    await refresh(hass, entry)
    server.responses[APPLIANCES] = paged([appliance("a-1", "fs01")])
    await refresh(hass, entry)

    assert [event.data["id"] for event in fired] == ["a-1"]


async def test_a_failure_leaves_the_sensor_unavailable(
    hass: HomeAssistant, server  # noqa: F811
) -> None:
    entry = make_entry()
    await setup(hass, entry)

    server.responses[APPLIANCES] = error(500, "internal error", "UnknownError")
    await refresh(hass, entry)

    assert state(hass, SENSOR) == STATE_UNAVAILABLE
    assert state(hass, HEALTH_OK) != STATE_ON
    assert er.async_get(hass).async_get(SENSOR), "a failure deletes nothing"


@pytest.mark.parametrize("api_version", ["1.2-rev1", "1.3-rev1"])
async def test_older_revisions_do_not_ask(
    hass: HomeAssistant, server, api_version: str  # noqa: F811
) -> None:
    entry = make_entry(api_version=api_version)
    await setup(hass, entry)

    assert [call for call in server.calls if call[:2] == APPLIANCES] == []
    assert state(hass, SENSOR) is None
    assert state(hass, HEALTH_OK) == STATE_ON
