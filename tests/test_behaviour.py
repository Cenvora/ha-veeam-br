"""Behavioural tests: the integration running in Home Assistant against a fake VeeamClient.

Most of the older tests read the source; these run it. The fake stands in for veeam-br's
VeeamClient only — the coordinator, the platforms, the pruning and the config entry
lifecycle are the real ones — and answers each operation from a table the test controls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
# veeam-br 0.5.1 (the manifest floor) adds veeam_br.exceptions. Until it is on PyPI, CI may
# install 0.5.0, which the integration itself does not support; skip rather than error.
pytest.importorskip("veeam_br.exceptions", reason="needs veeam-br 0.5.1")

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState  # noqa: E402
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402
from veeam_br.exceptions import VeeamAuthenticationError, VeeamSessionError  # noqa: E402

from custom_components.veeam_br.api_version import ServerUnreachableError  # noqa: E402
from custom_components.veeam_br.const import DOMAIN  # noqa: E402

HOST = "vbr01.example.com"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/veeam_br."""
    yield


# ---------------------------------------------------------------------------
# The fake server
# ---------------------------------------------------------------------------


def error(status: int, message: str = "refused", code: str = "AccessDenied"):
    """An Error model, as the generated operations return for a documented failure."""
    return SimpleNamespace(
        error_code=code, message=message, additional_properties={"status": status}
    )


def paged(items: list, cap: int | None = None):
    """A collection endpoint honouring skip/limit and reporting pagination.total.

    `cap` is a server answering with fewer than asked for, whatever the limit.
    """

    def answer(kwargs):
        skip = kwargs.get("skip") or 0
        limit = kwargs.get("limit") or 200
        if cap is not None:
            limit = min(limit, cap)
        return SimpleNamespace(
            data=items[skip : skip + limit], pagination=SimpleNamespace(total=len(items))
        )

    return answer


def job(job_id="job-1", name="Nightly VMs"):
    return SimpleNamespace(
        id=job_id,
        name=name,
        type_="Backup",
        status="Stopped",
        last_result="Success",
        last_run=None,
        next_run=None,
    )


def repository(repo_id, name, type_="LinuxLocal", extras=None):
    return SimpleNamespace(
        id=repo_id,
        name=name,
        description="",
        type_=type_,
        unique_id=None,
        additional_properties=extras or {},
    )


def repository_state(repo_id, capacity=200.0, free=180.0, used=20.0):
    return SimpleNamespace(
        id=repo_id,
        capacity_gb=capacity,
        free_gb=free,
        used_space_gb=used,
        is_online=True,
        is_out_of_date=False,
    )


class FakeServer:
    """Answers every operation from `responses`, keyed by (namespace, operation)."""

    def __init__(self) -> None:
        self.connect_error: BaseException | None = None
        self.calls: list[tuple[str, str, dict]] = []
        self.responses: dict[tuple[str, str], Any] = {
            ("jobs", "get_all_jobs_states"): paged([job()]),
            (
                "service",
                "get_server_info",
            ): SimpleNamespace(
                vbr_id="vbr-id",
                name="vbr01",
                build_version="13.0.1",
                patches=[],
                database_vendor="PostgreSql",
                sql_server_edition="",
                sql_server_version="",
                database_schema_version="",
                database_content_version="",
                platform="Linux",
            ),
            ("license_", "get_installed_license"): SimpleNamespace(
                status="Valid",
                edition="EnterprisePlus",
                type_="Perpetual",
                licensed_to="Example",
                support_id="",
                auto_update_enabled=True,
                cloud_connect="Disabled",
                free_agent_instance_consumption_enabled=False,
            ),
            ("repositories", "get_all_repositories"): paged(
                [
                    repository("repo-1", "Default Backup Repository"),
                    repository("repo-2", "Temp Local Repository", type_="WinLocal"),
                ]
            ),
            ("repositories", "get_all_repositories_states"): paged(
                [repository_state("repo-1"), repository_state("repo-2")]
            ),
            ("repositories", "get_all_scale_out_repositories"): paged([]),
            ("proxies", "get_all_proxies_states"): paged([]),
            ("wan_accelerators", "get_all_wan_accelerators"): paged([]),
            ("high_availability_ha_cluster", "get_high_availability_cluster"): error(
                400, "HA cluster is not configured", "NotFound"
            ),
            ("malware_detection", "view_suspicious_activity_events"): paged([]),
            ("malware_detection", "get_malware_detection_objects"): paged([]),
        }

    def handle(self, namespace: str, operation: str, kwargs: dict) -> Any:
        self.calls.append((namespace, operation, kwargs))
        answer = self.responses.get((namespace, operation))
        if callable(answer):
            answer = answer(kwargs)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeApi:
    def __init__(self, server: FakeServer, namespace: str) -> None:
        self._server = server
        self._namespace = namespace

    def __getattr__(self, operation: str):
        async def call(client=None, **kwargs):
            return self._server.handle(self._namespace, operation, kwargs)

        return call


