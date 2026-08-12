"""Resolving which REST API version to talk to a server with.

The API Version option can hold AUTO_API_VERSION, which is an intent rather than a version:
"use the newest revision this server serves". It is stored as-is and resolved during setup, so
a server upgrade — or a veeam-br release that adds a newer revision — is picked up on the next
restart without anyone editing the entry.

Shared by the config flow, which resolves it to validate the connection, and by setup, which
resolves it for real on every start.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PORT

from .const import (
    API_VERSIONS,
    AUTO_API_VERSION,
    CONF_API_VERSION,
    CONF_VERIFY_SSL,
    DEFAULT_API_VERSION,
    DEFAULT_VERIFY_SSL,
)

_LOGGER = logging.getLogger(__name__)


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
