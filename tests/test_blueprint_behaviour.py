"""Behavioural tests: the shipped blueprints running as automations in Home Assistant.

test_blueprints.py checks the files' structure; these load each blueprint through Home
Assistant's own blueprint machinery, drive it with the events and state changes the
integration produces, and check what the notification action receives — or that it is not
run at all.
"""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_mock_service,
)

from custom_components.veeam_br.const import DOMAIN  # noqa: E402
from custom_components.veeam_br.malware import EVENT_MALWARE  # noqa: E402
from custom_components.veeam_br.move_copy import EVENT_MOVE_COPY  # noqa: E402
from custom_components.veeam_br.recovery_appliances import EVENT_RECOVERY_APPLIANCE  # noqa: E402
from custom_components.veeam_br.security_analyzer import (  # noqa: E402
    EVENT_BEST_PRACTICE_VIOLATION,
)

BLUEPRINT_DIR = Path(__file__).parent.parent / "blueprints" / "automation" / "veeam_br"

NOTIFY = [{"action": "test.notify", "data": {"title": "{{ title }}", "message": "{{ message }}"}}]
RECOVER = [{"action": "test.recover", "data": {"title": "{{ title }}", "message": "{{ message }}"}}]

ENTRY_ID = "entry-vbr01"
OTHER_ENTRY_ID = "entry-vbr02"


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry to hang devices on, and to give events a server name. Never set up."""
    entry = MockConfigEntry(domain=DOMAIN, title="vbr01.example.com", entry_id=ENTRY_ID)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def notify_calls(hass: HomeAssistant):
    return async_mock_service(hass, "test", "notify")


@pytest.fixture
def recover_calls(hass: HomeAssistant):
    return async_mock_service(hass, "test", "recover")


async def use_blueprint(hass: HomeAssistant, tmp_path: Path, name: str, inputs: dict) -> None:
    """Install one shipped blueprint and create an automation from it."""
    hass.config.config_dir = str(tmp_path)
    folder = tmp_path / "blueprints" / "automation" / "veeam_br"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT_DIR / name, folder / name)

    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"use_blueprint": {"path": f"veeam_br/{name}", "input": inputs}}]},
    )
    await hass.async_block_till_done()
    # A blueprint that fails to load leaves no automation behind rather than raising
    assert hass.states.async_entity_ids("automation"), f"{name} did not load"


def add_entity(
    hass: HomeAssistant, entry: MockConfigEntry, domain: str, key: str, device: str
) -> str:
    """Register an entity on a named device, as the integration would, and return its ID."""
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, device)}, name=device
    )
    entity = er.async_get(hass).async_get_or_create(
        domain, DOMAIN, f"{device}_{key}", config_entry=entry, device_id=device_entry.id
    )
    return entity.entity_id


async def set_state(hass: HomeAssistant, entity_id: str, state: str, **attributes) -> None:
    hass.states.async_set(entity_id, state, attributes)
    await hass.async_block_till_done()


async def fire(hass: HomeAssistant, event_type: str, data: dict) -> None:
    hass.bus.async_fire(event_type, data)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Malware detected
# ---------------------------------------------------------------------------


def malware_event(**overrides) -> dict:
    return {
        "entry_id": ENTRY_ID,
        "id": "0f7d9c1e-0000-0000-0000-000000000001",
        "type": "RansomwareNotes",
        "severity": "Infected",
        "state": "Created",
        "source": "InternalVeeamDetector",
        "machine": "fileserver01",
        "backup_object_id": "0f7d9c1e-0000-0000-0000-0000000000aa",
        "detection_time": "2026-09-28T10:15:00+00:00",
        "creation_time": "2026-09-28T10:16:00+00:00",
        "details": "Ransomware notes found",
        "engine": "Inline scan",
        "created_by": None,
        **overrides,
    }


async def test_malware_infected_event_notifies(hass, tmp_path, entry, notify_calls):
    await use_blueprint(hass, tmp_path, "malware_detected.yaml", {"notification_action": NOTIFY})

    await fire(hass, EVENT_MALWARE, malware_event())

    assert len(notify_calls) == 1
    data = notify_calls[0].data
    assert data["title"] == "Veeam: malware detected on fileserver01"
    assert data["message"].startswith("Ransomware Notes on fileserver01, marked Infected")
    assert "by Inline scan" in data["message"]
    assert "Ransomware notes found" in data["message"]
    assert "Server: vbr01.example.com." in data["message"]


async def test_malware_suspicious_event_reads_differently(hass, tmp_path, entry, notify_calls):
    await use_blueprint(hass, tmp_path, "malware_detected.yaml", {"notification_action": NOTIFY})

    await fire(
        hass,
        EVENT_MALWARE,
        malware_event(severity="Suspicious", type="EncryptedData", engine=None, details=None),
    )

    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Veeam: suspicious activity on fileserver01"
    assert (
        notify_calls[0]
        .data["message"]
        .startswith("Encrypted Data on fileserver01, marked Suspicious at ")
    )


async def test_malware_only_the_chosen_severities_notify(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass,
        tmp_path,
        "malware_detected.yaml",
        {"notification_action": NOTIFY, "severities": ["Infected"]},
    )

    await fire(hass, EVENT_MALWARE, malware_event(severity="Suspicious"))
    await fire(hass, EVENT_MALWARE, malware_event(severity="Clean"))
    assert not notify_calls

    # Compared case-insensitively
    await fire(hass, EVENT_MALWARE, malware_event(severity="infected"))
    assert len(notify_calls) == 1


async def test_malware_default_skips_clean_and_informative(hass, tmp_path, entry, notify_calls):
    await use_blueprint(hass, tmp_path, "malware_detected.yaml", {"notification_action": NOTIFY})

    await fire(hass, EVENT_MALWARE, malware_event(severity="Clean", source="MarkAsCleanEvent"))
    await fire(hass, EVENT_MALWARE, malware_event(severity="Informative"))
    await fire(hass, EVENT_MALWARE, malware_event(severity=None))

    assert not notify_calls


@pytest.mark.parametrize(("ignore", "expected"), [(True, 0), (False, 1)])
async def test_malware_false_positives(hass, tmp_path, entry, notify_calls, ignore, expected):
    await use_blueprint(
        hass,
        tmp_path,
        "malware_detected.yaml",
        {"notification_action": NOTIFY, "ignore_false_positives": ignore},
    )

    await fire(hass, EVENT_MALWARE, malware_event(state="FalsePositive"))

    assert len(notify_calls) == expected


async def test_malware_limited_to_one_server(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass,
        tmp_path,
        "malware_detected.yaml",
        {"notification_action": NOTIFY, "veeam_server": ENTRY_ID},
    )

    await fire(hass, EVENT_MALWARE, malware_event(entry_id=OTHER_ENTRY_ID))
    assert not notify_calls

    await fire(hass, EVENT_MALWARE, malware_event())
    assert len(notify_calls) == 1


async def test_malware_event_from_an_unknown_server_still_reads(hass, tmp_path, notify_calls):
    """No config entry to name the server: the message just leaves it out."""
    await use_blueprint(hass, tmp_path, "malware_detected.yaml", {"notification_action": NOTIFY})

    await fire(hass, EVENT_MALWARE, malware_event(entry_id="gone", machine=None))

    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Veeam: malware detected on an unknown machine"
    assert "Server:" not in notify_calls[0].data["message"]


# ---------------------------------------------------------------------------
# Best practice violation
# ---------------------------------------------------------------------------


async def test_best_practice_violation_notifies(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass, tmp_path, "best_practice_violation.yaml", {"notification_action": NOTIFY}
    )

    await fire(
        hass,
        EVENT_BEST_PRACTICE_VIOLATION,
        {
            "entry_id": ENTRY_ID,
            "id": "bp-1",
            "name": "MFA is enabled for all users",
            "note": "2 users without MFA",
        },
    )

    assert len(notify_calls) == 1
    data = notify_calls[0].data
    assert data["title"] == "Veeam best practice violated: MFA is enabled for all users"
    assert data["message"] == (
        "MFA is enabled for all users is now in violation on vbr01.example.com. "
        "2 users without MFA"
    )


async def test_best_practice_violation_limited_to_one_server(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass,
        tmp_path,
        "best_practice_violation.yaml",
        {"notification_action": NOTIFY, "veeam_server": ENTRY_ID},
    )

    await fire(
        hass,
        EVENT_BEST_PRACTICE_VIOLATION,
        {"entry_id": OTHER_ENTRY_ID, "id": "bp-1", "name": "Immutability", "note": None},
    )

    assert not notify_calls


# ---------------------------------------------------------------------------
# Recovery appliance connected
# ---------------------------------------------------------------------------


def appliance_event(**overrides) -> dict:
    return {
        "entry_id": ENTRY_ID,
        "id": "appliance-1",
        "host": "WIN-RECOVERY",
        "connected": True,
        "verified": False,
        "endpoint": "10.0.0.50:10006",
        "addresses": ["10.0.0.50"],
        "version": "13.0.1.100",
        "platform": "Windows",
        "connected_since": "2026-09-28T10:00:00+00:00",
        "last_contact": "2026-09-28T10:01:00+00:00",
        **overrides,
    }


async def test_recovery_appliance_connected_notifies(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass, tmp_path, "recovery_appliance_connected.yaml", {"notification_action": NOTIFY}
    )

    await fire(hass, EVENT_RECOVERY_APPLIANCE, appliance_event())

    assert len(notify_calls) == 1
    data = notify_calls[0].data
    assert data["title"] == "Veeam: recovery appliance connected: WIN-RECOVERY"
    assert data["message"].startswith(
        "WIN-RECOVERY (10.0.0.50:10006) connected to vbr01.example.com from Veeam Recovery Media."
    )
    assert "not verified yet" in data["message"]


@pytest.mark.parametrize(("verified", "text"), [(True, "It is verified."), (None, None)])
async def test_recovery_appliance_verification(hass, tmp_path, entry, notify_calls, verified, text):
    await use_blueprint(
        hass, tmp_path, "recovery_appliance_connected.yaml", {"notification_action": NOTIFY}
    )

    await fire(hass, EVENT_RECOVERY_APPLIANCE, appliance_event(verified=verified, endpoint=None))

    message = notify_calls[0].data["message"]
    assert message.startswith("WIN-RECOVERY (10.0.0.50) connected")
    if text:
        assert message.endswith(text)
    else:
        assert "verified" not in message


async def test_recovery_appliance_limited_to_one_server(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass,
        tmp_path,
        "recovery_appliance_connected.yaml",
        {"notification_action": NOTIFY, "veeam_server": ENTRY_ID},
    )

    await fire(hass, EVENT_RECOVERY_APPLIANCE, appliance_event(entry_id=OTHER_ENTRY_ID))

    assert not notify_calls


# ---------------------------------------------------------------------------
# Move/copy action required
# ---------------------------------------------------------------------------


def move_copy_event(**overrides) -> dict:
    return {
        "entry_id": ENTRY_ID,
        "id": "7c1f0a2b-3d4e-4f56-8a90-1b2c3d4e5f60",
        "name": "Move backup",
        "type": "BackupMove",
        "job_id": "job-1",
        "job_name": "Nightly VMs",
        "created": "2026-09-28T09:00:00+00:00",
        "progress": 40,
        "result": "Failed",
        "message": "Target repository is out of space",
        "platform": "VMware",
        **overrides,
    }


async def test_move_copy_notifies_with_what_the_action_needs(hass, tmp_path, entry, notify_calls):
    action = [
        {
            "action": "test.notify",
            "data": {
                "title": "{{ title }}",
                "message": "{{ message }}",
                "config_entry_id": "{{ entry_id }}",
                "session_id": "{{ session_id }}",
            },
        }
    ]
    await use_blueprint(
        hass, tmp_path, "move_copy_action_required.yaml", {"notification_action": action}
    )

    await fire(hass, EVENT_MOVE_COPY, move_copy_event())

    assert len(notify_calls) == 1
    data = notify_calls[0].data
    assert data["title"] == "Veeam move/copy needs a decision: Nightly VMs"
    assert data["message"] == (
        "Move backup (Nightly VMs) stopped at 40% on vbr01.example.com and is waiting: retry "
        "it, detach what failed, or stop and undo it. Veeam says: Target repository is out of "
        "space"
    )
    assert data["config_entry_id"] == ENTRY_ID
    assert data["session_id"] == "7c1f0a2b-3d4e-4f56-8a90-1b2c3d4e5f60"


async def test_move_copy_without_optional_fields(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass, tmp_path, "move_copy_action_required.yaml", {"notification_action": NOTIFY}
    )

    await fire(hass, EVENT_MOVE_COPY, move_copy_event(job_name=None, progress=None, message=None))

    data = notify_calls[0].data
    assert data["title"] == "Veeam move/copy needs a decision: Move backup"
    assert data["message"].startswith("Move backup stopped on vbr01.example.com and is waiting")
    assert "Veeam says" not in data["message"]


async def test_move_copy_limited_to_one_server(hass, tmp_path, entry, notify_calls):
    await use_blueprint(
        hass,
        tmp_path,
        "move_copy_action_required.yaml",
        {"notification_action": NOTIFY, "veeam_server": ENTRY_ID},
    )

    await fire(hass, EVENT_MOVE_COPY, move_copy_event(entry_id=OTHER_ENTRY_ID))

    assert not notify_calls


# ---------------------------------------------------------------------------
# State-based blueprints: unavailable is not a transition, and device names read well
# ---------------------------------------------------------------------------


async def test_job_failed_notifies_with_the_prefix_dropped(hass, tmp_path, entry, notify_calls):
    sensor = add_entity(hass, entry, "sensor", "last_result", "VBR Job Nightly VMs")
    await set_state(hass, sensor, "success")
    await use_blueprint(
        hass,
        tmp_path,
        "job_failed.yaml",
        {"notification_action": NOTIFY, "job_result_sensors": [sensor]},
    )

    await set_state(hass, sensor, "failed")

    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Veeam backup failed: Job Nightly VMs"
    assert notify_calls[0].data["message"] == "Job Nightly VMs finished with result Failed."


async def test_job_failed_warning_title(hass, tmp_path, entry, notify_calls):
    sensor = add_entity(hass, entry, "sensor", "last_result", "VBR Backup Job 1")
    await set_state(hass, sensor, "success")
    await use_blueprint(
        hass,
        tmp_path,
        "job_failed.yaml",
        {
            "notification_action": NOTIFY,
            "job_result_sensors": [sensor],
            "include_warnings": True,
        },
    )

    await set_state(hass, sensor, "warning")

    assert notify_calls[0].data["title"] == "Veeam backup warning: Backup Job 1"


async def test_job_failed_ignores_unavailable(hass, tmp_path, entry, notify_calls):
    sensor = add_entity(hass, entry, "sensor", "last_result", "VBR Job Nightly VMs")
    await set_state(hass, sensor, "failed")
    await use_blueprint(
        hass,
        tmp_path,
        "job_failed.yaml",
        {"notification_action": NOTIFY, "job_result_sensors": [sensor]},
    )

    # A failed fetch, and the same failure coming back
    await set_state(hass, sensor, "unavailable")
    await set_state(hass, sensor, "failed")
    # After a restart
    await set_state(hass, sensor, "unknown")
    await set_state(hass, sensor, "failed")
    # An attribute-only update
    await set_state(hass, sensor, "failed", raw_value="Failed")

    assert not notify_calls


async def test_job_failed_keeps_a_user_given_name(hass, tmp_path, entry, notify_calls):
    sensor = add_entity(hass, entry, "sensor", "last_result", "VBR Job Nightly VMs")
    device_id = er.async_get(hass).async_get(sensor).device_id
    dr.async_get(hass).async_update_device(device_id, name_by_user="VBR nightly")
    await set_state(hass, sensor, "success")
    await use_blueprint(
        hass,
        tmp_path,
        "job_failed.yaml",
        {"notification_action": NOTIFY, "job_result_sensors": [sensor]},
    )

    await set_state(hass, sensor, "failed")

    assert notify_calls[0].data["title"] == "Veeam backup failed: VBR nightly"


async def test_job_failed_with_an_old_unprefixed_name(hass, tmp_path, entry, notify_calls):
    sensor = add_entity(hass, entry, "sensor", "last_result", "Nightly VMs")
    await set_state(hass, sensor, "success")
    await use_blueprint(
        hass,
        tmp_path,
        "job_failed.yaml",
        {"notification_action": NOTIFY, "job_result_sensors": [sensor]},
    )

    await set_state(hass, sensor, "failed")

    assert notify_calls[0].data["message"] == "Nightly VMs finished with result Failed."


async def test_proxy_offline_and_back(hass, tmp_path, entry, notify_calls, recover_calls):
    online = add_entity(hass, entry, "binary_sensor", "online", "VBR VMware Backup Proxy")
    await set_state(hass, online, "on")
    await use_blueprint(
        hass,
        tmp_path,
        "proxy_offline.yaml",
        {
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
            "proxy_online_sensors": [online],
            "offline_for": {"seconds": 0},
        },
    )

    await set_state(hass, online, "off")
    await set_state(hass, online, "on")

    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Veeam: VMware Backup Proxy is offline"
    assert len(recover_calls) == 1
    assert recover_calls[0].data["message"] == "VMware Backup Proxy is online again."


async def test_proxy_offline_ignores_unavailable(
    hass, tmp_path, entry, notify_calls, recover_calls
):
    online = add_entity(hass, entry, "binary_sensor", "online", "VBR Proxy proxy01")
    await set_state(hass, online, "off")
    await use_blueprint(
        hass,
        tmp_path,
        "proxy_offline.yaml",
        {
            "notification_action": NOTIFY,
            "recovery_action": RECOVER,
            "proxy_online_sensors": [online],
            "offline_for": {"seconds": 0},
        },
    )

    await set_state(hass, online, "unavailable")
    await set_state(hass, online, "off")
    await set_state(hass, online, "unavailable")
    await set_state(hass, online, "on")
    await set_state(hass, online, "unavailable")
    await set_state(hass, online, "on")

    assert not notify_calls
    assert not recover_calls


async def test_repository_space_low_ignores_unavailable(hass, tmp_path, entry, notify_calls):
    used = add_entity(hass, entry, "sensor", "used_percentage", "VBR Default Backup Repository")
    await set_state(hass, used, "50")
    await use_blueprint(
        hass,
        tmp_path,
        "repository_space_low.yaml",
        {
            "notification_action": NOTIFY,
            "repository_sensors": [used],
            "sustained_for": {"seconds": 0},
        },
    )

    await set_state(hass, used, "unavailable")
    await set_state(hass, used, "90")
    assert not notify_calls

    await set_state(hass, used, "50")
    await set_state(hass, used, "91")
    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Veeam: Default Backup Repository is low on space"


async def test_ha_cluster_failover_ignores_unavailable(hass, tmp_path, entry, notify_calls):
    failover = add_entity(
        hass, entry, "binary_sensor", "failover_in_progress", "VBR HA Cluster vbr-ha"
    )
    await set_state(hass, failover, "off")
    await use_blueprint(
        hass,
        tmp_path,
        "ha_cluster_failover.yaml",
        {"notification_action": NOTIFY, "failover_sensor": failover},
    )

    await set_state(hass, failover, "unavailable")
    await set_state(hass, failover, "on")
    assert not notify_calls

    await set_state(hass, failover, "off")
    await set_state(hass, failover, "on")
    assert len(notify_calls) == 1
    assert notify_calls[0].data["title"] == "Veeam: HA Cluster vbr-ha is failing over"


async def test_daily_summary_lists_jobs_with_the_prefix_dropped(
    hass, tmp_path, entry, notify_calls
):
    failed = add_entity(hass, entry, "sensor", "last_result", "VBR Job Nightly VMs")
    ok = add_entity(hass, entry, "sensor", "last_result", "VBR Job SQL")
    gone = add_entity(hass, entry, "sensor", "last_result", "VBR Job Old")
    await set_state(hass, failed, "failed")
    await set_state(hass, ok, "success")
    await set_state(hass, gone, "unavailable")
    await use_blueprint(
        hass,
        tmp_path,
        "daily_backup_summary.yaml",
        {"notification_action": NOTIFY, "job_result_sensors": [failed, ok, gone]},
    )

    # Run it now rather than waiting for the time trigger; turning it off afterwards cancels
    # that trigger's timer, which the test harness would otherwise report as lingering
    automation = {"entity_id": hass.states.async_entity_ids("automation")[0]}
    await hass.services.async_call("automation", "trigger", automation, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("automation", "turn_off", automation, blocking=True)

    assert len(notify_calls) == 1
    message = notify_calls[0].data["message"]
    assert "- Job Nightly VMs: failed" in message
    assert "1 job(s) reported no result, or could not be read." in message