class FakeClient:
    """Stands in for veeam_br.client.VeeamClient."""

    server: FakeServer
    instances: list[FakeClient] = []

    def __init__(self, host, username, password, api_version, verify_ssl=True, timeout=30.0):
        self.host = host
        self.api_version = api_version
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.closed = False
        FakeClient.instances.append(self)

    async def connect(self) -> None:
        if self.server.connect_error is not None:
            raise self.server.connect_error

    async def close(self) -> None:
        self.closed = True

    def api(self, namespace: str) -> FakeApi:
        return FakeApi(self.server, namespace)

    async def call(self, fn, *args, **kwargs):
        kwargs.pop("x_api_version", None)
        return await fn(**kwargs)


@pytest.fixture
def server():
    """A fresh fake server, wired in as the client every entry gets."""
    fake = FakeServer()
    FakeClient.server = fake
    FakeClient.instances = []
    with patch("custom_components.veeam_br.VeeamClient", FakeClient):
        yield fake


def make_entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Veeam B&R ({HOST})",
        unique_id=f"{HOST}:{data.get("port", 443)}",
        data={
            "host": HOST,
            "port": 443,
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
            # Pinned, so setup does not probe the network for the version
            "api_version": "1.3-rev2",
            **data,
        },
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data["coordinator"].async_refresh()
    await hass.async_block_till_done()


def state(hass: HomeAssistant, entity_id: str) -> str | None:
    current = hass.states.get(entity_id)
    return current.state if current else None


REPO_ONLINE = "binary_sensor.vbr_default_backup_repository_online"
REPO_CAPACITY = "sensor.vbr_default_backup_repository_capacity"
REPO_TYPE = "sensor.vbr_default_backup_repository_type"
REPO_RESCAN = "button.vbr_default_backup_repository_rescan"
TEMP_CAPACITY = "sensor.vbr_temp_local_repository_capacity"
TEMP_TYPE = "sensor.vbr_temp_local_repository_type"
TEMP_IMMUTABLE = "binary_sensor.vbr_temp_local_repository_immutable"
JOB_START = "button.vbr_job_nightly_vms_start"
CONNECTED = "binary_sensor.vbr_server_vbr01_connected"
HEALTH_OK = "binary_sensor.vbr_server_vbr01_health_ok"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def test_setup_creates_prefixed_entities(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert state(hass, REPO_ONLINE) == STATE_ON
    assert state(hass, REPO_CAPACITY) == "200.0"
    assert state(hass, CONNECTED) == STATE_ON
    assert state(hass, HEALTH_OK) == STATE_ON
    assert hass.states.get("sensor.vbr_license_vbr01_status") is not None

    devices = {
        device.name
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    }
    assert "VBR Default Backup Repository" in devices
    assert "VBR Job Nightly VMs" in devices
    assert "VBR Server vbr01" in devices
    assert "VBR License vbr01" in devices


async def test_client_gets_a_timeout_and_a_prepared_ssl_context(
    hass: HomeAssistant, server
) -> None:
    """A hung server must not stall the integration, and no context is built on the loop."""
    await setup(hass, make_entry())

    client = FakeClient.instances[-1]
    assert client.timeout == 30.0
    assert not isinstance(client.verify_ssl, bool), "expected Home Assistant's SSL context"


async def test_refused_login_asks_for_new_credentials(hass: HomeAssistant, server) -> None:
    server.connect_error = VeeamAuthenticationError("Veeam login failed")
    entry = make_entry()
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)
    assert FakeClient.instances[-1].closed


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("All connection attempts failed"), TimeoutError(), OSError("no route")],
)
async def test_unreachable_server_is_retried(hass: HomeAssistant, server, failure) -> None:
    server.connect_error = failure
    entry = make_entry()
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert FakeClient.instances[-1].closed


