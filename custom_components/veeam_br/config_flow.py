"""Config flow for Veeam Backup & Replication integration."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    API_VERSIONS,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    # Get API version from data or use default
    api_version = data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

    # Import the veeam_br library dynamically based on API version
    try:
        client_module = importlib.import_module(f"veeam_br.{api_module}")
        create_token_module = importlib.import_module(
            f"veeam_br.{api_module}.api.login.create_token"
        )
        models_module = importlib.import_module(f"veeam_br.{api_module}.models.e_login_grant_type")
        token_spec_module = importlib.import_module(
            f"veeam_br.{api_module}.models.token_login_spec"
        )

        Client = client_module.Client
        create_token = create_token_module
        ELoginGrantType = models_module.ELoginGrantType
        TokenLoginSpec = token_spec_module.TokenLoginSpec
    except ImportError as err:
        # Provide more specific feedback about what went wrong during import.
        error_message: str
        if isinstance(err, ModuleNotFoundError):
            missing_name = getattr(err, "name", "") or ""
            if missing_name == "veeam_br":
                error_message = "veeam_br library is not installed"
            elif missing_name.startswith(f"veeam_br.{api_module}"):
                error_message = f"API version {api_version} is not supported or not available"
            else:
                error_message = (
                    "A required veeam_br module could not be found " "(see logs for details)"
                )
        else:
            error_message = (
                "An unexpected import error occurred while loading the veeam_br library "
                "(see logs for details)"
            )

        _LOGGER.error(
            "Error importing veeam_br for API version %s: %s (%s)",
            api_version,
            error_message,
            err,
        )
        raise ConnectionError(error_message) from err
    # Construct base URL
    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"

    # Test connection by attempting to authenticate
    try:

        def _test_connection():
            client = Client(
                base_url=base_url, verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            )
            body = TokenLoginSpec(
                grant_type=ELoginGrantType.PASSWORD,
                username=data[CONF_USERNAME],
                password=data[CONF_PASSWORD],
            )
            with client:
                return create_token.sync(client=client, body=body, x_api_version=api_version)

        token_result = await hass.async_add_executor_job(_test_connection)

        if not token_result or not token_result.access_token:
            raise PermissionError("Authentication failed - no access token received")

    except PermissionError as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise
    except ConnectionError as err:
        _LOGGER.error("Failed to connect to Veeam server: %s", err)
        raise
    except Exception as err:
        _LOGGER.error("Unexpected error during connection test: %s", err)
        raise ConnectionError(f"Failed to connect: {err}") from err

    # Return info that you want to store in the config entry.
    return {"title": f"Veeam BR ({data[CONF_HOST]})"}


class VeeamBRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veeam Backup & Replication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> VeeamBROptionsFlow:
        """Get the options flow for this handler."""
        return VeeamBROptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Check if already configured
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        # Show the form
        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
                vol.Optional(CONF_API_VERSION, default=DEFAULT_API_VERSION): vol.In(
                    list(API_VERSIONS.keys())
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)


class VeeamBROptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Veeam Backup & Replication integration."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate the new API version by testing if it can connect
            # Create a test data dict with the new API version and existing config
            test_data = {**self.config_entry.data, CONF_API_VERSION: user_input[CONF_API_VERSION]}

            try:
                await validate_input(self.hass, test_data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception validating new API version")
                errors["base"] = "unknown"
            else:
                # Only update if validation passed - store in options
                return self.async_create_entry(title="", data=user_input)

        # Get current API version from config entry options or data
        current_api_version = self.config_entry.options.get(
            CONF_API_VERSION, self.config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
        )

        options_schema = vol.Schema(
            {
                vol.Required(CONF_API_VERSION, default=current_api_version): vol.In(
                    list(API_VERSIONS.keys())
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema, errors=errors)
