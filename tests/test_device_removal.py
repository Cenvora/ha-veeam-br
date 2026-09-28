"""Tests for removing devices that no longer exist on the server.

Two paths matter. Home Assistant only offers a per-device **Delete** button when an
integration implements async_remove_config_entry_device, which is why a deleted repository
could previously only be disabled. And the automatic sweep must not mistake a failed fetch for
a deletion.

device_is_current lives in __init__.py, which imports Home Assistant, so it is lifted out with
ast — the same approach as the other coordinator helpers.
"""

import ast
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"
INIT_PATH = COMPONENT / "__init__.py"
SENSOR_PATH = COMPONENT / "sensor.py"

ENTRY_ID = "entry-1"


@pytest.fixture(name="is_current", scope="module")
def is_current_fixture():
    """Execute device_is_current with the constants it depends on."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "device_is_current")
        or (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") in {"DEVICE_KINDS", "SINGLETON_KINDS", "DOMAIN"}
        )
    ]
    names = [
        getattr(n, "name", getattr(getattr(n, "targets", [None])[0], "id", "")) for n in wanted
    ]
    assert "device_is_current" in names, f"helper not found, got {names}"

    namespace = {"DOMAIN": "veeam_br"}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(INIT_PATH), "exec"), namespace)
    return namespace["device_is_current"]


def data(**overrides):
    """Coordinator data with one of everything."""
    payload = {
        "jobs": [{"id": "job-1", "name": "Nightly"}],
        "repositories": [{"id": "repo-1", "name": "Default"}],
        "sobrs": [{"id": "sobr-1", "name": "Main"}],
        "server_info": {"name": "vbr01"},
        "license_info": {"status": "Valid"},
        "ha_cluster": None,
    }
    payload.update(overrides)
    return payload


def ids(identifier):
    return {("veeam_br", identifier)}


@pytest.mark.parametrize(
    "identifier",
    ["job_job-1", "repository_repo-1", "sobr_sobr-1", f"server_{ENTRY_ID}", f"license_{ENTRY_ID}"],
)
def test_live_devices_may_not_be_deleted(is_current, identifier):
    """Deleting one would just recreate it on the next poll."""
    assert is_current(ids(identifier), data(), ENTRY_ID) is True


@pytest.mark.parametrize(
    "identifier",
    ["job_job-9", "repository_repo-9", "sobr_sobr-9"],
)
def test_deleted_objects_may_be_removed(is_current, identifier):
    """The reported case: a repository deleted in Veeam should be purgeable in HA."""
    assert is_current(ids(identifier), data(), ENTRY_ID) is False


def test_a_cluster_device_becomes_removable_once_the_cluster_is_gone(is_current):
    """ha_cluster is None on a server that is no longer clustered."""
    assert is_current(ids(f"ha_cluster_{ENTRY_ID}"), data(), ENTRY_ID) is False
    assert (
        is_current(ids(f"ha_cluster_{ENTRY_ID}"), data(ha_cluster={"name": "VBR-HA"}), ENTRY_ID)
        is True
    )


def test_a_singleton_from_another_entry_is_removable(is_current):
    """A device left behind by a config entry that no longer exists."""
    assert is_current(ids("server_some-old-entry"), data(), ENTRY_ID) is False


def test_devices_from_other_integrations_are_not_claimed(is_current):
    assert is_current({("hue", "light-1")}, data(), ENTRY_ID) is False


def test_an_unrecognised_identifier_is_removable(is_current):
    """It cannot be something this version maintains."""
    assert is_current(ids("mystery_thing"), data(), ENTRY_ID) is False


@pytest.mark.parametrize("empty", [None, {}])
def test_without_data_the_user_is_not_blocked(is_current, empty):
    """An unloaded or failed entry should not prevent cleaning up."""
    assert is_current(ids("job_job-1"), empty, ENTRY_ID) is False


def test_missing_collections_do_not_raise(is_current):
    assert is_current(ids("job_job-1"), {"server_info": None}, ENTRY_ID) is False


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_the_delete_hook_exists_with_the_name_home_assistant_looks_for():
    """The name is the contract: HA looks up this exact function on the integration."""
    source = INIT_PATH.read_text(encoding="utf-8")

    assert "async def async_remove_config_entry_device(" in source
    hook = source[source.index("async def async_remove_config_entry_device") :]
    hook = hook[: hook.index("\n\ndef ")] if "\n\ndef " in hook else hook

    assert "device_is_current" in hook, "should refuse to delete a device that still exists"
    assert "return False" in hook and "return True" in hook


PRUNING_PATH = COMPONENT / "pruning.py"


@pytest.fixture(name="reported", scope="module")
def reported_fixture():
    """reported_ids from pruning.py, which decides whether a kind may be pruned at all."""
    tree = ast.parse(PRUNING_PATH.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in ("reported_ids", "forget_missing")
    ]
    namespace = {"Any": object}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(PRUNING_PATH), "exec"), namespace)
    return namespace


def test_the_automatic_sweep_will_not_purge_on_an_empty_fetch(reported):
    """An empty list is indistinguishable from a failed fetch that degraded gracefully.

    Without this, the first time the jobs endpoint errored, every job device would be deleted.
    """
    assert reported["reported_ids"](data(jobs=[]), "jobs") is None


def test_the_automatic_sweep_will_not_purge_after_a_failed_fetch(reported):
    """A failed endpoint keeps its previous data; that is not evidence either way."""
    payload = data(fetch_ok={"jobs": False, "repositories": True})

    assert reported["reported_ids"](payload, "jobs") is None
    assert reported["reported_ids"](payload, "repositories") == {"repo-1"}


def test_a_successful_fetch_is_trusted(reported):
    assert reported["reported_ids"](data(fetch_ok={"jobs": True}), "jobs") == {"job-1"}


def test_platforms_forget_only_what_was_really_removed(reported):
    tracked = {"job-1", "job-2"}

    reported["forget_missing"](tracked, None)
    assert tracked == {"job-1", "job-2"}, "an untrusted cycle forgets nothing"

    reported["forget_missing"](tracked, {"job-1"})
    assert tracked == {"job-1"}


def test_one_sweep_serves_every_platform():
    """Each platform used to scan the whole registry, deleting the others' entities."""
    for name in ("sensor.py", "binary_sensor.py", "button.py"):
        source = (COMPONENT / name).read_text(encoding="utf-8")
        assert "_remove_stale" not in source, f"{name} still sweeps"
        assert "dr.async_get" not in source, f"{name} still removes devices itself"
        assert "forget_missing(" in source, f"{name} never forgets pruned objects"

    pruning = PRUNING_PATH.read_text(encoding="utf-8")
    assert "async_entries_for_config_entry" in pruning


def test_manual_deletion_covers_what_the_sweep_deliberately_skips():
    """The sweep never prunes on empty, so the manual path has to work."""
    init_source = INIT_PATH.read_text(encoding="utf-8")
    pruning_source = PRUNING_PATH.read_text(encoding="utf-8")

    assert "async def async_remove_config_entry_device" in init_source
    assert "Delete button" in pruning_source
