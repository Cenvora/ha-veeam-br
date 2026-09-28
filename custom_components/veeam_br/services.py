"""Service actions: settling a move/copy backup session that awaits a decision (1.3-rev2)."""

from __future__ import annotations

import importlib
import logging
from uuid import UUID

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .button import _rejection
from .const import API_VERSIONS, DOMAIN, FEATURE_MANAGE_MOVE_COPY, check_api_feature_availability
from .display import describe_error
from .move_copy import (
    ACTIONS,
    ATTR_ACTION,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_SESSION_ID,
    SERVICE_MANAGE_MOVE_COPY,
)

_LOGGER = logging.getLogger(__name__)

MANAGE_MOVE_COPY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_SESSION_ID): cv.string,
        vol.Required(ATTR_ACTION): vol.In(list(ACTIONS)),
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's service actions, once for every config entry."""

    async def manage_move_copy_session(call: ServiceCall) -> ServiceResponse:
        entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": call.data[ATTR_CONFIG_ENTRY_ID]},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"title": entry.title},
            )

        api_version = entry.runtime_data["api_version"]
        if not check_api_feature_availability(api_version, FEATURE_MANAGE_MOVE_COPY):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="move_copy_unsupported",
                translation_placeholders={"api_version": api_version},
            )
        try:
            session_id = UUID(call.data[ATTR_SESSION_ID].strip())
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_session_id",
                translation_placeholders={"session_id": call.data[ATTR_SESSION_ID]},
            ) from err

        action = call.data[ATTR_ACTION]
        # Already imported during setup, so this is a lookup rather than a disk read
        models = importlib.import_module(f"veeam_br.{API_VERSIONS[api_version]}.models")
        body = models.ManageMoveCopySessionSpec(
            action=models.EMoveCopySessionAction(ACTIONS[action])
        )
        veeam_client = entry.runtime_data["veeam_client"]
        what = action.replace("_", " ")
        target = f"move/copy session {session_id}"

        try:
            result = await veeam_client.call(
                veeam_client.api("backups").manage_move_copy_session,
                session_id=session_id,
                body=body,
            )
        except Exception as err:
            _LOGGER.error("Failed to %s %s: %s", what, target, describe_error(err))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_failed",
                translation_placeholders={
                    "action": what,
                    "target": target,
                    "error": describe_error(err),
                },
            ) from err

        reason = _rejection(result)
        if reason is not None:
            _LOGGER.error("Veeam refused to %s %s: %s", what, target, reason)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="action_rejected",
                translation_placeholders={"action": what, "target": target, "error": reason},
            )

        _LOGGER.info("Requested: %s %s", what, target)
        await entry.runtime_data["coordinator"].async_request_refresh()

        # The server starts a new session to carry out the action
        new_id = getattr(result, "id", None)
        state = getattr(result, "state", None)
        return {
            "session_id": str(new_id) if new_id else None,
            "state": getattr(state, "value", state) if state else None,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_MANAGE_MOVE_COPY,
        manage_move_copy_session,
        schema=MANAGE_MOVE_COPY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
