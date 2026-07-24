"""Client for Gardena Smart System API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp
from aiohttp import ClientTimeout

from .api_tracker import APIRequestTracker
from .auth import GardenaAuthError, GardenaAuthenticationManager
from .models import GardenaDataParser, GardenaLocation

_LOGGER = logging.getLogger(__name__)

# Constants
SMART_HOST = "https://api.smart.gardena.dev"
PRIVATE_HOST = "https://smart.gardena.com"
API_TIMEOUT = 30


class GardenaAPIError(Exception):
    """API error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.status_code = status_code


class GardenaCommandError(Exception):
    """Command-specific error."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        command_id: Optional[str] = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.status_code = status_code
        self.command_id = command_id


class _ShouldRetry(Exception):
    """Internal signal used to trigger an API retry."""

    def __init__(
        self,
        status_code: int,
        delay: float,
    ) -> None:
        """Initialize retry signal."""
        super().__init__(f"Retryable error {status_code}")
        self.status_code = status_code
        self.delay = delay


class GardenaSmartSystemClient:
    """Client for Gardena Smart System API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_key: Optional[str] = None,
        dev_mode: bool = False,
    ) -> None:
        """Initialize the client."""
        self.auth_manager = GardenaAuthenticationManager(
            client_id,
            client_secret,
            api_key,
            dev_mode,
        )

        self.api_tracker = APIRequestTracker()
        self.auth_manager.api_tracker = self.api_tracker

        self._dev_mode = dev_mode
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = ClientTimeout(total=API_TIMEOUT)

            connector = None

            # Handle SSL issues on macOS in development.
            if self._dev_mode:
                connector = aiohttp.TCPConnector(ssl=False)

            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )

        return self._session

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        is_command: bool = False,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Make authenticated request to Gardena API with retries.

        The retry loop lives outside the request lock so that
        asyncio.sleep during back-off never holds the lock.
        """

        for attempt in range(max_retries + 1):
            try:
                async with self._request_lock:
                    # Ensure we have a valid token.
                    await self.auth_manager.authenticate()

                    session = await self._get_session()
                    url = f"{SMART_HOST}/v2{endpoint}"
                    headers = self.auth_manager.get_auth_headers()

                    _LOGGER.debug(
                        "Making %s request to %s (attempt %d/%d)",
                        method,
                        url,
                        attempt + 1,
                        max_retries + 1,
                    )

                    async with session.request(
                        method,
                        url,
                        headers=headers,
                        json=data,
                    ) as response:
                        self.api_tracker.record(
                            method,
                            endpoint,
                            response.status,
                            source="command" if is_command else "client",
                        )

                        return await self._handle_response(
                            response,
                            is_command,
                            attempt,
                        )

            except _ShouldRetry as exc:
                if attempt >= max_retries:
                    _LOGGER.error(
                        "Server error %s after %d retries — giving up",
                        exc.status_code,
                        max_retries,
                    )

                    if is_command:
                        raise GardenaCommandError(
                            f"Server error: {exc.status_code}",
                            exc.status_code,
                        )

                    raise GardenaAPIError(
                        f"Server error: {exc.status_code}",
                        exc.status_code,
                    )

                _LOGGER.warning(
                    "Retryable HTTP %s — waiting %.1f seconds "
                    "before attempt %d/%d",
                    exc.status_code,
                    exc.delay,
                    attempt + 2,
                    max_retries + 1,
                )

                await asyncio.sleep(exc.delay)

            except GardenaAuthError:
                raise

            except GardenaCommandError:
                raise

            except GardenaAPIError:
                raise

            except aiohttp.ClientError as exc:
                _LOGGER.error(
                    "Network error during API request: %s",
                    exc,
                )

                raise GardenaAPIError(
                    f"Network error: {exc}"
                ) from exc

            except Exception as exc:
                _LOGGER.error(
                    "Unexpected API request error: %s",
                    exc,
                )

                raise GardenaAPIError(
                    str(exc)
                ) from exc

        raise GardenaAPIError(
            "Request failed after all retry attempts"
        )

    async def _handle_response(
        self,
        response: aiohttp.ClientResponse,
        is_command: bool,
        attempt: int,
    ) -> Dict[str, Any]:
        """Handle API response."""

        response_text = await response.text()

        _LOGGER.debug(
            "Response status: %s (empty body)",
            response.status,
            response_text,
        )

        # --------------------------------------------------------------
        # Successful responses
        # --------------------------------------------------------------

        if 200 <= response.status < 300:
            if not response_text:
                return {}

            try:
                return json.loads(response_text)

            except json.JSONDecodeError:
                _LOGGER.debug(
                    "Successful API response contained no valid JSON"
                )
                return {}

        # --------------------------------------------------------------
        # Command-specific errors
        # --------------------------------------------------------------

        if is_command:
            if response.status == 400:
                _LOGGER.error(
                    "Bad command request (400)"
                )
                raise GardenaCommandError(
                    "Invalid command parameters",
                    400,
                )

            if response.status == 403:
                _LOGGER.error(
                    "Command forbidden (403) - insufficient permissions"
                )
                raise GardenaCommandError(
                    "Command forbidden - check device permissions",
                    403,
                )

            if response.status == 404:
                _LOGGER.error(
                    "Service not found (404)"
                )
                raise GardenaCommandError(
                    "Service not found",
                    404,
                )

            if response.status == 409:
                _LOGGER.error(
                    "Command conflict (409) - device busy or invalid state"
                )
                raise GardenaCommandError(
                    "Command conflict - device may be busy",
                    409,
                )

        # --------------------------------------------------------------
        # Standard errors
        # --------------------------------------------------------------

        if response.status == 400:
            raise GardenaAPIError(
                "Bad request",
                400,
            )

        if response.status == 401:
            _LOGGER.error(
                "Authentication failed (401)"
            )

            raise GardenaAuthError(
                "Authentication failed"
            )

        if response.status == 403:
            _LOGGER.error(
                "Access denied (403)"
            )

            raise GardenaAPIError(
                "Access denied - check API key and permissions",
                403,
            )

        if response.status == 404:
            _LOGGER.error(
                "Resource not found (404)"
            )

            raise GardenaAPIError(
                "Resource not found",
                404,
            )

        if response.status == 429:
            retry_after = response.headers.get(
                "Retry-After"
            )

            try:
                delay = (
                    int(retry_after)
                    if retry_after
                    else 2 ** (attempt + 2)
                )
            except (TypeError, ValueError):
                delay = 2 ** (attempt + 2)

            _LOGGER.warning(
                "Rate limited (429), retrying in %ss. "
                "Consider reducing polling frequency or checking "
                "the API quota.",
                delay,
            )

            raise _ShouldRetry(
                429,
                delay,
            )

        if response.status in (
            500,
            502,
            503,
            504,
        ):
            raise _ShouldRetry(
                response.status,
                2 ** attempt,
            )

        # --------------------------------------------------------------
        # Other errors
        # --------------------------------------------------------------

        try:
            error_data = json.loads(response_text)

            if isinstance(error_data, dict):
                error_msg = error_data.get(
                    "message",
                    "Unknown error",
                )
            else:
                error_msg = str(error_data)

        except Exception:
            error_msg = (
                response_text
                or f"HTTP {response.status}"
            )

        _LOGGER.error(
            "API error %s: %s",
            response.status,
            error_msg,
        )

        raise GardenaAPIError(
            f"API error: {error_msg}",
            response.status,
        )

    async def get_locations(
        self,
    ) -> List[GardenaLocation]:
        """Get all locations."""

        _LOGGER.debug(
            "Fetching locations"
        )

        try:
            response = await self._make_request(
                "GET",
                "/locations",
            )

            locations = (
                GardenaDataParser.parse_locations_response(
                    response
                )
            )

            _LOGGER.debug(
                "Found %d locations",
                len(locations),
            )

            return locations

        except GardenaAPIError as exc:
            if exc.status_code == 404:
                _LOGGER.error(
                    "No locations found (404). "
                    "The user has no access to any location. "
                    "Please set up the Gardena Smart Gateway "
                    "in the official Gardena app first."
                )

            raise

    async def get_location(
        self,
        location_id: str,
    ) -> GardenaLocation:
        """Get specific location with devices."""

        _LOGGER.debug(
            "Fetching location %s",
            location_id,
        )

        try:
            response = await self._make_request(
                "GET",
                f"/locations/{location_id}",
            )

            location = (
                GardenaDataParser.parse_location_response(
                    response
                )
            )

            _LOGGER.debug(
                "Location %s fetched successfully with %d devices",
                location_id,
                len(location.devices),
            )

            return location

        except Exception as exc:
            _LOGGER.error(
                "Failed to fetch location %s: %s",
                location_id,
                exc,
            )
            raise

    async def send_command(
        self,
        service_id: str,
        command_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Send command to device service."""

        _LOGGER.debug(
            "Sending command to service %s: %s",
            service_id,
            command_data,
        )

        try:
            response = await self._make_request(
                "PUT",
                f"/command/{service_id}",
                data=command_data,
                is_command=True,
            )

            _LOGGER.debug(
                "Command sent successfully to service %s",
                service_id,
            )

            return response

        except GardenaCommandError as exc:
            _LOGGER.error(
                "Command error for service %s: %s",
                service_id,
                exc,
            )
            raise

        except Exception as exc:
            _LOGGER.error(
                "Failed to send command to service %s: %s",
                service_id,
                exc,
            )
            raise

    async def close(self) -> None:
        """Close the client."""

        _LOGGER.debug(
            "Closing Gardena Smart System client"
        )

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        await self.auth_manager.close()

    async def get_private_devices(
        self,
        location_id: str,
    ) -> Dict[str, Any]:
        """Get devices from Gardena private API."""

        # Ensure the access token is valid.
        await self.auth_manager.authenticate()

        session = await self._get_session()

        headers = {
            "Authorization": (
                f"Bearer {self.auth_manager._access_token}"
            ),
            "Authorization-Provider": "husqvarna",
            "Accept": "application/json",
        }

        url = (
            f"{PRIVATE_HOST}/v1/devices"
            f"?locationId={location_id}"
        )

        async with session.get(
            url,
            headers=headers,
        ) as response:
            text = await response.text()

            try:
                data = json.loads(text) if text else {}

            except json.JSONDecodeError:
                # Do not expose a raw invalid response because it may
                # contain sensitive data.
                if response.status != 200:
                    _LOGGER.error(
                        "Private API error status=%s "
                        "body=<invalid JSON omitted>",
                        response.status,
                    )
                else:
                    _LOGGER.debug(
                        "Private API returned invalid JSON; "
                        "body omitted"
                    )

                raise GardenaAPIError(
                    "Private API returned invalid JSON: "
                    f"{response.status}",
                    response.status,
                )

            # Keep only names of data removed from the response.
            # No removed values are stored.
            omitted_data: set[str] = set()

            # ----------------------------------------------------------
            # Filter unused / excessive Private API data.
            # ----------------------------------------------------------

            for device in data.get("devices", []):
                abilities = device.get(
                    "abilities",
                    [],
                )

                # ------------------------------------------------------
                # Remove schedule-related abilities.
                # ------------------------------------------------------

                if isinstance(abilities, list):
                    filtered_abilities = []

                    for ability in abilities:
                        if not isinstance(ability, dict):
                            filtered_abilities.append(ability)
                            continue

                        ability_name = ability.get("name")

                        if ability_name in {
                            "mower_timer",
                            "scheduling",
                            "scheduling_wizard_mowing",
                        }:
                            omitted_data.add(
                                str(ability_name)
                            )
                            continue

                        filtered_abilities.append(
                            ability
                        )

                    # --------------------------------------------------
                    # Remove zone_map from LONA.
                    # --------------------------------------------------

                    for ability in filtered_abilities:
                        if not isinstance(ability, dict):
                            continue

                        if ability.get("name") != "lona":
                            continue

                        properties = ability.get(
                            "properties",
                            [],
                        )

                        if not isinstance(properties, list):
                            continue

                        filtered_properties = []

                        for prop in properties:
                            if not isinstance(prop, dict):
                                filtered_properties.append(
                                    prop
                                )
                                continue

                            if prop.get("name") == "zone_map":
                                omitted_data.add(
                                    "zone_map"
                                )
                                continue

                            filtered_properties.append(
                                prop
                            )

                        ability["properties"] = (
                            filtered_properties
                        )

                    device["abilities"] = (
                        filtered_abilities
                    )

                # ------------------------------------------------------
                # Remove schedule-related constraints.
                # ------------------------------------------------------

                constraints = device.get(
                    "constraints",
                    [],
                )

                if isinstance(constraints, list):
                    filtered_constraints = []

                    for constraint in constraints:
                        if not isinstance(
                            constraint,
                            dict,
                        ):
                            filtered_constraints.append(
                                constraint
                            )
                            continue

                        resource_name = constraint.get(
                            "resource_name"
                        )

                        if resource_name in {
                            "scheduling_wizard_mowing",
                            "scheduled_events",
                            "adaptive_scheduling",
                        }:
                            omitted_data.add(
                                f"constraint:{resource_name}"
                            )
                            continue

                        filtered_constraints.append(
                            constraint
                        )

                    if filtered_constraints:
                        device["constraints"] = (
                            filtered_constraints
                        )
                    else:
                        device.pop(
                            "constraints",
                            None,
                        )

                # ------------------------------------------------------
                # Remove top-level schedule data.
                # ------------------------------------------------------

                for key in (
                    "scheduled_events",
                    "scheduling_wizard_mowing",
                    "scheduling_wizard_watering",
                ):
                    if key in device:
                        omitted_data.add(key)
                        device.pop(key, None)

                # ------------------------------------------------------
                # Remove PIN completely from settings.
                # ------------------------------------------------------

                settings = device.get(
                    "settings",
                    [],
                )

                if isinstance(settings, list):
                    filtered_settings = []

                    for setting in settings:
                        if not isinstance(setting, dict):
                            filtered_settings.append(
                                setting
                            )
                            continue

                        if setting.get("name") == "pin":
                            omitted_data.add("pin")
                            continue

                        filtered_settings.append(
                            setting
                        )

                    device["settings"] = (
                        filtered_settings
                    )

            # ----------------------------------------------------------
            # Error response
            # ----------------------------------------------------------

            if response.status != 200:
                _LOGGER.error(
                    "Private API error status=%s body=%s",
                    response.status,
                    json.dumps(
                        data,
                        ensure_ascii=False,
                    ),
                )

                if omitted_data:
                    _LOGGER.error(
                        "Private API response contained additional "
                        "data omitted from response/logs: %s",
                        ", ".join(
                            sorted(omitted_data)
                        ),
                    )

                raise GardenaAPIError(
                    f"Private API error: {response.status}",
                    response.status,
                )

            # ----------------------------------------------------------
            # Successful response — only log in DEBUG.
            # ----------------------------------------------------------

            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug(
                    "Private API response status=%s body=%s",
                    response.status,
                    json.dumps(
                        data,
                        ensure_ascii=False,
                    ),
                )

                if omitted_data:
                    _LOGGER.debug(
                        "Private API response contained additional "
                        "data omitted from response/logs: %s",
                        ", ".join(
                            sorted(omitted_data)
                        ),
                    )

            return data