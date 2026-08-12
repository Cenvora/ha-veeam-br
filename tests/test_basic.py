"""Basic validation tests for Veeam BR integration."""

import pytest


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
    """Test that stale entity cleanup scans the registry directly (not just session-tracked IDs).

    The cleanup must scan the entity registry rather than comparing session-scoped
    tracking sets so that entities persisted from previous HA sessions are also
    removed when their corresponding job/repo/SOBR no longer exists.
    """
    from pathlib import Path

    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sensor.py"
    button_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "button.py"

    with open(sensor_path) as f:
        sensor_content = f.read()

    with open(button_path) as f:
        button_content = f.read()

    # The old approach iterated over stale_job_ids (session-scoped diff) and matched
    # entity unique_ids by substring. The new approach must scan the full registry
    # using async_entries_for_config_entry and compare against current API data.
    # Verify the new approach is used instead of the old set-difference pattern.
    assert (
        "stale_job_ids = current_job_ids - current_jobs_in_data" not in sensor_content
    ), "sensor.py should not use session-scoped set-difference for stale job detection"
    assert (
        "stale_repo_ids = current_repo_ids - current_repos_in_data" not in sensor_content
    ), "sensor.py should not use session-scoped set-difference for stale repo detection"
    assert (
        "stale_sobr_ids = current_sobr_ids - current_sobrs_in_data" not in sensor_content
    ), "sensor.py should not use session-scoped set-difference for stale SOBR detection"
    assert (
        "stale_job_ids = current_job_ids - current_jobs_in_data" not in button_content
    ), "button.py should not use session-scoped set-difference for stale job detection"

    # Verify that the cleanup uses entity registry scanning
    assert (
        "async_entries_for_config_entry" in sensor_content
    ), "sensor.py stale cleanup should scan the entity registry"
    assert (
        "async_entries_for_config_entry" in button_content
    ), "button.py stale cleanup should scan the entity registry"

    # Verify that device registry cleanup is present in sensor.py
    assert (
        "device_registry" in sensor_content or "dr.async_get" in sensor_content
    ), "sensor.py should clean up orphaned devices from the device registry"
    assert (
        "async_remove_device" in sensor_content
    ), "sensor.py should remove orphaned devices via device_registry.async_remove_device"


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
    """Test that Linux Hardened Repository immutability is extracted from makeRecentBackupsImmutableDays."""
    from pathlib import Path

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"

    with open(init_path) as f:
        content = f.read()

    # Verify HLR immutability logic is present
    assert (
        "makeRecentBackupsImmutableDays" in content
    ), "__init__.py should check makeRecentBackupsImmutableDays for Linux Hardened repos"
    # Verify that HLR check is guarded so it doesn't override S3 immutability
    assert (
        '"is_immutable" not in repo_dict' in content
    ), "HLR immutability check should only run when S3 immutability was not already found"


def test_api_v1_2_rev1_jobs_error_handling():
    """Test that jobs API errors are handled gracefully for v1.2-rev1 compatibility.

    In API v1.2-rev1 the veeam_br library may raise ValueError (from dict(string))
    when parsing some response fields. The integration must catch those errors so
    that setup succeeds instead of raising UpdateFailed with an opaque dict error.
    """
    from pathlib import Path
    import re

    init_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"

    with open(init_path) as f:
        content = f.read()

    # Jobs section must now be wrapped in its own try/except so that
    # ValueError (the exact dict-construction error reported by the user) and
    # KeyError / AttributeError / TypeError (other parsing failures) are caught
    # and logged rather than propagating to the outer UpdateFailed handler.
    # Use a regex to scope checks to the jobs error-handling block around the
    # "Failed to parse jobs API response" log message.
    jobs_error_match = re.search(
        r"(.{0,400}Failed to parse jobs API response.{0,400})",
        content,
        flags=re.DOTALL,
    )
    assert (
        jobs_error_match is not None
    ), "__init__.py should catch jobs API parsing errors and log them gracefully"
    jobs_error_block = jobs_error_match.group(1)

    # The per-job inner loop must also catch ValueError (e.g. unknown enum values)
    # not just AttributeError/TypeError as before.
    # Check that all four exception types are present within the jobs outer
    # try/except block, without requiring a specific tuple order.
    for exc_type in ("ValueError", "KeyError", "AttributeError", "TypeError"):
        assert (
            exc_type in jobs_error_block
        ), f"__init__.py jobs outer try/except should catch {exc_type}"


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

    with open(init_path) as f:
        content = f.read()

    assert (
        "from .sdk_patches import" in content
    ), "__init__.py should apply the model patches from sdk_patches"

    patch_call = content.index("patch_null_values_in_models(")
    connect_call = content.index("await veeam_client.connect()")
    assert patch_call < connect_call, (
        "models must be patched before the client connects, so no response is parsed by "
        "an unpatched model"
    )

    # Patching imports modules, which blocks; it must not run on the event loop.
    assert (
        "await asyncio.to_thread(patch_models)" in content
    ), "model patching does blocking imports and should run via asyncio.to_thread"


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
    the user cannot tell the servers apart.
    """
    from pathlib import Path

    sensor_path = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "sensor.py"

    with open(sensor_path) as f:
        content = f.read()

    assert (
        '"name": "Veeam License"' not in content
    ), "the license device name must be qualified per entry, not hardcoded"
    assert (
        'server_info.get("name", "Unknown") if server_info else "Unknown"' not in content
    ), "the server device should fall back to the configured host, not a shared 'Unknown'"
    assert (
        content.count("Veeam License (") == 1
    ), "the license device name should include the configured host"


def _load_const():
    """Load const.py standalone.

    const.py imports only the standard library, so it can be loaded without Home
    Assistant installed (unlike the rest of the integration package).
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

    assert (
        "1.3-rev2" in const.FALLBACK_API_VERSIONS
    ), "1.3-rev2 must be in the fallback API version list (Veeam B&R 13.1)"
    assert const.FALLBACK_API_VERSIONS["1.3-rev2"] == "v1_3_rev2"
    assert const.DEFAULT_API_VERSION == "1.3-rev2", "Default API version should be the newest"
    assert const.DEFAULT_API_MODULE == "v1_3_rev2"
    assert (
        const.DEFAULT_API_VERSION in const.FALLBACK_API_VERSIONS
    ), "DEFAULT_API_VERSION must be a known API version"


