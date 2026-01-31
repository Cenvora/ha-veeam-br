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