async def test_undetectable_version_on_a_down_server_is_retried(
    hass: HomeAssistant, server
) -> None:
    """Falling back to a default while the server is down would pin the wrong revision."""
    entry = make_entry(api_version="auto")
    with patch(
        "custom_components.veeam_br.async_resolve_api_version",
        side_effect=ServerUnreachableError("nothing answered"),
    ):
        await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert not FakeClient.instances, "no client should be created before the version is known"


async def test_unknown_pinned_version_is_a_clear_setup_error(hass: HomeAssistant, server) -> None:
    entry = make_entry(api_version="9.9-rev9")
    await setup(hass, entry)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.reason and "9.9-rev9" in entry.reason


async def test_unload_closes_the_client(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)
    client = FakeClient.instances[-1]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert client.closed


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


async def test_a_failed_endpoint_keeps_its_entities(hass: HomeAssistant, server) -> None:
    """The reported bug: one failed fetch deleted every repository entity."""
    entry = make_entry()
    await setup(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get(REPO_ONLINE)

    server.responses[("repositories", "get_all_repositories")] = httpx.ReadTimeout("")
    await refresh(hass, entry)

    assert registry.async_get(REPO_ONLINE), "a failed fetch must not delete entities"
    assert registry.async_get(REPO_RESCAN)
    assert state(hass, REPO_ONLINE) == STATE_UNAVAILABLE
    assert state(hass, HEALTH_OK) == STATE_OFF, "health reflects the failed endpoint"
    assert state(hass, CONNECTED) == STATE_ON, "the server itself still answered"

    server.responses[("repositories", "get_all_repositories")] = paged(
        [repository("repo-1", "Default Backup Repository")]
    )
    await refresh(hass, entry)
    assert state(hass, REPO_ONLINE) == STATE_ON
    assert state(hass, HEALTH_OK) == STATE_ON


async def test_an_error_model_is_a_failure_not_an_empty_list(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)

    server.responses[("repositories", "get_all_repositories")] = error(500, "internal error")
    await refresh(hass, entry)

    assert er.async_get(hass).async_get(REPO_ONLINE)
    assert entry.runtime_data["coordinator"].data["fetch_ok"]["repositories"] is False


async def test_an_empty_collection_prunes_nothing(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)

    server.responses[("repositories", "get_all_repositories")] = paged([])
    await refresh(hass, entry)

    assert er.async_get(hass).async_get(REPO_ONLINE), "empty looks exactly like an outage"


async def test_a_deleted_repository_is_pruned_and_can_come_back(
    hass: HomeAssistant, server
) -> None:
    entry = make_entry()
    await setup(hass, entry)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)

    server.responses[("repositories", "get_all_repositories")] = paged(
        [repository("repo-2", "Temp Local Repository", type_="WinLocal")]
    )
    await refresh(hass, entry)

    assert registry.async_get(REPO_ONLINE) is None
    assert registry.async_get(REPO_RESCAN) is None
    assert not [
        device
        for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
        if (DOMAIN, "repository_repo-1") in device.identifiers
    ]
    assert registry.async_get(TEMP_TYPE), "the other repository is untouched"

    server.responses[("repositories", "get_all_repositories")] = paged(
        [
            repository("repo-1", "Default Backup Repository"),
            repository("repo-2", "Temp Local Repository", type_="WinLocal"),
        ]
    )
    await refresh(hass, entry)

    # Every platform re-adds it, the binary sensors included
    assert state(hass, REPO_ONLINE) == STATE_ON
    assert state(hass, REPO_CAPACITY) == "200.0"
    assert hass.states.get(REPO_RESCAN) is not None


async def test_a_repository_without_state_is_unavailable_not_unknown(
    hass: HomeAssistant, server, caplog
) -> None:
    """Temp Local Repository: listed, but missing from the states response."""
    server.responses[("repositories", "get_all_repositories_states")] = paged(
        [repository_state("repo-1")]
    )
    entry = make_entry()
    await setup(hass, entry)

    assert state(hass, TEMP_CAPACITY) == STATE_UNAVAILABLE
    assert state(hass, TEMP_TYPE) == "Windows (local)", "configuration fields still show"
    assert state(hass, TEMP_IMMUTABLE) == STATE_OFF, "Windows repositories cannot be immutable"
    assert "Temp Local Repository" in caplog.text and "not in the repository states" in caplog.text

    caplog.clear()
    await refresh(hass, entry)
    assert "not in the repository states" not in caplog.text, "reported once, not every poll"