def test_manifest_requires_veeam_br_with_rev2():
    """Test that the manifest requires a veeam-br release that ships v1_3_rev2."""
    import json
    from pathlib import Path

    manifest_path = (
        Path(__file__).parent.parent / "custom_components" / "veeam_br" / "manifest.json"
    )

    with open(manifest_path) as f:
        manifest = json.load(f)

    requirement = next(r for r in manifest["requirements"] if r.startswith("veeam-br"))

    # v1_3_rev2 first shipped in veeam-br 0.3.0
    assert (
        ">=0.3.0" in requirement
    ), f"veeam-br requirement should be >=0.3.0 for 1.3-rev2 support, got {requirement}"


def test_api_versions_discovery_is_sorted(tmp_path, monkeypatch):
    """Test that discovered API versions are ordered oldest to newest.

    os.listdir order is filesystem-dependent, so the selector order (and the
    first-option fallback in the config flow) must not rely on it.
    """
    const = _load_const()

    # Fake veeam_br package directory, created in an order that is not the sorted order
    for name in (
        "v1_3_rev10",
        "v1_2_rev1",
        "v1_3_rev2",
        "v1_10_rev0",
        "v1_3_rev0",
        "not_a_version",
    ):
        (tmp_path / name).mkdir()

    class FakeSpec:
        submodule_search_locations = [str(tmp_path)]
        origin = None

    monkeypatch.setattr(const.importlib.util, "find_spec", lambda name: FakeSpec())

    versions = const._discover_api_versions()

    assert list(versions.keys()) == [
        "1.2-rev1",
        "1.3-rev0",
        "1.3-rev2",
        "1.3-rev10",
        "1.10-rev0",
    ], "API versions should be sorted numerically, and non-version directories ignored"


def test_api_versions_discovery_falls_back(monkeypatch):
    """Test that discovery falls back to the static list when veeam_br is unavailable."""
    const = _load_const()

    monkeypatch.setattr(const.importlib.util, "find_spec", lambda name: None)
    assert const._discover_api_versions() == const.FALLBACK_API_VERSIONS

    def boom(name):
        raise RuntimeError("broken package")

    monkeypatch.setattr(const.importlib.util, "find_spec", boom)
    assert const._discover_api_versions() == const.FALLBACK_API_VERSIONS


def test_fallback_api_versions_cover_library():
    """Test that the fallback list covers every version the installed veeam-br ships.

    The fallback may list newer revisions than an older installed library provides, but it
    must never omit one the library supports — otherwise that version is unselectable when
    package inspection fails.
    """
    try:
        from veeam_br.versions import VERSION_TO_PACKAGE
    except ImportError:
        pytest.skip("veeam-br not installed")

    const = _load_const()

    library_versions = {
        version: package.rsplit(".", 1)[-1] for version, package in VERSION_TO_PACKAGE.items()
    }
    missing = {
        version: module
        for version, module in library_versions.items()
        if const.FALLBACK_API_VERSIONS.get(version) != module
    }

    assert not missing, (
        f"FALLBACK_API_VERSIONS is missing versions shipped by veeam-br: {missing}. "
        "It should mirror veeam_br.versions.VERSION_TO_PACKAGE."
    )
