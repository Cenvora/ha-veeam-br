"""Token management for Veeam Backup & Replication integration."""

from __future__ import annotations

from datetime import datetime, timedelta
import importlib
import logging
from typing import Any

from .const import API_VERSIONS

_LOGGER = logging.getLogger(__name__)


class VeeamTokenManager:
    """Manage Veeam API tokens with automatic refresh."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        api_version: str = "1.3-rev1",
    ):
        """Initialize the token manager.

        Args:
            base_url: Base URL of the Veeam server (e.g., https://veeam.example.com:9419)
            username: Veeam username for authentication
            password: Veeam password for authentication
            verify_ssl: Whether to verify SSL certificates
            api_version: API version to use
        """
        self._base_url = base_url
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._api_version = api_version
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: datetime | None = None
        self._authenticated_client = None

    async def ensure_valid_token(self, hass) -> bool:
        """Ensure we have a valid access token.

        Args:
            hass: Home Assistant instance

        Returns:
            bool: True if token is valid or successfully refreshed/obtained
        """
        # Check if we need to refresh the token
        if self._needs_refresh():
            return await self._refresh_or_authenticate(hass)
        return True

    def _needs_refresh(self) -> bool:
        """Check if token needs to be refreshed.

        Returns:
            bool: True if token needs refresh
        """
        if not self._access_token:
            return True

        if not self._token_expires_at:
            return True

        # Refresh 1 minute before expiry to avoid edge cases
        buffer_time = timedelta(minutes=1)
        return datetime.now() >= (self._token_expires_at - buffer_time)

    async def _refresh_or_authenticate(self, hass) -> bool:
        """Refresh token or re-authenticate if refresh fails.

        Args:
            hass: Home Assistant instance

        Returns:
            bool: True if successful
        """
        # Try to refresh using refresh_token if available
        if self._refresh_token:
            _LOGGER.debug("Attempting to refresh access token using refresh_token")
            if await self._refresh_token_async(hass):
                _LOGGER.debug("Successfully refreshed access token")
                return True
            _LOGGER.warning("Token refresh failed, falling back to username/password")

        # Fall back to username/password authentication
        _LOGGER.debug("Authenticating with username/password")
        return await self._authenticate_async(hass)

    async def _refresh_token_async(self, hass) -> bool:
        """Refresh the access token using refresh_token.

        Args:
            hass: Home Assistant instance

        Returns:
            bool: True if successful
        """
        try:
            api_module = API_VERSIONS.get(self._api_version, "v1_3_rev1")

            # Dynamic imports based on API version
            client_module = importlib.import_module(f"veeam_br.{api_module}")
            login_module = importlib.import_module(f"veeam_br.{api_module}.api.login")
            models_module = importlib.import_module(
                f"veeam_br.{api_module}.models.e_login_grant_type"
            )
            token_spec_module = importlib.import_module(
                f"veeam_br.{api_module}.models.token_login_spec"
            )

            Client = client_module.Client
            create_token = login_module.create_token
            ELoginGrantType = models_module.ELoginGrantType
            TokenLoginSpec = token_spec_module.TokenLoginSpec

            def _do_refresh():
                client = Client(base_url=self._base_url, verify_ssl=self._verify_ssl)
                body = TokenLoginSpec(
                    grant_type=ELoginGrantType.REFRESH_TOKEN,
                    refresh_token=self._refresh_token,
                )
                with client:
                    return create_token.sync(
                        client=client, body=body, x_api_version=self._api_version
                    )

            result = await hass.async_add_executor_job(_do_refresh)

            if result and result.access_token:
                self._access_token = result.access_token
                # Keep the existing refresh_token if new one is not provided
                if result.refresh_token:
                    self._refresh_token = result.refresh_token

                # Calculate expiration time (default 15 minutes = 900 seconds)
                expires_in = getattr(result, "expires_in", 900)
                self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)

                # Update authenticated client
                self._update_authenticated_client()
                return True

        except Exception as err:
            _LOGGER.error("Failed to refresh token: %s", err)

        return False

    async def _authenticate_async(self, hass) -> bool:
        """Authenticate with username/password.

        Args:
            hass: Home Assistant instance

        Returns:
            bool: True if successful
        """
        try:
            api_module = API_VERSIONS.get(self._api_version, "v1_3_rev1")

            # Dynamic imports based on API version
            client_module = importlib.import_module(f"veeam_br.{api_module}")
            login_module = importlib.import_module(f"veeam_br.{api_module}.api.login")
            models_module = importlib.import_module(
                f"veeam_br.{api_module}.models.e_login_grant_type"
            )
            token_spec_module = importlib.import_module(
                f"veeam_br.{api_module}.models.token_login_spec"
            )

            Client = client_module.Client
            create_token = login_module.create_token
            ELoginGrantType = models_module.ELoginGrantType
            TokenLoginSpec = token_spec_module.TokenLoginSpec

            def _do_auth():
                client = Client(base_url=self._base_url, verify_ssl=self._verify_ssl)
                body = TokenLoginSpec(
                    grant_type=ELoginGrantType.PASSWORD,
                    username=self._username,
                    password=self._password,
                )
                with client:
                    return create_token.sync(
                        client=client, body=body, x_api_version=self._api_version
                    )

            result = await hass.async_add_executor_job(_do_auth)

            if result and result.access_token:
                self._access_token = result.access_token
                self._refresh_token = getattr(result, "refresh_token", None)

                # Calculate expiration time (default 15 minutes = 900 seconds)
                expires_in = getattr(result, "expires_in", 900)
                self._token_expires_at = datetime.now() + timedelta(seconds=expires_in)

                # Update authenticated client
                self._update_authenticated_client()
                return True

        except Exception as err:
            _LOGGER.error("Failed to authenticate: %s", err)

        return False

    def _update_authenticated_client(self):
        """Update the authenticated client with the new token."""
        api_module = API_VERSIONS.get(self._api_version, "v1_3_rev1")
        client_module = importlib.import_module(f"veeam_br.{api_module}")
        AuthenticatedClient = client_module.AuthenticatedClient

        self._authenticated_client = AuthenticatedClient(
            base_url=self._base_url,
            token=self._access_token,
            verify_ssl=self._verify_ssl,
        )

    def get_authenticated_client(self):
        """Get the authenticated client for API calls.

        Returns:
            AuthenticatedClient: The authenticated client instance
        """
        return self._authenticated_client

    def get_token_info(self) -> dict[str, Any]:
        """Get current token information for debugging.

        Returns:
            dict: Token information
        """
        return {
            "has_access_token": bool(self._access_token),
            "has_refresh_token": bool(self._refresh_token),
            "expires_at": self._token_expires_at.isoformat() if self._token_expires_at else None,
            "needs_refresh": self._needs_refresh(),
        }
