import logging
import asyncio
from urllib.parse import quote  # PATCH: URL-encode credentials in login()

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed  # PATCH: surface auth failures
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# pylint: disable=abstract-method
# pylint: disable=too-many-instance-attributes
class BesmartClient(object):
    """Representation of a BeSMART thermostat."""

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
        """Initialize the thermostat."""
        self._username = username
        self._password = password
        self._lastupdate = None
        self._user = None
        self._timeout = 30
        # PATCH: verify_ssl is now a config option instead of being hardcoded False.
        # async_get_clientsession caches one session per verify_ssl flag, so True
        # reuses HA's shared verified session and False uses an isolated no-verify
        # session (no degradation of the shared one).
        self._session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    # PATCH: removed unused _fahToCent()/_centToFah() helpers (dead code).

    async def login(self):
        try:
            url = self.BASE_URL + self.LOGIN.format(
                # PATCH: URL-encode so passwords with & = + % # or spaces don't break the URL
                username=quote(self._username, safe=""),
                password=quote(self._password, safe=""),
            )
            async with asyncio.timeout(self._timeout):
                res = await self._session.get(url)

            data = await res.json()
            error_code = data.get("error_code")

            if error_code == "6":
                # PATCH: was `raise asyncio.web.HTTPUnauthorized()` (AttributeError:
                # asyncio has no `web`). Raise ConfigEntryAuthFailed to trigger HA reauth.
                raise ConfigEntryAuthFailed("Invalid credentials.")

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            self._user = message.get("user")
            _LOGGER.debug("login: {}".format(message))
            return list(map(lambda x: x.get("id"), message.get("wifi_box")))
        except ConfigEntryAuthFailed:
            # PATCH: don't swallow auth failures as generic warnings; let them propagate.
            self._user = None
            raise
        except Exception as ex:
            _LOGGER.warning(ex)
            self._user = None
            raise

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
            # TODO: check status
            
            if not res.ok:
                res.raise_for_status()

            # Fix 1: Assicuriamo che message sia un dizionario, anche se l'API risponde con None
            message = data.get("message") or {}
            boiler = message.get("boiler")
            
            # Fix 2: Assicuriamo che la lista thermostat esista prima di iterare (evita il crash del filtro)
            thermostats = list(
                filter(lambda x: x.get("id") is not None, message.get("thermostat") or [])
            )
            
            _LOGGER.debug("boiler: {}".format(boiler))
            _LOGGER.debug("thermostats: {}".format(thermostats))
            return { "boiler": boiler, "thermostats": thermostats }
        except Exception as ex:
            _LOGGER.warning("Errore in devices(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
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
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            _LOGGER.debug("thermostat data: {}".format(message))
            return message
        except Exception as ex:
            _LOGGER.warning("Errore in thermostat(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
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
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            _LOGGER.debug("thermostat settings: {}".format(message))
            return message
        except Exception as ex:
            _LOGGER.warning("Errore in thermostatSettings(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
            return None

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
                    }
                )
            
            data = await res.json()
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("thermostat set mode: {}".format(data))
            return True
        except Exception as ex:
            _LOGGER.warning("Errore in setThermostatMode(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
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
                    }
                )
            
            data = await res.json()
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("thermostat set temp: {}".format(data))
            return True
        except Exception as ex:
            _LOGGER.warning("Errore in setThermostatTemp(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
            return False

    async def setThermostatSeason(self, wifi_box: str, thermostat: str, season: str):
        try:
            await self._ensure_login()

            settings = await self.thermostatSettings(wifi_box, thermostat)
            if not settings:
                raise Exception("Impossibile recuperare i settaggi correnti del termostato.")
                
            settings["season"] = season

            async with asyncio.timeout(self._timeout):
                res = await self._session.put(
                    self.BASE_URL + self.SET_THERMOSTAT_TEMP,
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
                    }
                )
            
            data = await res.json()
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("thermostat set season: {}".format(data))
            return True
        except Exception as ex:
            _LOGGER.warning("Errore in setThermostatSeason(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
            return False

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
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            message = data.get("message")
            _LOGGER.debug("boiler data: {}".format(message))
            return message
        except Exception as ex:
            _LOGGER.warning("Errore in boiler(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
            return None

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
                    }
                )
            
            data = await res.json()
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("boiler set mode: {}".format(data))
            return True
        except Exception as ex:
            _LOGGER.warning("Errore in setBoilerMode(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
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
                    }
                )
            
            data = await res.json()
            # TODO: check status

            if not res.ok:
                res.raise_for_status()

            _LOGGER.debug("boiler set temp: {}".format(data))
            return True
        except Exception as ex:
            _LOGGER.warning("Errore in setBoilerTemp(): %s", ex)
            self._user = None # Forza il re-login al prossimo ciclo
            return False

    async def _ensure_login(self):
        if not self._user:
            await self.login()

    async def ensure_login(self):
        """Public wrapper: ensure we are logged in.

        Used by the coordinator as a pre-flight so that ConfigEntryAuthFailed
        (raised by login() on error_code "6") propagates and triggers the HA
        reauth flow, instead of being swallowed as None by the resource methods.
        """
        await self._ensure_login()
