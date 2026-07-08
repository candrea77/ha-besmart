"""API client for the BeSMART cloud (Riello)."""
import asyncio
import logging
from urllib.parse import quote  # URL-encode credentials in login()

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


# pylint: disable=too-many-instance-attributes
class BesmartClient:
    """Client for the BeSMART cloud API."""

    BASE_URL = "https://api.besmart-home.com/BeSMART_release/v1/api/"
    TOKEN = "a69157a524fdcf0246a58fc5767683c700c5b7b4"
    LOGIN = "iOS/users/login_new?username={username}&password={password}"

    GET_WIFI_BOX_DATA = "Android/Wifi_boxes/data/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}"

    GET_THERMOSTAT_DATA = "Android/Thermostats/data/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}/thermostat_id/{thermostat}"
    GET_THERMOSTAT_PROGRAM = "Android/thermostats/program/user_id/{0}/wifi_box_id/{1}/thermostat_id/{2}/day/{3}/token/{4}"
    GET_THERMOSTAT_SETTINGS = "Android/thermostats/setting/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}/thermostat_id/{thermostat}"
    SET_THERMOSTAT_TEMP = "Android/Thermostats/temperature"
    SET_THERMOSTAT_ADVANCE = "Android/Thermostats/advance"
    SET_THERMOSTAT_MODE = "Android/Thermostats/mode"
    SET_THERMOSTAT_HOLIDAY_END_TIME = "Android/Thermostats/holiday_end_time"
    SET_THERMOSTAT_SETTINGS = "Android/Thermostats/setting"
    SET_THERMOSTAT_PROGRAM = "Android/Thermostats/program_196"

    GET_BOILER_DATA = "Android/Boilers/data/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}"
    SET_BOILER_MODE = "Android/Boilers/work_mode"
    SET_BOILER_DHW_TEMP = "Android/Boilers/dhw_target_temp"

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        verify_ssl: bool = True,
    ):
        """Initialize the client."""
        self._username = username
        self._password = password
        self._user = None
        self._timeout = 30
        # PATCH 0.5: lock to serialise login() when multiple write commands run
        # concurrently (e.g. two set_temperature from an automation).
        self._login_lock = asyncio.Lock()
        # verify_ssl is a config option. async_get_clientsession caches one
        # session per verify_ssl flag, so True reuses HA's shared verified
        # session and False uses an isolated no-verify session.
        self._session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    # ------------------------------------------------------------------ utils

    def _redact(self, text: str) -> str:
        """Strip credentials from any log/exception message.

        aiohttp exceptions (ClientResponseError, ClientConnectorError, ...)
        embed the full request URL in their message; the login URL contains
        username and password in the query string, so we must never log those
        messages verbatim.
        """
        for secret in (
            quote(self._password, safe=""),
            self._password,
            quote(self._username, safe=""),
            self._username,
        ):
            if secret:
                text = text.replace(secret, "***")
        return text

    # ------------------------------------------------------------------ login

    async def login(self):
        """Log in and return the list of wifi box ids."""
        url = self.BASE_URL + self.LOGIN.format(
            # URL-encode so passwords with & = + % # or spaces don't break the URL
            username=quote(self._username, safe=""),
            password=quote(self._password, safe=""),
        )
        try:
            # PATCH 0.5: the body read (res.json) is now inside the timeout
            # block too, so a server stalling mid-response cannot hang us.
            async with asyncio.timeout(self._timeout):
                res = await self._session.get(url)
                data = await res.json()

            # PATCH 0.5: compare as string so an int 6 is caught as well.
            if str(data.get("error_code")) == "6":
                raise ConfigEntryAuthFailed("Invalid credentials.")

            if not res.ok:
                res.raise_for_status()

            message = data.get("message") or {}
            self._user = message.get("user")
            if not self._user:
                raise RuntimeError("Login response did not contain user data")

            # PATCH 0.5: never log the full user payload (may contain e-mail
            # and other personal data); the user id is enough for debugging.
            _LOGGER.debug("login ok, user id: %s", self._user.get("id"))
            return [x.get("id") for x in (message.get("wifi_box") or [])]
        except ConfigEntryAuthFailed:
            # Don't swallow auth failures; let them propagate to trigger reauth.
            self._user = None
            raise
        except Exception as ex:
            self._user = None
            # PATCH 0.5: sanitized logging - never leak credentials from the
            # exception message (which may contain the login URL).
            _LOGGER.warning(
                "Login to BeSMART cloud failed: %s: %s",
                type(ex).__name__,
                self._redact(str(ex)),
            )
            raise

    async def _ensure_login(self):
        # PATCH 0.5: serialised to avoid concurrent double logins.
        async with self._login_lock:
            if not self._user:
                await self.login()

    async def ensure_login(self):
        """Public wrapper: ensure we are logged in.

        Used by the coordinator as a pre-flight so that ConfigEntryAuthFailed
        (raised by login() on error_code "6") propagates and triggers the HA
        reauth flow, instead of being swallowed as None by the resource methods.
        """
        await self._ensure_login()

    # ------------------------------------------------------------------ reads

    async def devices(self, wifi_box: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.get(
                    self.BASE_URL + self.GET_WIFI_BOX_DATA.format(
                        user=self._user.get("id"),
                        wifi_box=wifi_box,
                        token=self.TOKEN,
                    ),
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            # message may be None if the API answers with an empty payload
            message = data.get("message") or {}
            boiler = message.get("boiler")
            thermostats = [
                x for x in (message.get("thermostat") or []) if x.get("id") is not None
            ]

            _LOGGER.debug("boiler: %s", boiler)
            _LOGGER.debug("thermostats: %s", thermostats)
            return {"boiler": boiler, "thermostats": thermostats}
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in devices(): %s", self._redact(str(ex)))
            self._user = None  # force re-login on next cycle
            return None

    async def thermostat(self, wifi_box: str, thermostat: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.get(
                    self.BASE_URL + self.GET_THERMOSTAT_DATA.format(
                        user=self._user.get("id"),
                        wifi_box=wifi_box,
                        token=self.TOKEN,
                        thermostat=thermostat,
                    ),
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            _LOGGER.debug("thermostat data: %s", message)
            return message
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in thermostat(): %s", self._redact(str(ex)))
            self._user = None
            return None

    async def thermostatSettings(self, wifi_box: str, thermostat: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.get(
                    self.BASE_URL + self.GET_THERMOSTAT_SETTINGS.format(
                        user=self._user.get("id"),
                        wifi_box=wifi_box,
                        token=self.TOKEN,
                        thermostat=thermostat,
                    ),
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            _LOGGER.debug("thermostat settings: %s", message)
            return message
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in thermostatSettings(): %s", self._redact(str(ex)))
            self._user = None
            return None

    async def boiler(self, wifi_box: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.get(
                    self.BASE_URL + self.GET_BOILER_DATA.format(
                        user=self._user.get("id"),
                        wifi_box=wifi_box,
                        token=self.TOKEN,
                    ),
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            _LOGGER.debug("boiler data: %s", message)
            return message
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in boiler(): %s", self._redact(str(ex)))
            self._user = None
            return None

    # ----------------------------------------------------------------- writes

    async def setThermostatMode(self, wifi_box: str, thermostat: str, mode: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.put(
                    self.BASE_URL + self.SET_THERMOSTAT_MODE,
                    data={
                        "mode": mode,
                        "wifi_box_id": wifi_box,
                        "user_id": self._user.get("id"),
                        "thermostat_id": thermostat,
                        "id": self._user.get("id"),
                        "token": self.TOKEN,
                    },
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("thermostat set mode: %s", data)
            return True
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in setThermostatMode(): %s", self._redact(str(ex)))
            self._user = None
            return False

    async def setThermostatTemp(self, wifi_box: str, thermostat: str, temp: float, tempMode: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.put(
                    self.BASE_URL + self.SET_THERMOSTAT_TEMP,
                    data={
                        "fraction_part": round(temp % 1 * 10),
                        "integer_part": int(temp),
                        "temp_mode": tempMode,
                        "wifi_box_id": wifi_box,
                        "user_id": self._user.get("id"),
                        "thermostat_id": thermostat,
                        "id": self._user.get("id"),
                        "token": self.TOKEN,
                    },
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("thermostat set temp: %s", data)
            return True
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in setThermostatTemp(): %s", self._redact(str(ex)))
            self._user = None
            return False

    async def setThermostatSeason(self, wifi_box: str, thermostat: str, season: str):
        try:
            await self._ensure_login()

            settings = await self.thermostatSettings(wifi_box, thermostat)
            if not settings:
                raise RuntimeError("Could not read current thermostat settings.")

            # PATCH 0.5 (FIX): the season/settings payload was being PUT to the
            # SET_THERMOSTAT_TEMP endpoint. The payload (unit, season, min/max
            # heating set point, sensor_influence, climatic_curve) belongs to
            # the settings endpoint, so use SET_THERMOSTAT_SETTINGS.
            # ROLLBACK NOTE: if season switching stops working on your device,
            # change self.SET_THERMOSTAT_SETTINGS back to self.SET_THERMOSTAT_TEMP
            # on the line below (single-line revert).
            async with asyncio.timeout(self._timeout):
                res = await self._session.put(
                    self.BASE_URL + self.SET_THERMOSTAT_SETTINGS,
                    data={
                        "unit": settings.get("unit"),
                        "season": season,
                        "min_heating_set_point": settings.get("min_heating_set_point"),
                        "max_heating_set_point": settings.get("max_heating_set_point"),
                        "sensor_influence": settings.get("sensor_influence"),
                        "climatic_curve": settings.get("climatic_curve"),
                        "wifi_box_id": wifi_box,
                        "user_id": self._user.get("id"),
                        "thermostat_id": thermostat,
                        "id": self._user.get("id"),
                        "token": self.TOKEN,
                    },
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("thermostat set season: %s", data)
            return True
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in setThermostatSeason(): %s", self._redact(str(ex)))
            self._user = None
            return False

    async def setBoilerMode(self, wifi_box: str, mode: str):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.put(
                    self.BASE_URL + self.SET_BOILER_MODE,
                    data={
                        "mode": mode,
                        "wifi_box_id": wifi_box,
                        "user_id": self._user.get("id"),
                        "id": self._user.get("id"),
                        "token": self.TOKEN,
                    },
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("boiler set mode: %s", data)
            return True
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in setBoilerMode(): %s", self._redact(str(ex)))
            self._user = None
            return False

    async def setBoilerTemp(self, wifi_box: str, temp: float):
        try:
            await self._ensure_login()

            async with asyncio.timeout(self._timeout):
                res = await self._session.put(
                    self.BASE_URL + self.SET_BOILER_DHW_TEMP,
                    data={
                        "temp": int(temp),
                        "wifi_box_id": wifi_box,
                        "user_id": self._user.get("id"),
                        "id": self._user.get("id"),
                        "token": self.TOKEN,
                    },
                )
                data = await res.json()

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("boiler set temp: %s", data)
            return True
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:
            _LOGGER.warning("Error in setBoilerTemp(): %s", self._redact(str(ex)))
            self._user = None
            return False
