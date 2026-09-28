"""Basic validation tests for Veeam BR integration."""


def test_manifest_valid():
    """Test that manifest.json is valid and contains required fields."""
    import json
    from pathlib import Path

    manifest_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "manifest.json"
    )

    with open(manifest_path) as f:
        manifest = json.load(f)

    # Check required fields
    required_fields = [
        "domain",
        "name",
        "version",
        "documentation",
        "requirements",
        "codeowners",
        "iot_class",
        "config_flow",
    ]
    for field in required_fields:
        assert field in manifest, f"Missing required field: {field}"

    # Check specific values
    assert manifest["domain"] == "veeam_br"
    assert manifest["config_flow"] is True
    assert "veeam-br" in manifest["requirements"][0]


def test_strings_valid():
    """Test that strings.json is valid."""
    import json
    from pathlib import Path

    strings_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "strings.json"

    with open(strings_path) as f:
        strings = json.load(f)

    # Check for required sections
    assert "config" in strings
    assert "options" in strings

    # Check for reauth support
    assert "reauth_confirm" in strings["config"]["step"]
    assert "username" in strings["config"]["step"]["reauth_confirm"]["data"]
    assert "password" in strings["config"]["step"]["reauth_confirm"]["data"]


def test_imports():
    """Test that all modules can be imported."""
    from pathlib import Path

    # Check that key files exist
    base_path = Path(__file__).parent.parent / "custom_components" / "veeam_br"

    assert (base_path / "const.py").exists(), "const.py should exist"
    assert (base_path / "config_flow.py").exists(), "config_flow.py should exist"
    assert (base_path / "__init__.py").exists(), "__init__.py should exist"

    # Check for reauth methods in config_flow
    with open(base_path / "config_flow.py") as f:
        config_flow_content = f.read()

    assert "async def async_step_reauth" in config_flow_content
    assert "async def async_step_reauth_confirm" in config_flow_content


def test_const_api_versions():
    """Test that API versions are properly configured."""
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "const.py"

    with open(const_path) as f:
        const_content = f.read()

    # Check that API versions and default are defined
    assert "API_VERSIONS" in const_content
    assert "DEFAULT_API_VERSION" in const_content


def test_config_flow_has_reauth():
    """Test that config flow has reauth capability."""
    from pathlib import Path

    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        content = f.read()

    # Check that reauth methods exist
    assert (
        "async def async_step_reauth" in content
    ), "Config flow should have async_step_reauth method"
    assert (
        "async def async_step_reauth_confirm" in content
    ), "Config flow should have async_step_reauth_confirm method"


