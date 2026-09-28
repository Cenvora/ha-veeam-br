"""Common fixtures for Veeam Backup & Replication tests."""

import sys
from unittest.mock import patch

import pytest

# Modules some tests replace with stubs. Left in place, a stub leaks into every later test —
# and with veeam-br installed, a later import of the real module then fails.
_STUBBED_MODULES = ("veeam_br", "veeam_br.discovery")


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    """Put back any SDK module a test replaced in sys.modules."""
    saved = {name: sys.modules.get(name) for name in _STUBBED_MODULES}
    yield
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


@pytest.fixture(name="mock_setup_entry")
def mock_setup_entry_fixture():
    """Mock setup entry."""
    with patch(
        "custom_components.veeam_br.async_setup_entry",
        return_value=True,
    ) as mock_setup:
        yield mock_setup
