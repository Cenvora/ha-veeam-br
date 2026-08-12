"""Config flow for Veeam Backup & Replication integration."""

from __future__ import annotations

import importlib
import logging
import sys
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv, selector
import voluptuous as vol

from .const import (
    API_VERSIONS,
    AUTO_API_VERSION,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_MODULE,
    DEFAULT_API_VERSION,
    DEFAULT_PORT,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)
from .sdk_patches import patch_models as patch_null_values_in_models

_LOGGER = logging.getLogger(__name__)


class WrongPortError(ConnectionError):
    """The configured port did not answer, but another REST API port did.

    Carries the port that answered so the form can name it.
    """

    def __init__(self, port: int) -> None:
        super().__init__(f"The REST API answered on port {port}, not the configured port")
        self.port = port


def _get_api_version_selector_config(
    preferred_version: str | None = None,
) -> tuple[list[str], str]:
    """Get API version options and default for selector.

    AUTO_API_VERSION leads the list and is the default, so the common case is not asking
    the user to know which revision their server speaks.
    """
    api_version_options = [AUTO_API_VERSION, *API_VERSIONS.keys()]

    if preferred_version and preferred_version in api_version_options:
        return api_version_options, preferred_version

    return api_version_options, AUTO_API_VERSION


async def async_resolve_api_version(data: dict[str, Any]) -> str:
    """Resolve the configured API version, detecting it when set to auto.

    Detection probes the server's Swagger documents (see veeam_br.discovery) and needs no
    credentials, so it runs before the connection is validated. It is best-effort: a server
    with Swagger disabled or gated reports nothing, and the static default is used instead
    of failing setup.
    """
    api_version = data.get(CONF_API_VERSION, AUTO_API_VERSION)
    if api_version != AUTO_API_VERSION:
        return api_version

    from veeam_br.discovery import detect_api_version

    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"
    verify_ssl = data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    try:
        detected = await detect_api_version(
            base_url,
            verify_ssl=verify_ssl,
            versions=list(API_VERSIONS),
        )
    except Exception as err:  # noqa: BLE001 - detection must never fail the flow
        _LOGGER.debug("API version detection failed: %s", err)
        detected = None

    if detected is None:
        _LOGGER.info(
            "Could not detect the API version of %s; using %s. Select a version manually "
            "if this server needs a different one",
            data[CONF_HOST],
            DEFAULT_API_VERSION,
        )
        return DEFAULT_API_VERSION

    _LOGGER.info("Detected API version %s on %s", detected, data[CONF_HOST])
    return detected


