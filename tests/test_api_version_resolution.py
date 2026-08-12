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
RESOLVER_PATH = COMPONENT / "api_version.py"
INIT_PATH = COMPONENT / "__init__.py"
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
    tree = ast.parse(RESOLVER_PATH.read_text(encoding="utf-8"))
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
        compile(ast.Module(body=[func], type_ignores=[]), str(RESOLVER_PATH), "exec"),
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


def test_the_sentinel_is_stored_not_resolved_away():
    """auto is a standing intent, so it has to survive being saved.

    Resolving it at save time would freeze the entry on whichever revision was newest that
    day, which defeats the point: a server upgrade or a newer veeam-br should move it on.
    """
    flow = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert (
        "data[CONF_API_VERSION] = api_version" not in flow
    ), "validate_input must not write the resolved version back over the user's choice"

    options = flow[flow.index("class VeeamBROptionsFlow") :]
    assert (
        'async_create_entry(title="", data=user_input)' in options
    ), "the options flow should store what was chosen, including auto"


def test_setup_resolves_the_sentinel_on_every_start():
    """That is what makes a stored auto keep up with the server and the library."""
    init = INIT_PATH.read_text(encoding="utf-8")

    assert "stored_version == AUTO_API_VERSION" in init
    assert "await async_resolve_api_version(" in init

    # The answer has to reach the platforms, which cannot re-detect for themselves
    assert '"api_version": api_version' in init


def test_readers_go_through_one_resolver():
    """A reader seeing the raw "auto" would fall back to the default and silently disagree
    with the version the coordinator is actually using."""
    direct_read = "entry.options.get("

    for name in ("button.py", "sensor.py", "diagnostics.py"):
        source = (COMPONENT / name).read_text(encoding="utf-8")
        assert "configured_api_version" in source, f"{name} should use the shared resolver"

    # diagnostics reads the stored value on purpose, to report it alongside the resolved one
    for name in ("button.py", "sensor.py"):
        source = (COMPONENT / name).read_text(encoding="utf-8")
        assert direct_read not in source, f"{name} still reads the stored value directly"


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


# ---------------------------------------------------------------------------
# The shared resolver, which every reader depends on
# ---------------------------------------------------------------------------


def _load_const():
    import importlib.util

    spec = importlib.util.spec_from_file_location("veeam_br_const", CONST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Entry:
    """Duck-typed config entry."""

    def __init__(self, data=None, options=None, runtime_data=None):
        self.data = data or {}
        self.options = options or {}
        if runtime_data is not None:
            self.runtime_data = runtime_data


def test_the_resolved_version_wins_once_setup_has_run():
    const = _load_const()
    entry = Entry(data={"api_version": "auto"}, runtime_data={"api_version": "1.2-rev1"})

    assert const.configured_api_version(entry) == "1.2-rev1"


def test_auto_before_setup_falls_back_to_the_default():
    """Platforms can ask before the coordinator exists; the default is the honest answer."""
    const = _load_const()

    assert const.configured_api_version(Entry(data={"api_version": "auto"})) == DEFAULT


def test_a_pinned_version_is_returned_as_is():
    const = _load_const()

    assert const.configured_api_version(Entry(data={"api_version": "1.3-rev0"})) == "1.3-rev0"


def test_options_override_data():
    const = _load_const()
    entry = Entry(data={"api_version": "1.2-rev1"}, options={"api_version": "1.3-rev1"})

    assert const.configured_api_version(entry) == "1.3-rev1"


def test_a_stale_auto_in_runtime_data_is_ignored():
    """Belt and braces: runtime_data should never hold the sentinel, and if it did, returning
    it would break every API_VERSIONS lookup downstream."""
    const = _load_const()
    entry = Entry(data={"api_version": "auto"}, runtime_data={"api_version": "auto"})

    assert const.configured_api_version(entry) == DEFAULT


def test_an_entry_with_no_version_at_all_still_works():
    const = _load_const()

    assert const.configured_api_version(Entry()) == DEFAULT