def test_runtime_data_usage():
    """Test that the integration uses runtime_data instead of hass.data."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Check that runtime_data is used
    assert "entry.runtime_data" in init_content, "Integration should use entry.runtime_data"

    # Check for sensor.py
    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sensor.py"
    with open(sensor_path) as f:
        sensor_content = f.read()

    assert "entry.runtime_data" in sensor_content, "Sensors should use entry.runtime_data"

    # Check for button.py
    button_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "button.py"
    with open(button_path) as f:
        button_content = f.read()

    assert "entry.runtime_data" in button_content, "Buttons should use entry.runtime_data"


def test_diagnostics_support():
    """Test that diagnostics module exists and has required function."""
    from pathlib import Path

    diagnostics_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "diagnostics.py"
    )

    # Check diagnostics file exists
    assert diagnostics_path.exists(), "diagnostics.py should exist for Gold tier"

    # Check the function exists in the file
    with open(diagnostics_path) as f:
        diagnostics_content = f.read()

    assert (
        "async def async_get_config_entry_diagnostics" in diagnostics_content
    ), "diagnostics module should have async_get_config_entry_diagnostics function"


def test_action_exceptions():
    """Test that button actions raise exceptions on failure (Silver tier requirement)."""
    from pathlib import Path

    button_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "button.py"

    with open(button_path) as f:
        button_content = f.read()

    # Check that outer exception handlers raise exceptions
    # Count the number of "except Exception as err:" that should raise
    import re

    # Find all outer exception handlers (not in nested try blocks)
    # We're looking for patterns like "except Exception as err:" followed by logging and raise
    outer_exceptions = re.findall(
        r"except Exception as err:.*?(?=\n(?:class |async def |def |$))",
        button_content,
        re.DOTALL,
    )

    # Each outer exception handler should have a raise statement
    for exc_block in outer_exceptions:
        if "_LOGGER.error" in exc_block:
            assert (
                "raise" in exc_block
            ), f"Exception handlers should re-raise exceptions for Silver tier compliance"


def test_reconfigure_flow():
    """Test that reconfigure flow is implemented (Gold tier requirement)."""
    from pathlib import Path

    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        content = f.read()

    # Check that reconfigure method exists
    assert (
        "async def async_step_reconfigure" in content
    ), "Config flow should have async_step_reconfigure method for Gold tier"

    # Check strings.json has reconfigure step
    strings_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "strings.json"

    import json

    with open(strings_path) as f:
        strings = json.load(f)

    assert "reconfigure" in strings["config"]["step"], "strings.json should have reconfigure step"

    # Check that abort messages exist for reconfigure and reauth
    assert "abort" in strings["config"], "strings.json should have abort section"
    assert (
        "reconfigure_successful" in strings["config"]["abort"]
    ), "strings.json should have reconfigure_successful abort message"
    assert (
        "reauth_successful" in strings["config"]["abort"]
    ), "strings.json should have reauth_successful abort message"
    assert (
        "cannot_connect" in strings["config"]["abort"]
    ), "strings.json should have cannot_connect abort message"
    assert (
        "invalid_auth" in strings["config"]["abort"]
    ), "strings.json should have invalid_auth abort message"
    assert "unknown" in strings["config"]["abort"], "strings.json should have unknown abort message"


def test_parallel_updates():
    """Test that PARALLEL_UPDATES is specified (Silver tier requirement)."""
    from pathlib import Path

    # Check sensor.py
    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sensor.py"

    with open(sensor_path) as f:
        sensor_content = f.read()

    assert "PARALLEL_UPDATES" in sensor_content, "sensor.py should define PARALLEL_UPDATES"

    # Check button.py
    button_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "button.py"

    with open(button_path) as f:
        button_content = f.read()

    assert "PARALLEL_UPDATES" in button_content, "button.py should define PARALLEL_UPDATES"


def test_strict_typing():
    """Test that strict typing is enabled (Platinum tier requirement)."""
    from pathlib import Path

    # Check pyproject.toml has strict typing enabled
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"

    with open(pyproject_path) as f:
        content = f.read()

    assert "strict = true" in content, "pyproject.toml should have mypy strict mode enabled"
    assert (
        "disallow_untyped_defs = true" in content
    ), "pyproject.toml should have disallow_untyped_defs enabled"

    # Check py.typed marker exists
    py_typed_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "py.typed"

    assert py_typed_path.exists(), "py.typed marker file should exist for Platinum tier"


def test_async_dependency():
    """Test that the dependency is async (Platinum tier requirement)."""
    from pathlib import Path

    # Check that the integration uses await with veeam_br client
    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"

    with open(init_path) as f:
        init_content = f.read()

    # Verify async usage
    assert "await veeam_client.connect()" in init_content, "Should use async connect"
    assert (
        "await veeam_client.call(" in init_content
    ), "Should use async call method (veeam-br is async)"


def test_stale_entity_cleanup_uses_registry_scan():
    """Stale entity cleanup scans the registry directly, once, for every platform.

    Scanning the registry (rather than session-tracked IDs) is what removes entities persisted
    from earlier sessions. It lives in pruning.py: when each platform ran its own scan, the
    sensor platform deleted the binary sensors and buttons of any repository missing from a
    single poll.
    """
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "veeam_br"
    pruning = (base / "pruning.py").read_text(encoding="utf-8")

    assert "async_entries_for_config_entry" in pruning
    assert "device_reg.async_remove_device" in pruning
    # A device another entry still uses is detached, not deleted
    assert "remove_config_entry_id=entry_id" in pruning

    init = (base / "__init__.py").read_text(encoding="utf-8")
    assert "async_prune_stale(hass, entry, coordinator.data)" in init


def test_validate_input_reraises_permission_error():
    """Test that validate_input re-raises PermissionError so callers show correct error.

    If PermissionError is swallowed into ConnectionError, auth failures are incorrectly
    reported as "Failed to connect" instead of "Invalid authentication credentials".
    """
    from pathlib import Path
    import re

    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        content = f.read()

    # The validate_input function must re-raise PermissionError before the generic
    # Exception handler, so that callers can distinguish auth vs connection errors.
    assert (
        "except PermissionError:" in content
    ), "validate_input should catch PermissionError separately"
    # PermissionError handler must contain a bare 'raise' before the generic except Exception
    assert re.search(
        r"except\s+PermissionError\s*:.*?raise.*?except\s+Exception", content, re.DOTALL
    ), "validate_input should re-raise PermissionError (not wrap it in ConnectionError)"


def test_user_step_preserves_input_on_error():
    """Test that the user step form preserves non-sensitive input when re-shown after an error.

    When connection validation fails, the form should pre-fill host, port, and username
    so the user does not have to retype everything.  The password must never be preserved.
    """
    from pathlib import Path
    import re

    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        content = f.read()

    # Verify that host, port and username are populated from user_input when present
    assert (
        "host_default" in content
    ), "async_step_user should compute host_default from user_input to preserve the field"
    assert (
        "username_default" in content
    ), "async_step_user should compute username_default from user_input to preserve the field"
    assert (
        "port_default" in content
    ), "async_step_user should compute port_default from user_input to preserve the field"

    # Password must NOT be preserved (security requirement).
    # Find the async_step_user function body up to the next top-level definition.
    user_step_match = re.search(
        r"(async def async_step_user\b.*?)(?=\n    async def |\nclass |\Z)",
        content,
        re.DOTALL,
    )
    assert user_step_match, "async_step_user should be present"
    user_step_body = user_step_match.group(0)

    assert (
        "password_default" not in user_step_body
    ), "async_step_user must NOT define a password_default; password should never be pre-filled"


def test_hlr_immutability_logic():
    """Immutability is read from wherever each repository kind reports it."""
    import ast
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name == "_repository_immutability")
        or (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "NON_IMMUTABLE_REPOSITORY_TYPES"
        )
    ]
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(init_path), "exec"), namespace)
    immutability = namespace["_repository_immutability"]

    # Linux hardened: makeRecentBackupsImmutableDays
    hardened = {"repository": {"makeRecentBackupsImmutableDays": 7}}
    assert immutability("LinuxHardened", hardened) == (True, 7)
    assert immutability("LinuxHardened", {"repository": {"makeRecentBackupsImmutableDays": 0}}) == (
        False,
        None,
    )

    # Object storage: bucket.immutability, which wins over anything else
    bucket = {"bucket": {"immutability": {"isEnabled": True, "daysCount": 30}}, **hardened}
    assert immutability("AmazonS3", bucket) == (True, 30)
    assert immutability("AmazonS3", {"bucket": {"immutability": {"isEnabled": False}}}) == (
        False,
        None,
    )

    # Linux local on 13.x: governance mode
    governance = {"repository": {"enableGovernanceMode": True, "governanceModeRetentionDays": 14}}
    assert immutability("LinuxLocal", governance) == (True, 14)
    assert immutability("LinuxLocal", {"repository": {"path": "/backups"}}) == (False, None)

    # Data Domain / StoreOnce: repository.immutability
    dd = {"repository": {"immutability": {"isEnabled": True, "daysCount": 5}}}
    assert immutability("DellDataDomain", dd) == (True, 5)

    # Kinds with no immutability at all read False; the unknown stay unknown
    assert immutability("WinLocal", {}) == (False, None)
    assert immutability("Smb", {}) == (False, None)
    assert immutability("AzureBlob", {}) == (None, None)


def test_api_v1_2_rev1_jobs_error_handling():
    """Test that one job failing to parse does not lose the others.

    In API v1.2-rev1 the veeam_br library may raise ValueError (from dict(string))
    when parsing some response fields. A whole response that fails to parse fails the jobs
    endpoint, which keeps its previous data (see test_behaviour.py); a single job that fails
    to parse is skipped and logged.
    """
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"
    content = init_path.read_text(encoding="utf-8")

    block = content[content.index("async def fetch_jobs") :]
    block = block[: block.index("async def fetch_server_info")]
    assert "Failed to parse job" in block
    for exc_type in ("ValueError", "KeyError", "AttributeError", "TypeError"):
        assert exc_type in block, f"the per-job handler should catch {exc_type}"


def test_api_v1_2_rev1_sobr_extent_status():
    """Test that SOBR extent status is handled for both API versions.

    In v1.2-rev1 PerformanceExtentModel.status is a single ERepositoryExtentStatusType
    (a str-subclass enum).  In v1.3-rev1+ it is a list[ERepositoryExtentStatusType].
    The old code did `[s.value for s in extent.status]` which, for a str-enum, iterates
    over individual characters, raising AttributeError on each character's missing .value.
    The fix must handle both the list form and the single-enum form.
    """
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"

    with open(init_path) as f:
        content = f.read()

    # The new code must check whether status is a list or a single enum value.
    assert "isinstance(raw_status, list)" in content, (
        "__init__.py should check if extent.status is a list before iterating "
        "(v1.2-rev1 returns a single enum, v1.3-rev1+ returns a list)"
    )
    # Old pattern that directly iterates the status (breaks for str-enum) must be gone.
    assert "[s.value for s in extent.status]" not in content, (
        "__init__.py must not iterate directly over extent.status — "
        "that fails when status is a str-subclass enum (v1.2-rev1)"
    )


def test_null_value_patch_is_applied_before_any_request():
    """Test that the null-tolerance patch is applied during setup (issues #82, #83).

    VBR sends nulls where the schema promises values, which makes the generated models
    reject the whole response. The patch must land before the first API call, or the first
    refresh still loses the data. (The patch itself is tested in test_sdk_patches.py.)
    """
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"
    content = init_path.read_text(encoding="utf-8")

    assert "from .sdk_patches import" in content

    prepare = content[content.index("def _prepare_sdk") :]
    prepare = prepare[: prepare.index("\nasync def ")]
    assert "patch_null_values_in_models(" in prepare

    # Patching imports modules, which blocks: it runs in an executor, before connecting
    setup = content[content.index("async def async_setup_entry") :]
    executor = setup.index("async_add_executor_job(_prepare_sdk")
    connect = setup.index("await veeam_client.connect()")
    assert executor < connect


def test_config_flow_does_not_import_on_the_event_loop():
    """Test that the config flow pre-imports the SDK off the loop (issue #82).

    VeeamClient.connect() resolves the versioned SDK with importlib at call time, which
    Home Assistant reports as a blocking call when awaited directly from the flow.
    """
    from pathlib import Path

    config_flow_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "config_flow.py"
    )

    with open(config_flow_path) as f:
        content = f.read()

    assert (
        "async_add_executor_job(_load_veeam_br" in content
    ), "veeam_br should be imported in an executor, not on the event loop"

    # Everything connect() imports dynamically must be pre-imported there
    loader = content[
        content.index("def _load_veeam_br") : content.index("async def validate_input")
    ]
    for module in ("client", "api.login.create_token", "models.token_login_spec"):
        assert module in loader, f"_load_veeam_br should pre-import {module}"

    # The plain import must not sit in the coroutine any more
    validate = content[content.index("async def validate_input") :]
    validate = validate[: validate.index("\nclass ")]
    assert (
        "from veeam_br.client import VeeamClient" not in validate
    ), "importing veeam_br inside validate_input puts a blocking import on the loop"


def test_devices_are_distinguishable_across_servers():
    """Test that device names identify their server (issue #82).

    With two entries configured, a hardcoded or "Unknown" device name appears twice and
    the user cannot tell the servers apart. Names are built in entity.py.
    """
    from pathlib import Path

    entity_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "entity.py"
    content = entity_path.read_text(encoding="utf-8")

    license_block = content[content.index("def license_device_info") :]
    license_block = license_block[: license_block.index("\n\n\ndef ")]
    assert "server_label(entry, data)" in license_block, "the license is named per server"

    label = content[content.index("def server_label") :]
    label = label[: label.index("\n\n\ndef ")]
    assert "CONF_HOST" in label, "falls back to the configured host, not a shared 'Unknown'"


def _load_const():
    """Load const.py standalone.

    const.py imports only the standard library and veeam-br's version table, so it can be
    loaded without Home Assistant installed (unlike the rest of the integration package).
    """
    import importlib.util
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "const.py"
    spec = importlib.util.spec_from_file_location("veeam_br_const", const_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_api_1_3_rev2_supported():
    """Test that API version 1.3-rev2 (Veeam B&R 13.1) is supported and is the default."""
    const = _load_const()

    assert const.API_VERSIONS["1.3-rev2"] == "v1_3_rev2"
    assert const.DEFAULT_API_VERSION == "1.3-rev2", "Default API version should be the newest"
    assert const.DEFAULT_API_MODULE == "v1_3_rev2"


def test_manifest_requires_a_recent_enough_veeam_br():
    """Test that the manifest's veeam-br floor covers what the integration relies on."""
    import json
    from pathlib import Path
    import re

    manifest_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "manifest.json"
    )

    with open(manifest_path) as f:
        manifest = json.load(f)

    requirement = next(r for r in manifest["requirements"] if r.startswith("veeam-br"))

    # Floors we depend on, newest first:
    #   0.5.1 — veeam_br.exceptions (tell a refused password from a dropped session), the
    #           VeeamClient timeout, and client.close()
    #   0.5.0 — veeam_br.discovery.detect_rest_api, used to name the port that answered
    #   0.4.0 — veeam_br.discovery.detect_api_version, used to auto-detect the version
    #   0.3.1 — VeeamClient recovers from a refused token refresh instead of leaving a
    #           dead session behind (issue #82)
    #   0.3.0 — first release shipping the v1_3_rev2 SDK
    minimum = (0, 5, 1)

    match = re.search(r">=\s*(\d+)\.(\d+)\.(\d+)", requirement)
    assert match, f"veeam-br requirement should pin a minimum version, got {requirement}"

    floor = tuple(int(part) for part in match.groups())
    assert floor >= minimum, (
        f"veeam-br requirement floor {'.'.join(map(str, floor))} is below the "
        f"{'.'.join(map(str, minimum))} that this integration relies on"
    )


def test_api_versions_discovery_is_sorted():
    """API versions are ordered oldest to newest, whatever order the SDK lists them in."""
    const = _load_const()

    versions = list(const.API_VERSIONS)
    assert versions == sorted(
        versions, key=lambda v: tuple(int(x) for x in v.replace("-rev", ".").split("."))
    )
    assert versions[-1] == const.DEFAULT_API_VERSION, "the default is the newest"


def test_api_versions_come_from_the_sdk():
    """The SDK's version table is the one list: no directory scan, no copy to keep in step."""
    from pathlib import Path

    const_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "const.py"
    content = const_path.read_text(encoding="utf-8")

    assert "from veeam_br.versions import VERSION_TO_PACKAGE" in content
    assert "FALLBACK_API_VERSIONS" not in content
    assert "os.listdir" not in content


def test_api_versions_cover_library():
    """Every version the installed veeam-br ships is offered, with the right package."""
    from veeam_br.versions import VERSION_TO_PACKAGE

    const = _load_const()

    library_versions = {
        version: package.rsplit(".", 1)[-1] for version, package in VERSION_TO_PACKAGE.items()
    }
    assert const.API_VERSIONS == library_versions
