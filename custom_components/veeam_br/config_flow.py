"""Config flow for Veeam Backup & Replication integration."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector
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


def _get_api_version_selector_config(
    preferred_version: str | None = None,
) -> tuple[list[str], str]:
    """Get API version options and default for selector."""
    api_version_options = list(API_VERSIONS.keys())

    if preferred_version and preferred_version in api_version_options:
        return api_version_options, preferred_version

    if DEFAULT_API_VERSION in api_version_options:
        return api_version_options, DEFAULT_API_VERSION

    if api_version_options:
        return api_version_options, api_version_options[0]

    _LOGGER.error("No API versions available, using fallback")
    return [DEFAULT_API_VERSION], DEFAULT_API_VERSION


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    api_version = data.get(CONF_API_VERSION, DEFAULT_API_VERSION)
    api_module = API_VERSIONS.get(api_version, "v1_3_rev1")

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
        _LOGGER.error("Error importing veeam_br: %s", err)
        raise ConnectionError("Failed to import veeam_br modules") from err

    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"

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
            raise PermissionError("Authentication failed")

    except Exception as err:
        raise ConnectionError(f"Failed to connect: {err}") from err

    return {"title": f"Veeam BR ({data[CONF_HOST]})"}


class VeeamBRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veeam Backup & Replication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "VeeamBROptionsFlow":
        return VeeamBROptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        api_version_options, api_version_default = _get_api_version_selector_config()

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): cv.string,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
                vol.Optional(
                    CONF_API_VERSION, default=api_version_default
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=api_version_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)


class VeeamBROptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Veeam Backup & Replication integration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            test_data = {**self.config_entry.data, CONF_API_VERSION: user_input[CONF_API_VERSION]}

            try:
                await validate_input(self.hass, test_data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception validating options")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="", data=user_input)

        api_version_options = list(API_VERSIONS.keys())

        current_api_version = self.config_entry.options.get(
            CONF_API_VERSION,
            self.config_entry.data.get(CONF_API_VERSION, DEFAULT_API_VERSION),
        )

        if current_api_version not in api_version_options:
            _LOGGER.warning(
                "Stored API version %s is invalid, falling back to default",
                current_api_version,
            )
            current_api_version = DEFAULT_API_VERSION

        options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_VERSION, default=current_api_version
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=api_version_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema, errors=errors)
