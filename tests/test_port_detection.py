"""Tests for telling the user which REST API port actually answered.

Veeam B&R 13.1 moved the REST API to 443 and will eventually drop 9419, so a failed
connection is now often the wrong port rather than a wrong host or a closed firewall. The
probing itself lives in veeam_br.discovery; what matters here is that a wrong port produces
port-specific advice instead of a bare "cannot connect".

config_flow.py imports Home Assistant, so the helper is lifted out with ast and run against
a stubbed veeam_br.discovery.
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

SUPPORTED = {"1.2-rev1": "v1_2_rev1", "1.3-rev2": "v1_3_rev2"}


def load_finder(endpoint=None, raises=None, record=None):
    """Load async_find_working_port with veeam_br.discovery stubbed out."""
    tree = ast.parse(CONFIG_FLOW_PATH.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_find_working_port"
    )

    async def detect_rest_api(host, *, ports=None, versions=None, verify_ssl=True, **kwargs):
        if record is not None:
            record.update(host=host, ports=ports, versions=versions, verify_ssl=verify_ssl)
        if raises is not None:
            raise raises
        return endpoint

    discovery = types.ModuleType("veeam_br.discovery")
    discovery.detect_rest_api = detect_rest_api
    discovery.DEFAULT_PORTS = (443, 9419)
    sys.modules.setdefault("veeam_br", types.ModuleType("veeam_br"))
    sys.modules["veeam_br.discovery"] = discovery

    namespace = {
        "API_VERSIONS": SUPPORTED,
        "CONF_HOST": "host",
        "CONF_VERIFY_SSL": "verify_ssl",
        "DEFAULT_VERIFY_SSL": True,
        "_LOGGER": types.SimpleNamespace(debug=lambda *a, **k: None),
        "Any": object,
    }
    exec(
        compile(ast.Module(body=[func], type_ignores=[]), str(CONFIG_FLOW_PATH), "exec"),
        namespace,
    )
    return namespace["async_find_working_port"]


class Endpoint:
    def __init__(self, port, api_version="1.3-rev2"):
        self.port = port
        self.api_version = api_version


def data(**overrides):
    entry = {"host": "vbr.example.com", "verify_ssl": True}
    entry.update(overrides)
    return entry


def test_reports_the_port_that_answered():
    finder = load_finder(endpoint=Endpoint(443))

    assert asyncio.run(finder(data(), 9419)) == 443


def test_only_the_other_ports_are_probed():
    """Re-probing the port that just failed to connect would waste a round trip."""
    record = {}
    finder = load_finder(endpoint=Endpoint(443), record=record)

    asyncio.run(finder(data(), 9419))

    assert record["ports"] == [443], "should skip the port already known to fail"
    assert record["host"] == "vbr.example.com"
    assert record["versions"] == list(SUPPORTED)


def test_a_custom_port_still_gets_both_well_known_ports_probed():
    """Someone on a non-standard port may simply have the wrong number entirely."""
    record = {}
    finder = load_finder(endpoint=Endpoint(443), record=record)

    assert asyncio.run(finder(data(), 8443)) == 443
    assert record["ports"] == [443, 9419], "neither well-known port has been ruled out"


def test_verify_ssl_is_passed_through():
    record = {}
    finder = load_finder(endpoint=Endpoint(9419), record=record)

    asyncio.run(finder(data(verify_ssl=False), 443))

    assert record["verify_ssl"] is False


def test_no_answer_gives_no_advice():
    """Nothing answering means the problem is not the port."""
    finder = load_finder(endpoint=None)

    assert asyncio.run(finder(data(), 443)) is None


def test_an_older_library_degrades_to_the_generic_error():
    """An ImportError escaping here would surface as "unknown" instead of the real error.

    The manifest floors veeam-br at 0.5.0, but a hand-installed older copy has no
    detect_rest_api, and the probe runs inside the connection-failure handler.
    """
    tree = ast.parse(CONFIG_FLOW_PATH.read_text(encoding="utf-8"))
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_find_working_port"
    )

    # A discovery module without the new names, exactly like veeam-br 0.4.0
    sys.modules.setdefault("veeam_br", types.ModuleType("veeam_br"))
    sys.modules["veeam_br.discovery"] = types.ModuleType("veeam_br.discovery")

    namespace = {
        "API_VERSIONS": SUPPORTED,
        "CONF_HOST": "host",
        "CONF_VERIFY_SSL": "verify_ssl",
        "DEFAULT_VERIFY_SSL": True,
        "_LOGGER": types.SimpleNamespace(debug=lambda *a, **k: None),
        "Any": object,
    }
    exec(
        compile(ast.Module(body=[func], type_ignores=[]), str(CONFIG_FLOW_PATH), "exec"),
        namespace,
    )

    assert asyncio.run(namespace["async_find_working_port"](data(), 9419)) is None


def test_a_failing_probe_is_not_fatal():
    """The probe is a nicety; it must not replace the real connection error."""
    finder = load_finder(raises=OSError("no route to host"))

    assert asyncio.run(finder(data(), 443)) is None


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_wrong_port_is_caught_before_connection_error():
    """WrongPortError subclasses ConnectionError, so ordering decides which wins."""
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert "class WrongPortError(ConnectionError)" in content

    # Every handler pair must test the subclass first, or the advice is never shown
    assert (
        content.count('errors["base"] = "wrong_port"') == 4
    ), "all four flows (user, reconfigure, reauth, options) should surface it"

    handlers = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith(("except WrongPortError", "except ConnectionError"))
    ]
    assert handlers, "no handlers found — has the flow been restructured?"
    for wrong_port, connection in zip(handlers[::2], handlers[1::2]):
        assert wrong_port.startswith("except WrongPortError"), (
            f"ConnectionError is caught before WrongPortError ({connection}), which would "
            "swallow the port advice"
        )
        assert connection.startswith("except ConnectionError")


def test_every_form_supplies_the_placeholder():
    """A message referencing {wrong_port} with no placeholder renders broken."""
    content = CONFIG_FLOW_PATH.read_text(encoding="utf-8")

    assert content.count('"wrong_port": str(wrong_port or "")') == 4


def test_error_text_names_both_ports():
    """The advice is only useful if it explains which port goes with which release."""
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        for section in ("config", "options"):
            message = data[section]["error"]["wrong_port"]
            assert "{wrong_port}" in message
            assert "443" in message
            assert "9419" in message


@pytest.mark.parametrize("section", ["config", "options"])
def test_both_flows_can_render_the_error(section):
    """The options flow resolves errors from its own section, not the config one."""
    data = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))

    assert "wrong_port" in data[section]["error"]