async def async_find_working_port(data: dict[str, Any], configured_port: int) -> int | None:
    """Return another port the REST API answers on, or None.

    Veeam B&R 13.1 moved the REST API to 443 and will eventually drop 9419, so "cannot
    connect" is now quite often the wrong port rather than a wrong host or a firewall. Worth
    one extra probe to be able to say which.
    """
    try:
        # Guarded with the probe itself: this runs inside validate_input's failure handler,
        # so an ImportError escaping here would replace the real connection error with
        # "unknown". The manifest floors veeam-br at 0.5.0, but a hand-installed older copy
        # should degrade to the generic error, not a misleading one.
        from veeam_br.discovery import DEFAULT_PORTS, detect_rest_api

        others = [port for port in DEFAULT_PORTS if port != configured_port]
        if not others:
            return None

        endpoint = await detect_rest_api(
            data[CONF_HOST],
            ports=others,
            versions=list(API_VERSIONS),
            verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
    except Exception as err:  # noqa: BLE001 - a failed probe just means no advice to give
        _LOGGER.debug("Port probe failed: %s", err)
        return None

    return endpoint.port if endpoint else None


def _load_veeam_br(api_version: str):
    """Import veeam_br and everything connect() imports dynamically, then patch models.

    VeeamClient.connect() resolves the versioned SDK with importlib at call time. Left to
    itself that lands on the event loop, which Home Assistant reports as a blocking call,
    so every module it needs is imported here instead — this runs in an executor. Importing
    a model imports the whole models package, so patching it costs nothing extra.
    """
    from veeam_br.client import VeeamClient

    api_module = API_VERSIONS.get(api_version, DEFAULT_API_MODULE)
    package = f"veeam_br.{api_module}"

    for module in (
        f"{package}.client",
        f"{package}.api.login.create_token",
        f"{package}.models.token_login_spec",
        f"{package}.models.e_login_grant_type",
    ):
        importlib.import_module(module)

    models_package = f"{package}.models"
    patch_null_values_in_models(
        models_package, importlib.import_module(f"{package}.types").UNSET, sys.modules
    )

    return VeeamClient


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect.

    Mutates data[CONF_API_VERSION] when it is set to auto, so the caller stores the resolved
    version rather than the sentinel: a server upgrade must not silently move an existing
    entry onto a newer revision, where enum values are renamed and fields added.
    """
    api_version = await async_resolve_api_version(data)
    data[CONF_API_VERSION] = api_version

    try:
        VeeamClient = await hass.async_add_executor_job(_load_veeam_br, api_version)
    except ImportError as err:
        _LOGGER.error("Error importing veeam_br: %s", err)
        raise ConnectionError("Failed to import veeam_br modules") from err

    base_url = f"https://{data[CONF_HOST]}:{data[CONF_PORT]}"

    try:
        vc = VeeamClient(
            host=base_url,
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            api_version=api_version,
            verify_ssl=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        await vc.connect()
    except PermissionError:
        raise
    except Exception as err:
        working_port = await async_find_working_port(data, data[CONF_PORT])
        if working_port is not None:
            _LOGGER.warning(
                "Could not reach the Veeam REST API on %s:%s, but it answered on port %s",
                data[CONF_HOST],
                data[CONF_PORT],
                working_port,
            )
            raise WrongPortError(working_port) from err
        raise ConnectionError(f"Failed to connect: {err}") from err

    return {"title": f"Veeam B&R ({data[CONF_HOST]})"}


class VeeamBRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Veeam Backup & Replication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "VeeamBROptionsFlow":
        return VeeamBROptionsFlow()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        wrong_port: int | None = None
        reconf_entry = self._get_reconfigure_entry()

        if user_input is not None:
            # Merge with existing config data
            data = {
                **reconf_entry.data,
                CONF_HOST: user_input[CONF_HOST],
                CONF_PORT: user_input[CONF_PORT],
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            }

            try:
                await validate_input(self.hass, data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reconf_entry,
                    data=data,
                    reason="reconfigure_successful",
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=reconf_entry.data.get(CONF_HOST)): cv.string,
                    vol.Required(
                        CONF_PORT, default=reconf_entry.data.get(CONF_PORT, DEFAULT_PORT)
                    ): cv.port,
                    vol.Required(
                        CONF_USERNAME, default=reconf_entry.data.get(CONF_USERNAME)
                    ): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                    vol.Optional(
                        CONF_VERIFY_SSL,
                        default=reconf_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    ): cv.boolean,
                }
            ),
            errors=errors,
            description_placeholders={
                "host": reconf_entry.data.get(CONF_HOST),
                "wrong_port": str(wrong_port or ""),
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth upon API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm reauth dialog."""
        errors: dict[str, str] = {}
        wrong_port: int | None = None
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            # Merge with existing config data
            data = {
                **reauth_entry.data,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }

            try:
                await validate_input(self.hass, data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data=data,
                    reason="reauth_successful",
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=reauth_entry.data.get(CONF_USERNAME)
                    ): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                }
            ),
            errors=errors,
            description_placeholders={
                "host": reauth_entry.data[CONF_HOST],
                "wrong_port": str(wrong_port or ""),
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        wrong_port: int | None = None

        if user_input is not None:
            await self.async_set_unique_id(f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}")
            self._abort_if_unique_id_configured()

            try:
                info = await validate_input(self.hass, user_input)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=info["title"], data=user_input)

        api_version_options, api_version_default = _get_api_version_selector_config(
            user_input.get(CONF_API_VERSION) if user_input else None
        )

        # Preserve user input on validation failure (except password for security)
        host_default = user_input[CONF_HOST] if user_input else vol.UNDEFINED
        port_default = user_input.get(CONF_PORT, DEFAULT_PORT) if user_input else DEFAULT_PORT
        username_default = user_input[CONF_USERNAME] if user_input else vol.UNDEFINED
        verify_ssl_default = (
            user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
            if user_input
            else DEFAULT_VERIFY_SSL
        )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=host_default): cv.string,
                vol.Required(CONF_PORT, default=port_default): cv.port,
                vol.Required(CONF_USERNAME, default=username_default): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_VERIFY_SSL, default=verify_ssl_default): cv.boolean,
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

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={"wrong_port": str(wrong_port or "")},
        )


class VeeamBROptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Veeam Backup & Replication integration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        wrong_port: int | None = None

        if user_input is not None:
            test_data = {**self.config_entry.data, CONF_API_VERSION: user_input[CONF_API_VERSION]}

            try:
                await validate_input(self.hass, test_data)
            except PermissionError:
                errors["base"] = "invalid_auth"
            except WrongPortError as err:
                # Subclasses ConnectionError, so it has to be caught before it
                errors["base"] = "wrong_port"
                wrong_port = err.port
            except ConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception validating options")
                errors["base"] = "unknown"
            else:
                # validate_input resolved auto into a concrete version; store that rather
                # than the sentinel, so the entry keeps talking the same revision after a
                # server upgrade
                return self.async_create_entry(
                    title="",
                    data={**user_input, CONF_API_VERSION: test_data[CONF_API_VERSION]},
                )

        api_version_options = [AUTO_API_VERSION, *API_VERSIONS.keys()]

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

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
            description_placeholders={"wrong_port": str(wrong_port or "")},
        )
