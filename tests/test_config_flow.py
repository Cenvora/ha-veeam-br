"""Test Veeam Backup & Replication config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

from custom_components.veeam_br.const import (
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)


async def test_user_form(hass, mock_veeam_client, mock_setup_entry):
    """Test the user form to set up the integration."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    # Test successful config flow
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "veeam.example.com",
            CONF_PORT: DEFAULT_PORT,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            CONF_API_VERSION: DEFAULT_API_VERSION,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Veeam B&R (veeam.example.com)"
    assert result["data"] == {
        CONF_HOST: "veeam.example.com",
        CONF_PORT: DEFAULT_PORT,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "password",
        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
        CONF_API_VERSION: DEFAULT_API_VERSION,
    }

    assert len(mock_setup_entry.mock_calls) == 1


async def test_user_form_cannot_connect(hass, mock_veeam_client, mock_setup_entry):
    """Test handling connection error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Simulate connection failure
    mock_veeam_client.return_value.connect.side_effect = ConnectionError("Connection failed")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "veeam.example.com",
            CONF_PORT: DEFAULT_PORT,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            CONF_API_VERSION: DEFAULT_API_VERSION,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_form_invalid_auth(hass, mock_veeam_client, mock_setup_entry):
    """Test handling invalid authentication."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Simulate authentication failure
    mock_veeam_client.return_value.connect.side_effect = PermissionError("Invalid credentials")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "veeam.example.com",
            CONF_PORT: DEFAULT_PORT,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "wrong_password",
            CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            CONF_API_VERSION: DEFAULT_API_VERSION,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_form_unexpected_exception(hass, mock_veeam_client, mock_setup_entry):
    """Test handling unexpected exception."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Simulate unexpected exception
    mock_veeam_client.return_value.connect.side_effect = Exception("Unexpected error")

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "veeam.example.com",
            CONF_PORT: DEFAULT_PORT,
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "password",
            CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
            CONF_API_VERSION: DEFAULT_API_VERSION,
        },
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unknown"}


async def test_user_form_already_configured(hass, mock_veeam_client, mock_setup_entry):
    """Test handling already configured server."""
    # Create an existing entry
    entry = MagicMock()
    entry.unique_id = "veeam.example.com:9419"
    hass.config_entries.async_entries = MagicMock(return_value=[])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    # Configure with same host and port
    with patch.object(
        hass.config_entries.flow,
        "_async_current_ids",
        return_value={"veeam.example.com:9419"},
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "veeam.example.com",
                CONF_PORT: DEFAULT_PORT,
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "password",
                CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
                CONF_API_VERSION: DEFAULT_API_VERSION,
            },
        )

        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"


async def test_options_flow(hass, mock_veeam_client):
    """Test options flow."""
    # Create a config entry
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_HOST: "veeam.example.com",
        CONF_PORT: DEFAULT_PORT,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "password",
        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
        CONF_API_VERSION: DEFAULT_API_VERSION,
    }
    entry.options = {}

    # Mock the config entry
    with patch(
        "homeassistant.config_entries.ConfigFlow.async_create_entry",
        return_value={"type": FlowResultType.CREATE_ENTRY},
    ):
        from custom_components.veeam_br.config_flow import VeeamBROptionsFlow

        options_flow = VeeamBROptionsFlow()
        options_flow.hass = hass
        options_flow.config_entry = entry

        # Initialize options flow
        result = await options_flow.async_step_init()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "init"

        # Test changing API version
        new_api_version = "1.2-rev1"
        result = await options_flow.async_step_init(
            user_input={CONF_API_VERSION: new_api_version}
        )

        assert result["type"] == FlowResultType.CREATE_ENTRY
        assert result["data"] == {CONF_API_VERSION: new_api_version}


async def test_reauth_flow(hass, mock_veeam_client):
    """Test reauth flow."""
    # Create an existing entry
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_HOST: "veeam.example.com",
        CONF_PORT: DEFAULT_PORT,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "old_password",
        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
        CONF_API_VERSION: DEFAULT_API_VERSION,
    }

    # Mock the reauth flow
    from custom_components.veeam_br.config_flow import VeeamBRConfigFlow

    flow = VeeamBRConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}

    # Mock getting the reauth entry
    with patch.object(flow, "_get_reauth_entry", return_value=entry):
        # Initialize reauth flow
        result = await flow.async_step_reauth_confirm()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"

        # Test successful reauth
        with patch.object(
            flow, "async_update_reload_and_abort"
        ) as mock_update:
            mock_update.return_value = {
                "type": FlowResultType.ABORT,
                "reason": "reauth_successful",
            }

            result = await flow.async_step_reauth_confirm(
                user_input={
                    CONF_USERNAME: "admin",
                    CONF_PASSWORD: "new_password",
                }
            )

            assert mock_update.called
            # Verify the new password was used
            call_args = mock_update.call_args
            assert call_args[1]["data"][CONF_PASSWORD] == "new_password"


async def test_reauth_flow_invalid_auth(hass, mock_veeam_client):
    """Test reauth flow with invalid credentials."""
    # Create an existing entry
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_HOST: "veeam.example.com",
        CONF_PORT: DEFAULT_PORT,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: "old_password",
        CONF_VERIFY_SSL: DEFAULT_VERIFY_SSL,
        CONF_API_VERSION: DEFAULT_API_VERSION,
    }

    # Simulate authentication failure
    mock_veeam_client.return_value.connect.side_effect = PermissionError("Invalid credentials")

    from custom_components.veeam_br.config_flow import VeeamBRConfigFlow

    flow = VeeamBRConfigFlow()
    flow.hass = hass
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}

    with patch.object(flow, "_get_reauth_entry", return_value=entry):
        result = await flow.async_step_reauth_confirm(
            user_input={
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "wrong_password",
            }
        )

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        assert result["errors"] == {"base": "invalid_auth"}
