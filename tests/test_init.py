"""Test Veeam Backup & Replication integration setup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant

from custom_components.veeam_br import async_setup_entry, async_unload_entry
from custom_components.veeam_br.const import (
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
)


@pytest.fixture
def mock_config_entry():
    """Create a mock config entry."""
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain="veeam_br",
        title="Veeam B&R (veeam.example.com)",
        data={
            CONF_HOST: "veeam.example.com",
            CONF_PORT: DEFAULT_PORT,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            CONF_API_VERSION: DEFAULT_API_VERSION,
        },
        options={},
        source="user",
        unique_id="veeam.example.com:9419",
        entry_id="test_entry_id",
    )


@pytest.fixture
def mock_veeam_api_responses():
    """Create mock responses for Veeam API calls."""
    # Mock jobs response
    jobs_result = MagicMock()
    jobs_result.data = [
        MagicMock(
            id="job1",
            name="Test Job 1",
            type_=MagicMock(value="Backup"),
            status=MagicMock(value="Success"),
            last_result=MagicMock(value="Success"),
            last_run=None,
            next_run=None,
        )
    ]

    # Mock server info response
    server_info = MagicMock()
    server_info.vbr_id = "server123"
    server_info.name = "Veeam Server"
    server_info.build_version = "12.0.0.1000"
    server_info.patches = []
    server_info.database_vendor = "Microsoft SQL Server"
    server_info.sql_server_edition = "Standard"
    server_info.sql_server_version = "2019"
    server_info.database_schema_version = "1.0"
    server_info.database_content_version = "1.0"
    server_info.platform = MagicMock(value="Windows")

    return {
        "jobs": jobs_result,
        "server_info": server_info,
        "repositories": MagicMock(data=[]),
        "repositories_states": MagicMock(data=[]),
        "sobr": MagicMock(data=[]),
        "license": None,
    }


async def test_setup_entry_success(hass: HomeAssistant, mock_config_entry, mock_veeam_api_responses):
    """Test successful setup of the integration."""
    with patch("custom_components.veeam_br.VeeamClient") as mock_client_class, patch(
        "custom_components.veeam_br.importlib.import_module"
    ) as mock_import:
        # Setup mocks
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.call = AsyncMock(
            side_effect=[
                mock_veeam_api_responses["jobs"],  # get_all_jobs_states
                mock_veeam_api_responses["server_info"],  # get_server_info
                None,  # get_installed_license (returns None)
                mock_veeam_api_responses["repositories"],  # get_all_repositories
                mock_veeam_api_responses["repositories_states"],  # get_all_repositories_states
                mock_veeam_api_responses["sobr"],  # get_all_scale_out_repositories
            ]
        )
        mock_client.api = MagicMock(
            return_value=MagicMock(
                get_all_jobs_states=MagicMock(),
                get_server_info=MagicMock(),
                get_installed_license=MagicMock(),
                get_all_repositories=MagicMock(),
                get_all_repositories_states=MagicMock(),
                get_all_scale_out_repositories=MagicMock(),
            )
        )
        mock_client_class.return_value = mock_client

        # Mock types module
        mock_types = MagicMock()
        mock_types.UNSET = MagicMock()
        mock_import.return_value = mock_types

        # Mock platform setup
        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"
        ) as mock_forward:
            result = await async_setup_entry(hass, mock_config_entry)

            assert result is True
            assert hasattr(mock_config_entry, "runtime_data")
            assert "coordinator" in mock_config_entry.runtime_data
            assert "veeam_client" in mock_config_entry.runtime_data

            # Verify client connection was called
            mock_client.connect.assert_called_once()

            # Verify platforms were set up
            assert mock_forward.called


async def test_setup_entry_connection_failed(
    hass: HomeAssistant, mock_config_entry, caplog: pytest.LogCaptureFixture
):
    """Test setup failure when connection fails."""
    with patch("custom_components.veeam_br.VeeamClient") as mock_client_class, patch(
        "custom_components.veeam_br.importlib.import_module"
    ):
        # Setup mocks
        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=Exception("Connection failed"))
        mock_client_class.return_value = mock_client

        result = await async_setup_entry(hass, mock_config_entry)

        assert result is False
        assert "Failed to connect to Veeam API" in caplog.text


async def test_setup_entry_import_failed(
    hass: HomeAssistant, mock_config_entry, caplog: pytest.LogCaptureFixture
):
    """Test setup failure when importing veeam_br fails."""
    with patch(
        "custom_components.veeam_br.importlib.import_module",
        side_effect=ImportError("Failed to import"),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

        assert result is False
        assert "Failed to import veeam_br types" in caplog.text


async def test_unload_entry(hass: HomeAssistant, mock_config_entry, mock_veeam_api_responses):
    """Test successful unload of the integration."""
    with patch("custom_components.veeam_br.VeeamClient") as mock_client_class, patch(
        "custom_components.veeam_br.importlib.import_module"
    ) as mock_import:
        # Setup mocks for setup
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.call = AsyncMock(
            side_effect=[
                mock_veeam_api_responses["jobs"],
                mock_veeam_api_responses["server_info"],
                None,
                mock_veeam_api_responses["repositories"],
                mock_veeam_api_responses["repositories_states"],
                mock_veeam_api_responses["sobr"],
            ]
        )
        mock_client.api = MagicMock(
            return_value=MagicMock(
                get_all_jobs_states=MagicMock(),
                get_server_info=MagicMock(),
                get_installed_license=MagicMock(),
                get_all_repositories=MagicMock(),
                get_all_repositories_states=MagicMock(),
                get_all_scale_out_repositories=MagicMock(),
            )
        )
        mock_client_class.return_value = mock_client

        mock_types = MagicMock()
        mock_types.UNSET = MagicMock()
        mock_import.return_value = mock_types

        # Setup entry first
        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups"
        ) as mock_forward, patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            return_value=True,
        ) as mock_unload:
            await async_setup_entry(hass, mock_config_entry)

            # Now test unload
            result = await async_unload_entry(hass, mock_config_entry)

            assert result is True
            mock_unload.assert_called_once()


async def test_update_listener(hass: HomeAssistant, mock_config_entry):
    """Test update listener reloads the entry."""
    from custom_components.veeam_br import update_listener

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload"
    ) as mock_reload:
        await update_listener(hass, mock_config_entry)
        mock_reload.assert_called_once_with(mock_config_entry.entry_id)
