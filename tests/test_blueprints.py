"""Validation for the shipped automation blueprints.

Home Assistant is not installed in this environment, so these tests check the things that
actually break a blueprint in the wild and that no YAML linter would catch: an `!input` that
was never declared, a declared input nothing uses (a UI field that does nothing), a
`source_url` that does not match where the file really lives (which breaks the import link),
and selectors pointing at a different integration.
"""

import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parent.parent
BLUEPRINT_DIR = REPO / "blueprints" / "automation" / "veeam_br"
REPO_SLUG = "Cenvora/ha-veeam-br"


class BlueprintLoader(yaml.SafeLoader):
    """SafeLoader that understands Home Assistant's blueprint tags."""


class Input:
    """Stands in for an !input tag so the document can be walked."""

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"!input {self.name}"


BlueprintLoader.add_constructor("!input", lambda loader, node: Input(loader.construct_scalar(node)))


def blueprint_files():
    return sorted(BLUEPRINT_DIR.glob("*.yaml"))


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=BlueprintLoader)


def walk(node):
    """Yield every value in a nested structure."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def used_inputs(document):
    return {node.name for node in walk(document) if isinstance(node, Input)}


def test_blueprints_exist():
    assert blueprint_files(), "no blueprints found — has the directory moved?"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_has_required_metadata(path):
    """Missing metadata makes a blueprint unimportable or anonymous in the UI."""
    document = load(path)
    meta = document.get("blueprint")

    assert meta, "no blueprint: block"
    assert meta.get("domain") == "automation"
    assert meta.get("name"), "needs a name for the blueprint list"
    assert meta.get("description"), "needs a description explaining which entities to pick"
    assert meta.get("input"), "an automation blueprint with no inputs is just an automation"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_source_url_matches_the_file_location(path):
    """The import link is built from source_url; a stale one imports the wrong file."""
    document = load(path)
    source_url = document["blueprint"].get("source_url")

    assert source_url, "needs source_url so Home Assistant can offer re-import"
    expected = f"https://github.com/{REPO_SLUG}/blob/main/" f"{path.relative_to(REPO).as_posix()}"
    assert source_url == expected, f"expected {expected}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_every_input_is_declared(path):
    """An !input with no declaration fails at import time with a schema error."""
    document = load(path)
    declared = set(document["blueprint"]["input"])

    undeclared = used_inputs(document) - declared
    assert not undeclared, f"used but not declared: {sorted(undeclared)}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_every_declared_input_is_used(path):
    """A declared input nothing references is a UI field that silently does nothing."""
    document = load(path)
    declared = set(document["blueprint"]["input"])

    unused = declared - used_inputs(document)
    assert not unused, f"declared but never used: {sorted(unused)}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_is_a_runnable_automation(path):
    """Uses the current plural keys, which the 2026.1 floor guarantees."""
    document = load(path)

    assert "triggers" in document, "no triggers"
    assert "actions" in document, "no actions"
    assert "trigger" not in document, "singular trigger: is the pre-2024.10 spelling"
    assert "action" not in document, "singular action: is the pre-2024.10 spelling"
    assert "condition" not in document, "singular condition: is the pre-2024.10 spelling"

    for entry in document["triggers"]:
        assert "trigger" in entry, f"trigger entry missing its platform: {entry}"
        assert "platform" not in entry, "platform: is the pre-2024.10 spelling"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_entity_selectors_target_this_integration(path):
    """A selector without the filter lists every entity in the user's system."""
    document = load(path)

    for name, spec in document["blueprint"]["input"].items():
        selector = spec.get("selector", {})
        if "entity" not in selector:
            continue
        filters = (selector["entity"] or {}).get("filter")
        assert filters, f"{name}: entity selector should filter to this integration"
        integrations = {f.get("integration") for f in filters}
        assert integrations == {"veeam_br"}, f"{name}: filters {integrations}"


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_notification_action_is_an_action_selector(path):
    """Hard-coding a notify service would tie the blueprint to one setup."""
    document = load(path)
    inputs = document["blueprint"]["input"]

    assert "notification_action" in inputs, "every blueprint should let the user choose"
    assert "action" in inputs["notification_action"]["selector"]


@pytest.mark.parametrize("path", blueprint_files(), ids=lambda p: p.name)
def test_optional_inputs_have_defaults(path):
    """An input with no default is mandatory; that has to be deliberate."""
    document = load(path)

    mandatory = [
        name for name, spec in document["blueprint"]["input"].items() if "default" not in spec
    ]
    # The entities to watch and the action to run are the only things a user must supply
    allowed = {
        "job_result_sensors",
        "repository_sensors",
        "expiry_sensors",
        "failover_sensor",
        "notification_action",
    }
    assert set(mandatory) <= allowed, f"unexpectedly mandatory: {sorted(set(mandatory) - allowed)}"


def test_job_blueprints_watch_last_result_not_status():
    """Status never reports failure — EJobStatus is running/inactive/disabled.

    The pass/fail outcome is on the Last Result sensor (ESessionResult). A blueprint keyed on
    Status would never fire.
    """
    for name in ("job_failed.yaml", "daily_backup_summary.yaml"):
        text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")
        assert "Last Result" in text, f"{name} should direct users to the Last Result sensor"


def test_result_matching_is_case_insensitive():
    """1.2-rev1 lower-cases some enums where 1.3-rev* capitalizes them."""
    for name in ("job_failed.yaml", "daily_backup_summary.yaml"):
        text = (BLUEPRINT_DIR / name).read_text(encoding="utf-8")
        assert (
            "| lower" in text or "map('lower')" in text
        ), f"{name} should compare results case-insensitively across API revisions"


def test_readme_links_every_blueprint():
    """A blueprint nobody can find is a blueprint nobody uses."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    for path in blueprint_files():
        assert path.name in readme, f"{path.name} is not mentioned in the README"
        assert "blueprint_url" in readme, "README should offer one-click import links"


def test_hacs_declares_the_supported_home_assistant_version():
    """HACS blocks installation on older cores using this value."""
    hacs = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))

    major, minor = hacs["homeassistant"].split(".")[:2]
    assert (int(major), int(minor)) >= (2026, 1), (
        "blueprints use the plural trigger/action keys, which need 2024.10+; the project "
        "targets 2026.1+"
    )
