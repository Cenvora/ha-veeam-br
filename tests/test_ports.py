"""Tests for the REST API port default.

Veeam B&R 13.1 serves the REST API on 443 and no longer needs a dedicated port. 9419 still
answers on 13.1 for backward compatibility, but Veeam has said it will be removed, so new
setups start on 443 while older servers keep working on 9419.
"""

import importlib.util
import json
from pathlib import Path

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"
TRANSLATIONS = COMPONENT / "translations"


def _load_const():
    spec = importlib.util.spec_from_file_location("veeam_br_const", COMPONENT / "const.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_port_is_443():
    """13.1 answers on 443; that is what a new setup should try first."""
    const = _load_const()

    assert const.DEFAULT_PORT == 443


def test_legacy_port_is_still_named():
    """Older servers need 9419, so the value stays available and documented."""
    const = _load_const()

    assert const.LEGACY_PORT == 9419
    assert const.LEGACY_PORT != const.DEFAULT_PORT


def test_default_is_only_a_form_default():
    """Existing entries must keep their configured port, not inherit the new default."""
    content = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")

    # Reconfigure pre-fills from the stored entry and only falls back to the default
    assert "reconf_entry.data.get(CONF_PORT, DEFAULT_PORT)" in content

    # Setup never reads the port from anywhere but the submitted form or the default
    assert "entry.data[CONF_PORT]" in (COMPONENT / "__init__.py").read_text(encoding="utf-8")


def test_both_forms_explain_the_port_choice():
    """A bare "443" would strand anyone on a pre-13.1 server."""
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        for step in ("user", "reconfigure"):
            description = data["config"]["step"][step]["data_description"]["port"]
            assert "443" in description, f"{name}/{step} should mention 443"
            assert "9419" in description, f"{name}/{step} should mention the legacy port"
            assert "13.1" in description, f"{name}/{step} should say which version changed"


def test_no_translation_still_advertises_9419_as_the_default():
    """Every locale carries its own copy of the port hint."""
    offenders = []
    for path in sorted(TRANSLATIONS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        description = data["config"]["step"]["user"]["data_description"]["port"]
        if "443" not in description:
            offenders.append(path.name)

    assert not offenders, f"these locales still point at the old default: {offenders}"


def test_readme_does_not_call_9419_the_default():
    """The README is where people look before opening the form."""
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")

    assert "default: 9419" not in readme
    assert "443" in readme, "the README should name the current port"
