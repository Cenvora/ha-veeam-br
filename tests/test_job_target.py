"""A job's Target: targetName on 1.3-rev2, the backup repository before it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
pytest.importorskip("veeam_br.exceptions", reason="needs veeam-br 0.5.1")

from homeassistant.const import EntityCategory  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

from .test_behaviour import auto_enable_custom_integrations  # noqa: F401 - the fixture
from .test_behaviour import (  # noqa: E402
    job,
    make_entry,
    paged,
    setup,
)
from .test_behaviour import server  # noqa: F401 - the fixture

JOBS = ("jobs", "get_all_jobs_states")
TARGET = "sensor.vbr_job_nightly_vms_target"
REPO_ID = "88788f9e-d8f5-4eb4-bc4f-9b3f5403bcec"


def job_with(**fields):
    return SimpleNamespace(**{**vars(job()), **fields})


async def test_the_target_name_is_shown(hass: HomeAssistant, server) -> None:  # noqa: F811
    server.responses[JOBS] = paged(
        [
            job_with(
                target_name="esx02.lab.local",
                repository_name="Default Backup Repository",
                repository_id=REPO_ID,
            )
        ]
    )
    entry = make_entry()
    await setup(hass, entry)

    target = hass.states.get(TARGET)
    assert target.state == "esx02.lab.local"
    assert target.attributes["target_source"] == "target"
    assert target.attributes["repository_name"] == "Default Backup Repository"
    assert target.attributes["repository_id"] == REPO_ID
    assert er.async_get(hass).async_get(TARGET).entity_category is EntityCategory.DIAGNOSTIC


@pytest.mark.parametrize("api_version", ["1.3-rev2", "1.3-rev1"])
async def test_without_a_target_name_the_repository_is_shown(
    hass: HomeAssistant, server, api_version: str  # noqa: F811
) -> None:
    # 1.3-rev1 has no targetName; a 1.3-rev2 job may not report one either
    server.responses[JOBS] = paged(
        [job_with(repository_name="Default Backup Repository", repository_id=REPO_ID)]
    )
    entry = make_entry(api_version=api_version)
    await setup(hass, entry)

    target = hass.states.get(TARGET)
    assert target.state == "Default Backup Repository"
    assert target.attributes["target_source"] == "repository"


@pytest.mark.parametrize("empty", [None, ""])
async def test_no_target_at_all_is_unknown(
    hass: HomeAssistant, server, empty  # noqa: F811
) -> None:
    server.responses[JOBS] = paged([job_with(target_name=empty, repository_name=empty)])
    entry = make_entry()
    await setup(hass, entry)

    target = hass.states.get(TARGET)
    assert target.state == "unknown"
    assert target.attributes["target_source"] is None