async def test_collections_are_paged_to_the_end(hass: HomeAssistant, server) -> None:
    jobs = [job(f"job-{index}", f"Job {index}") for index in range(450)]
    # Asked for 10,000 at a time, the server answers 200: still paged to the total
    server.responses[("jobs", "get_all_jobs_states")] = paged(jobs, cap=200)
    entry = make_entry()
    await setup(hass, entry)

    assert len(entry.runtime_data["coordinator"].data["jobs"]) == 450
    calls = [call[2] for call in server.calls if call[1] == "get_all_jobs_states"]
    assert [call["skip"] for call in calls[:3]] == [0, 200, 400]
    assert {call["limit"] for call in calls} == {10000}


async def test_a_collection_fits_one_request(hass: HomeAssistant, server) -> None:
    jobs = [job(f"job-{index}", f"Job {index}") for index in range(450)]
    server.responses[("jobs", "get_all_jobs_states")] = paged(jobs)
    entry = make_entry()
    await setup(hass, entry)

    calls = [call[2] for call in server.calls if call[1] == "get_all_jobs_states"]
    assert len(calls) == 1
    assert len(entry.runtime_data["coordinator"].data["jobs"]) == 450


async def test_a_rejected_session_is_retried_once(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)
    answers = [VeeamSessionError("undecodable"), paged([job()])]

    def once(kwargs):
        answer = answers.pop(0) if answers else paged([job()])
        return answer(kwargs) if callable(answer) else answer

    server.responses[("jobs", "get_all_jobs_states")] = once
    await refresh(hass, entry)

    assert entry.runtime_data["coordinator"].last_update_success


async def test_refused_credentials_mid_poll_start_reauth(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)

    server.responses[("jobs", "get_all_jobs_states")] = VeeamAuthenticationError("refused")
    await refresh(hass, entry)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_connected_reads_off_when_nothing_answers(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)

    unreachable = httpx.ConnectError("All connection attempts failed")
    for key in list(server.responses):
        server.responses[key] = unreachable
    await refresh(hass, entry)

    assert not entry.runtime_data["coordinator"].last_update_success
    assert state(hass, CONNECTED) == STATE_OFF, "must read off, not unavailable"
    assert state(hass, HEALTH_OK) == STATE_OFF
    assert er.async_get(hass).async_get(REPO_ONLINE), "an outage deletes nothing"


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


async def test_a_refused_button_press_raises(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)
    server.responses[("jobs", "start_job")] = error(400, "The job is already running")

    with pytest.raises(HomeAssistantError, match="already running"):
        await hass.services.async_call("button", "press", {"entity_id": JOB_START}, blocking=True)


async def test_a_failed_button_press_raises(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)
    server.responses[("repositories", "rescan_repositories")] = httpx.ReadTimeout("")

    with pytest.raises(HomeAssistantError, match="ReadTimeout"):
        await hass.services.async_call("button", "press", {"entity_id": REPO_RESCAN}, blocking=True)


async def test_a_successful_button_press_calls_the_operation(hass: HomeAssistant, server) -> None:
    entry = make_entry()
    await setup(hass, entry)
    server.responses[("jobs", "start_job")] = SimpleNamespace(id="session-1")

    await hass.services.async_call("button", "press", {"entity_id": JOB_START}, blocking=True)

    started = [call for call in server.calls if call[1] == "start_job"]
    assert started and started[0][2]["id"] == "job-1"


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------


async def test_the_same_server_on_another_port_is_refused(hass: HomeAssistant, server) -> None:
    """13.1 answers on 443 and 9419; adding both duplicated every entity with _2."""
    make_entry().add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "host": HOST.upper(),
            "port": 9419,
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
            "api_version": "1.3-rev2",
        },
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_reconfigure_moves_the_unique_id(hass: HomeAssistant, server) -> None:
    entry = make_entry(port=9419)
    await setup(hass, entry)

    with patch("custom_components.veeam_br.config_flow.validate_input", return_value={}):
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "host": HOST,
                "port": 443,
                "username": "admin",
                "password": "secret",
                "verify_ssl": False,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == f"{HOST}:443"
    assert entry.data["port"] == 443
