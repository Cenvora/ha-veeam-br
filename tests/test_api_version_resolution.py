"""Tests for auto-detecting the API version (config flow policy).

The detection mechanism lives in veeam_br.discovery and is tested there. What matters here
is the policy: auto is the default, the sentinel is resolved before anything is stored, a
server that cannot be probed still gets a working default, and an explicit choice is left
alone.

config_flow.py imports Home Assistant, which this environment does not have, so
async_resolve_api_version is lifted out of the source with ast and run against a stubbed
veeam_br.discovery.
"""

import ast
import asyncio
import json
from pathlib import Path
import sys
import types

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"
CONFIG_FLOW_PATH = COMPONENT / "config_flow.py"
CONST_PATH = COMPONENT / "const.py"

AUTO = "auto"
SUPPORTED = {
    "1.2-rev1": "v1_2_rev1",
    "1.3-rev0": "v1_3_rev0",
    "1.3-rev1": "v1_3_rev1",
    "1.3-rev2": "v1_3_rev2",
}
DEFAULT = "1.3-rev2"


def load_resolver(detected=None, raises=None, record=None):
    """Load async_resolve_api_version with veeam_br.discovery stubbed out."""
    tree = ast.parse(CONFIG_FLOW_PATH.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_resolve_api_version"
    )

    async def detect_api_version(base_url, *, verify_ssl=True, versions=None, **kwargs):
        if record is not None:
            record.update(base_url=base_url, verify_ssl=verify_ssl, versions=versions)
        if raises is not None:
            raise raises
        return detected

    # The function imports veeam_br.discovery at call time
    discovery = types.ModuleType("veeam_br.discovery")
    discovery.detect_api_version = detect_api_version
    veeam_br = sys.modules.get("veeam_br") or types.ModuleType("veeam_br")
    sys.modules["veeam_br"] = veeam_br
    sys.modules["veeam_br.discovery"] = discovery

    namespace = {
        "AUTO_API_VERSION": AUTO,
        "API_VERSIONS": SUPPORTED,
        "CONF_API_VERSION": "api_version",
        "CONF_HOST": "host",
        "CONF_PORT": "port",
        "CONF_VERIFY_SSL": "verify_ssl",
        "DEFAULT_API_VERSION": DEFAULT,
        "DEFAULT_VERIFY_SSL": True,
        "_LOGGER": types.SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None),
        "Any": object,
    }
    exec(
        compile(ast.Module(body=[func], type_ignores=[]), str(CONFIG_FLOW_PATH), "exec"),
        namespace,
    )
    return namespace["async_resolve_api_version"]


def entry(**overrides):
    data = {"host": "vbr.example.com", "port": 9419, "api_version": AUTO}
    data.update(overrides)
    return data


def resolve(resolver, data):
    return asyncio.run(resolver(data))


def test_auto_resolves_to_the_detected_version():
    resolver = load_resolver(detected="1.3-rev0")

    assert resolve(resolver, entry()) == "1.3-rev0"


def test_detection_is_told_only_supported_versions():
    """Probing a version the SDK cannot speak would produce an unusable answer."""
    record = {}
    resolver = load_resolver(detected="1.3-rev1", record=record)

    resolve(resolver, entry())

    assert record["versions"] == list(SUPPORTED)
    assert record["base_url"] == "https://vbr.example.com:9419"


def test_verify_ssl_is_passed_through():
    """A self-signed server must be probeable, or detection never works there."""
    record = {}
    resolver = load_resolver(detected="1.3-rev1", record=record)

    resolve(resolver, entry(verify_ssl=False))

    assert record["verify_ssl"] is False


def test_undetectable_server_falls_back_to_the_default():
    """Swagger may be disabled or gated; setup should still proceed."""
    resolver = load_resolver(detected=None)

    assert resolve(resolver, entry()) == DEFAULT


def test_detection_failure_falls_back_to_the_default():
    """A raising probe must not break the config flow."""
    resolver = load_resolver(raises=OSError("network unreachable"))

    assert resolve(resolver, entry()) == DEFAULT


@pytest.mark.parametrize("chosen", sorted(SUPPORTED))
def test_an_explicit_choice_is_never_overridden(chosen):
    """A user who pinned a version means it, even if the server serves a newer one."""
    record = {}
    resolver = load_resolver(detected="1.3-rev2", record=record)

    assert resolve(resolver, entry(api_version=chosen)) == chosen
    assert record == {}, "no probing should happen when a version is pinned"


def test_missing_api_version_is_treated_as_auto():
    """Entries created before this option existed should still get detection."""
    data = entry()
    del data["api_version"]
    resolver = load_resolver(detected="1.3-rev1")

    assert resolve(resolver, data) == "1.3-rev1"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_sentinel_is_never_stored():
    """The resolved version must reach the config entry, not the sentinel."""
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert (
        "data[CONF_API_VERSION] = api_version" in content
    ), "validate_input should write the resolved version back for the caller to store"

    # The options flow builds its own dict to save, so it needs the resolved value too
    options = content[content.index("class VeeamBROptionsFlow") :]
    assert (
        "CONF_API_VERSION: test_data[CONF_API_VERSION]" in options
    ), "the options flow must persist the resolved version, not the submitted sentinel"


def test_auto_is_offered_and_is_the_default():
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert "[AUTO_API_VERSION, *API_VERSIONS.keys()]" in content
    assert (
        content.count("[AUTO_API_VERSION, *API_VERSIONS.keys()]") == 2
    ), "both the config flow and the options flow should offer auto"
    assert "return api_version_options, AUTO_API_VERSION" in content


def test_auto_sentinel_is_not_a_real_api_version():
    """The sentinel must not collide with a version the SDK could ship."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("veeam_br_const", CONST_PATH)
    const = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(const)

    assert const.AUTO_API_VERSION == AUTO
    assert const.AUTO_API_VERSION not in const.FALLBACK_API_VERSIONS
    assert const.AUTO_API_VERSION not in const.API_VERSIONS
    assert const.AUTO_API_VERSION != const.DEFAULT_API_VERSION


def test_auto_option_is_explained_in_strings():
    """A bare "auto" in a dropdown needs a sentence saying what it does."""
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        description = data["config"]["step"]["user"]["data_description"]["api_version"]
        assert "auto" in description.lower()
