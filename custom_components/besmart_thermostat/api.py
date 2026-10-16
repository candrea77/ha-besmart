"""API client for the BeSMART cloud (Riello)."""
import asyncio
import json
import logging
import math
import re
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote  # URL-encode credentials in login()

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# PATCH 0.7: path/query segments carrying the user id, e.g.
# ".../user_id/12345/..." or "...?user_id=12345". The id itself is matched by
# position, never by value, so short numeric ids can't mangle other text.
_USER_ID_RE = re.compile(r"(user_id[/=])[^/&?\s'\"]+")
# PATCH 0.7: wifi box id in URLs: keep only the last 4 characters.
_WIFI_BOX_RE = re.compile(r"(wifi_box_id[/=])[^/&?\s'\"]*?([^/&?\s'\"]{0,4})(?=[/&?\s'\"]|$)")

# PATCH 0.7: Boilers/data keys describing the boiler state, logged raw (debug)
# to compare with the official app (flame/heating/DHW status).
_STATE_KEY_RE = re.compile(r"flame|fire|status|active|mode|work|state", re.IGNORECASE)

# PATCH 0.7 (security): keys whose values are masked in logged payloads. With a
# public, app-wide API token, user_id + wifi_box_id act as credentials.
_SENSITIVE_KEYS = frozenset(
    {
        "user_id", "wifi_box_id", "wifi_box", "token", "email", "username",
        "password", "mac", "mac_address", "serial", "serial_number", "imei",
        "phone",
    }
)

# PATCH 0.7 (security): upper bound for a cloud response body.
MAX_RESPONSE_BYTES = 512 * 1024

# PATCH 0.7 (security): accepted values for write commands.
THERMOSTAT_MODES = frozenset(range(6))  # AUTO, MANUAL, ECO, PARTY, IDLE, DHW
THERMOSTAT_TEMP_MODES = frozenset({"0", "1", "2"})  # frost, economy, comfort
THERMOSTAT_TEMP_MIN = 3.0
THERMOSTAT_TEMP_MAX = 35.0
SEASONS = frozenset({"0", "1"})  # cooling, heating
BOILER_MODES = frozenset({"0", "1", "2"})
DHW_TEMP_MIN = 30
DHW_TEMP_MAX = 60


class BesmartError(Exception):
    """PATCH 0.7: cloud error whose message never contains secrets.

    Raised ``from None`` so that Home Assistant (which falls back to the
    ``__cause__`` text for empty messages) can never show the original aiohttp
    exception, whose message embeds the request URL with the credentials.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class BesmartTLSError(BesmartError):
    """PATCH 0.7: TLS certificate verification of the cloud failed."""


class BesmartValidationError(ValueError):
    """PATCH 0.7: a write command was rejected before reaching the cloud."""


def mask_id(value) -> str:
    """PATCH 0.7: show only the last 4 characters of an identifier."""
    text = str(value)
    return "***" + text[-4:]


# PATCH 0.7: values the cloud uses for "no data" (in addition to empty/None).
_INVALID_VALUES = {"", "null", "none", "dncon"}


def parse_value(value) -> float | None:
    """Parse a numeric cloud value, returning None when it is not valid.

    Empty, None, "null", "DNCON" and any string containing "disable" are
    treated as missing. "0.0" is a valid 0, not missing.
    """
    if value is None:
        return None
    text = str(value).strip()
    lowered = text.lower()
    if lowered in _INVALID_VALUES or "disable" in lowered:
        return None
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


# pylint: disable=too-many-instance-attributes
class BesmartClient:
    """Client for the BeSMART cloud API.

    PATCH 0.7 (security): every request goes through _request() (timeout, body
    size limit, sanitized exceptions); write commands validate their input
    before anything is sent.
    """

    BASE_URL = "https://api.besmart-home.com/BeSMART_release/v1/api/"
    # Fixed app-wide token, the same one hardcoded in the official app (it is
    # not a per-user secret, but it is still kept out of the logs).
    TOKEN = "a69157a524fdcf0246a58fc5767683c700c5b7b4"
    # The endpoint only supports GET: POST answers "Unknown method". The
    # credentials therefore travel in the query string (over verified TLS by
    # default) and are never logged.
    LOGIN = "iOS/users/login_new?username={username}&password={password}"

    GET_WIFI_BOX_DATA = "Android/Wifi_boxes/data/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}"

    GET_THERMOSTAT_DATA = "Android/Thermostats/data/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}/thermostat_id/{thermostat}"
    GET_THERMOSTAT_SETTINGS = "Android/thermostats/setting/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}/thermostat_id/{thermostat}"
    SET_THERMOSTAT_TEMP = "Android/Thermostats/temperature"
    SET_THERMOSTAT_MODE = "Android/Thermostats/mode"
    SET_THERMOSTAT_SETTINGS = "Android/Thermostats/setting"
    # PATCH 0.7: removed the unused program/advance/holiday_end_time endpoints
    # (smaller write surface).

    GET_BOILER_DATA = "Android/Boilers/data/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}"
    # PATCH 0.7: read-only technical endpoints (see CLAUDE.md: never write
    # Boilers/TSP, never call Boilers/reset).
    GET_BOILER_MODULATION = "Android/Boilers/modulation_ratio/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}"
    # PATCH 0.7 (b2): single-value technical endpoints, read on demand by the
    # "Read technical data" button. Boilers/technical_datas no longer exists
    # (404). Only names from TECHNICAL_ENDPOINTS are ever formatted in.
    GET_BOILER_VALUE = "Android/Boilers/{endpoint}/user_id/{user}/wifi_box_id/{wifi_box}/token/{token}"
    TECHNICAL_ENDPOINTS = (
        "system_pressure",
        "flow_temperature",
        "return_temperature",
        "flues_temperature",
        "dhw_temperature",
        "dhw_set_point",
        "heating_set_point",
        "dhw_flow_rate",
        "flame_status",
        "external_temperature",
        "heat_exchanger_hours_amassed",
        "worked_hours_in_heating",
        "worked_hours_in_dhw",
        "unit",
    )
    # Same limits as the official app's technical area.
    TECHNICAL_CONCURRENCY = 3
    TECHNICAL_TIMEOUT = 5
    TECHNICAL_ATTEMPTS = 3
    TECHNICAL_RETRY_DELAY = 1
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
        # PATCH 0.7: technical endpoints that answered 404 (not supported by
        # the cloud / this boiler). Skipped on later presses until reload.
        self.unsupported_endpoints: set[str] = set()
        # PATCH 0.7 (security): ids learnt at login, masked wherever they
        # appear in log text (they act as credentials).
        self._known_user_ids: set[str] = set()
        self._known_wifi_boxes: set[str] = set()
        # verify_ssl is a config option. async_get_clientsession caches one
        # session per verify_ssl flag, so True reuses HA's shared verified
        # session and False uses an isolated no-verify session.
        self._session = async_get_clientsession(hass, verify_ssl=verify_ssl)

    @property
    def user_id(self) -> str | None:
        """Id of the logged-in user (never log it)."""
        return str(self._user["id"]) if self._user and self._user.get("id") else None

    # ------------------------------------------------------------------ utils

    def _redact(self, text: str) -> str:
        """Strip credentials from any log/exception message.

        aiohttp exceptions (ClientResponseError, ClientConnectorError, ...)
        embed the full request URL in their message; the login URL contains
        username and password in the query string, so we must never log those
        messages verbatim.

        PATCH 0.7: data URLs also embed the API token and the user id, so
        those are stripped too; the wifi box id is reduced to its last 4
        characters. User and wifi box ids learnt at login are also masked
        when they appear outside URLs (e.g. in payloads).
        """
        text = str(text)
        for secret in (
            quote(self._password, safe=""),
            self._password,
            quote(self._username, safe=""),
            self._username,
            self.TOKEN,
        ):
            if secret:
                text = text.replace(secret, "***")
        text = _USER_ID_RE.sub(r"\1***", text)
        text = _WIFI_BOX_RE.sub(r"\1***\2", text)
        for box in self._known_wifi_boxes:
            text = re.sub(rf"(?<![\w*]){re.escape(box)}(?!\w)", mask_id(box), text)
        for uid in self._known_user_ids:
            text = re.sub(rf"(?<![\w*]){re.escape(uid)}(?!\w)", "***", text)
        return text

    def _redact_payload(self, payload) -> str:
        """PATCH 0.7: redacted text of a cloud payload for debug logs."""

        def scrub(obj):
            if isinstance(obj, dict):
                return {
                    k: ("***" if str(k).lower() in _SENSITIVE_KEYS else scrub(v))
                    for k, v in obj.items()
                }
            if isinstance(obj, list):
                return [scrub(v) for v in obj]
            return obj

        return self._redact(str(scrub(payload)))

    def mask_wifi_box(self, wifi_box) -> str:
        """PATCH 0.7: wifi box id safe to show in messages."""
        return mask_id(wifi_box)

    def _current_user_id(self) -> str:
        """PATCH 0.7: snapshot of the user id right after _ensure_login().

        Avoids AttributeError races when another coroutine resets
        ``self._user`` concurrently.
        """
        user_id = self.user_id
        if not user_id:
            raise BesmartError("not logged in")
        return user_id

    async def _request(self, method: str, url: str, *, data=None, timeout=None):
        """PATCH 0.7: one HTTP request -> (status, parsed JSON or None).

        Applies the timeout to the whole exchange, caps the body size and
        converts every transport error into a BesmartError whose message is
        redacted (raised ``from None``).
        """
        try:
            async with asyncio.timeout(timeout or self._timeout):
                if method == "get":
                    res = await self._session.get(url)
                else:
                    res = await self._session.put(url, data=data)
                try:
                    length = res.content_length
                    if length is not None and length > MAX_RESPONSE_BYTES:
                        raise BesmartError("response too large")
                    raw = await res.content.read(MAX_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise BesmartError("response too large")
                    status = res.status
                finally:
                    res.release()
        except BesmartError:
            raise
        except (aiohttp.ClientConnectorCertificateError, aiohttp.ClientSSLError) as ex:
            raise BesmartTLSError(
                f"TLS certificate verification failed: {type(ex).__name__}"
            ) from None
        except TimeoutError:
            raise BesmartError("timeout", retryable=True) from None
        except aiohttp.ClientError as ex:
            raise BesmartError(
                f"{type(ex).__name__}: {self._redact(str(ex))}", retryable=True
            ) from None
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, None

    # ------------------------------------------------------------------ login

    async def login(self):
        """Log in and return the list of wifi box ids.

        PATCH 0.7: raises ConfigEntryAuthFailed (invalid credentials) or
        BesmartError/BesmartTLSError with a redacted message; the original
        exception is never chained.
        """
        url = self.BASE_URL + self.LOGIN.format(
            # URL-encode so passwords with & = + % # or spaces don't break the URL
            username=quote(self._username, safe=""),
            password=quote(self._password, safe=""),
        )
        try:
            status, data = await self._request("get", url)

            # PATCH 0.5: compare as string so an int 6 is caught as well.
            if isinstance(data, dict) and str(data.get("error_code")) == "6":
                raise ConfigEntryAuthFailed("Invalid credentials.")

            if status >= 400:
                raise BesmartError(f"HTTP {status}")

            message = data.get("message") if isinstance(data, dict) else None
            user = message.get("user") if isinstance(message, dict) else None
            if not isinstance(user, dict) or not user.get("id"):
                raise BesmartError("Login response did not contain user data")

            self._user = user
            self._known_user_ids.add(str(user["id"]))
            boxes = [
                x.get("id")
                for x in (message.get("wifi_box") or [])
                if isinstance(x, dict) and x.get("id") is not None
            ]
            self._known_wifi_boxes.update(str(b) for b in boxes)
            # PATCH 0.7: no ids in the log (they act as credentials).
            _LOGGER.debug("login ok, %d wifi box(es)", len(boxes))
            return boxes
        except ConfigEntryAuthFailed:
            # Don't swallow auth failures; let them propagate to trigger reauth.
            self._user = None
            raise
        except BesmartError as ex:
            self._user = None
            _LOGGER.warning("Login to BeSMART cloud failed: %s", self._redact(str(ex)))
            raise
        except Exception as ex:  # pylint: disable=broad-except
            self._user = None
            message = f"{type(ex).__name__}: {self._redact(str(ex))}"
            _LOGGER.warning("Login to BeSMART cloud failed: %s", message)
            raise BesmartError(message) from None

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

    async def _read(self, name: str, path: str, *, reset_user: bool = True, **fmt):
        """PATCH 0.7: GET a data endpoint and return the JSON dict, or None.

        On error logs a redacted warning and (for core reads) resets the login
        so that the next cycle logs in again.
        """
        try:
            await self._ensure_login()
            url = self.BASE_URL + path.format(
                user=self._current_user_id(), token=self.TOKEN, **fmt
            )
            status, data = await self._request("get", url)
            if status >= 400:
                raise BesmartError(f"HTTP {status}")
            if not isinstance(data, dict):
                raise BesmartError("invalid response")
            return data
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Error in %s(): %s", name, self._redact(str(ex)))
            if reset_user:
                self._user = None  # force re-login on next cycle
            return None

    async def devices(self, wifi_box: str):
        data = await self._read("devices", self.GET_WIFI_BOX_DATA, wifi_box=wifi_box)
        if data is None:
            return None
        # message may be None if the API answers with an empty payload
        message = data.get("message")
        if not isinstance(message, dict):
            message = {}
        boiler = message.get("boiler")
        if not isinstance(boiler, dict):
            boiler = None
        thermostats = [
            x for x in (message.get("thermostat") or [])
            if isinstance(x, dict) and x.get("id") is not None
        ]
        _LOGGER.debug("boiler: %s", self._redact_payload(boiler))
        _LOGGER.debug("thermostats: %s", self._redact_payload(thermostats))
        return {"boiler": boiler, "thermostats": thermostats}

    async def thermostat(self, wifi_box: str, thermostat: str):
        data = await self._read(
            "thermostat", self.GET_THERMOSTAT_DATA, wifi_box=wifi_box, thermostat=thermostat
        )
        message = data.get("message") if data else None
        if not isinstance(message, dict):
            return None
        _LOGGER.debug("thermostat data: %s", self._redact_payload(message))
        return message

    async def thermostatSettings(self, wifi_box: str, thermostat: str):
        data = await self._read(
            "thermostatSettings", self.GET_THERMOSTAT_SETTINGS, wifi_box=wifi_box, thermostat=thermostat
        )
        message = data.get("message") if data else None
        if not isinstance(message, dict):
            return None
        _LOGGER.debug("thermostat settings: %s", self._redact_payload(message))
        return message

    async def boiler(self, wifi_box: str):
        data = await self._read("boiler", self.GET_BOILER_DATA, wifi_box=wifi_box)
        message = data.get("message") if data else None
        if not isinstance(message, dict):
            return None
        _LOGGER.debug("boiler data: %s", self._redact_payload(message))
        # PATCH 0.7: raw state fields (flame/heating/DHW), to compare with
        # the official app when a binary sensor looks wrong.
        _LOGGER.debug(
            "boiler state fields: %s",
            self._redact_payload(
                {k: v for k, v in message.items() if _STATE_KEY_RE.search(str(k))}
            ),
        )
        return message

    async def _optional_get(self, name: str, path: str, wifi_box: str):
        """PATCH 0.7: GET an optional endpoint and return its ``message``.

        Returns None on any error or when the cloud answers with status false.
        Unlike the core read methods it never resets ``self._user``: an
        endpoint unsupported by a given boiler must not force a re-login (and
        disturb the other calls) on every cycle.
        """
        data = await self._read(name, path, reset_user=False, wifi_box=wifi_box)
        if data is None:
            return None
        if data.get("status") is not True:
            _LOGGER.debug(
                "%s: cloud returned no data (error_code %s)", name, data.get("error_code")
            )
            return None
        return data.get("message")

    async def boiler_modulation(self, wifi_box: str):
        """PATCH 0.7: return the raw modulation ratio value, or None.

        Response: {"message": {"key": "modulation_ratio", "value": "..."}}.
        """
        message = await self._optional_get(
            "boiler_modulation", self.GET_BOILER_MODULATION, wifi_box
        )
        if not isinstance(message, dict) or message.get("key") != "modulation_ratio":
            return None
        _LOGGER.debug("boiler modulation: %s", self._redact(str(message.get("value"))))
        return message.get("value")

    async def boiler_technical(self, wifi_box: str) -> dict:
        """PATCH 0.7 (b2): read every technical endpoint once (button press).

        Returns {endpoint: raw value} for the endpoints that answered; failed
        or unsupported ones are absent (the caller exposes them as None).
        At most TECHNICAL_CONCURRENCY requests run at once, each with a
        TECHNICAL_TIMEOUT s timeout and up to TECHNICAL_ATTEMPTS attempts on
        timeouts, connection errors and 5xx. A 404 is never retried: the
        endpoint is recorded in ``unsupported_endpoints``, logged once at
        debug level and skipped afterwards. Never resets ``self._user``.
        """
        await self._ensure_login()
        user_id = self._current_user_id()
        semaphore = asyncio.Semaphore(self.TECHNICAL_CONCURRENCY)
        endpoints = [e for e in self.TECHNICAL_ENDPOINTS if e not in self.unsupported_endpoints]
        results = await asyncio.gather(
            *[self._technical_value(e, user_id, wifi_box, semaphore) for e in endpoints]
        )
        values = {e: v for e, (ok, v) in zip(endpoints, results) if ok}
        failed = [e for e, (ok, _) in zip(endpoints, results) if not ok and e not in self.unsupported_endpoints]
        if failed:
            _LOGGER.warning("Technical data: no answer from %s", ", ".join(failed))
        _LOGGER.debug("boiler technical data: %s", self._redact_payload(values))
        return values

    async def _technical_value(
        self, endpoint: str, user_id: str, wifi_box: str, semaphore
    ) -> tuple[bool, object]:
        """Return (True, value) on success, (False, None) on failure."""
        url = self.BASE_URL + self.GET_BOILER_VALUE.format(
            endpoint=endpoint,
            user=user_id,
            wifi_box=wifi_box,
            token=self.TOKEN,
        )
        for attempt in range(1, self.TECHNICAL_ATTEMPTS + 1):
            try:
                async with semaphore:
                    status, data = await self._request(
                        "get", url, timeout=self.TECHNICAL_TIMEOUT
                    )
                if status == 404:
                    if endpoint not in self.unsupported_endpoints:
                        self.unsupported_endpoints.add(endpoint)
                        _LOGGER.debug("Technical endpoint not supported (404): %s", endpoint)
                    return False, None
                if status >= 500:
                    raise BesmartError(f"HTTP {status}", retryable=True)
                if status >= 400:
                    raise BesmartError(f"HTTP {status}")
                if not isinstance(data, dict) or data.get("status") is not True:
                    raise BesmartError(
                        "status false (error_code %s)"
                        % (data.get("error_code") if isinstance(data, dict) else None)
                    )
                message = data.get("message")
                if not isinstance(message, dict):
                    raise BesmartError("no message")
                return True, message.get("value")
            except BesmartError as ex:
                error = ex
            _LOGGER.debug(
                "Technical endpoint %s, attempt %s/%s failed: %s",
                endpoint, attempt, self.TECHNICAL_ATTEMPTS, self._redact(str(error)),
            )
            if not error.retryable or attempt == self.TECHNICAL_ATTEMPTS:
                break
            await asyncio.sleep(self.TECHNICAL_RETRY_DELAY)
        return False, None

    # ----------------------------------------------------------------- writes

    async def _write(self, name: str, path: str, payload: dict) -> bool:
        """PATCH 0.7: PUT a command; True on success, False on failure.

        Failure: HTTP error, ``status`` explicitly false, or ``error_code``
        present and different from 0. When ``status`` is missing the (redacted)
        body is logged at debug level and the write is considered successful,
        so commands that work today keep working.
        """
        try:
            await self._ensure_login()
            user_id = self._current_user_id()
            status, data = await self._request(
                "put",
                self.BASE_URL + path,
                data={**payload, "user_id": user_id, "id": user_id, "token": self.TOKEN},
            )
            if status >= 400:
                raise BesmartError(f"HTTP {status}")
            if isinstance(data, dict):
                error_code = data.get("error_code")
                if data.get("status") is False or (
                    error_code not in (None, "") and str(error_code) != "0"
                ):
                    raise BesmartError(
                        f"cloud rejected the command (error_code {error_code})"
                    )
                if "status" not in data:
                    _LOGGER.debug(
                        "%s: no status in response, assuming success: %s",
                        name, self._redact_payload(data),
                    )
            else:
                _LOGGER.debug("%s: non-JSON response, assuming success", name)
            _LOGGER.debug("%s ok: %s", name, self._redact_payload(data))
            return True
        except ConfigEntryAuthFailed:
            raise
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Error in %s(): %s", name, self._redact(str(ex)))
            self._user = None
            return False

    async def setThermostatMode(self, wifi_box: str, thermostat: str, mode):
        # PATCH 0.7: whitelist of the BeSMART thermostat modes.
        try:
            mode = int(mode)
        except (TypeError, ValueError):
            raise BesmartValidationError(f"Invalid thermostat mode: {mode!r}") from None
        if mode not in THERMOSTAT_MODES:
            raise BesmartValidationError(f"Invalid thermostat mode: {mode}")
        return await self._write(
            "setThermostatMode",
            self.SET_THERMOSTAT_MODE,
            {"mode": mode, "wifi_box_id": wifi_box, "thermostat_id": thermostat},
        )

    async def setThermostatTemp(self, wifi_box: str, thermostat: str, temp: float, tempMode: str):
        # PATCH 0.7: range check, 0.1 rounding (20.96 used to become 20 + 10
        # tenths) and temp mode whitelist.
        try:
            value = float(temp)
        except (TypeError, ValueError):
            raise BesmartValidationError(f"Invalid temperature: {temp!r}") from None
        if not math.isfinite(value) or not THERMOSTAT_TEMP_MIN <= value <= THERMOSTAT_TEMP_MAX:
            raise BesmartValidationError(
                f"Temperature must be between {THERMOSTAT_TEMP_MIN} and {THERMOSTAT_TEMP_MAX}"
            )
        if str(tempMode) not in THERMOSTAT_TEMP_MODES:
            raise BesmartValidationError(f"Invalid temperature mode: {tempMode!r}")
        tenths = int(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) * 10)
        return await self._write(
            "setThermostatTemp",
            self.SET_THERMOSTAT_TEMP,
            {
                "fraction_part": tenths % 10,
                "integer_part": tenths // 10,
                "temp_mode": str(tempMode),
                "wifi_box_id": wifi_box,
                "thermostat_id": thermostat,
            },
        )

    async def setThermostatSeason(self, wifi_box: str, thermostat: str, season: str):
        if str(season) not in SEASONS:
            raise BesmartValidationError(f"Invalid season: {season!r}")
        settings = await self.thermostatSettings(wifi_box, thermostat)
        if not settings:
            _LOGGER.warning("Error in setThermostatSeason(): could not read current settings")
            return False
        # PATCH 0.7: the settings read from the cloud are written back as they
        # are, so refuse to send anything if a field is missing or invalid
        # (it would misconfigure set points or the climatic curve).
        fields = ("min_heating_set_point", "max_heating_set_point", "sensor_influence", "climatic_curve")
        if str(settings.get("unit")) not in ("0", "1") or any(
            parse_value(settings.get(f)) is None for f in fields
        ):
            _LOGGER.warning(
                "Error in setThermostatSeason(): incomplete thermostat settings, nothing sent"
            )
            return False

        # PATCH 0.5 (FIX): the season/settings payload was being PUT to the
        # SET_THERMOSTAT_TEMP endpoint. The payload (unit, season, min/max
        # heating set point, sensor_influence, climatic_curve) belongs to
        # the settings endpoint, so use SET_THERMOSTAT_SETTINGS.
        return await self._write(
            "setThermostatSeason",
            self.SET_THERMOSTAT_SETTINGS,
            {
                "unit": str(settings.get("unit")),
                "season": str(season),
                **{f: settings.get(f) for f in fields},
                "wifi_box_id": wifi_box,
                "thermostat_id": thermostat,
            },
        )

    async def setBoilerMode(self, wifi_box: str, mode: str):
        if str(mode) not in BOILER_MODES:
            raise BesmartValidationError(f"Invalid boiler mode: {mode!r}")
        return await self._write(
            "setBoilerMode",
            self.SET_BOILER_MODE,
            {"mode": str(mode), "wifi_box_id": wifi_box},
        )

    async def setBoilerTemp(self, wifi_box: str, temp: float):
        # PATCH 0.7: Home Assistant does not range-check water heater targets.
        try:
            value = float(temp)
        except (TypeError, ValueError):
            raise BesmartValidationError(f"Invalid temperature: {temp!r}") from None
        if not math.isfinite(value) or not value.is_integer() or not DHW_TEMP_MIN <= value <= DHW_TEMP_MAX:
            raise BesmartValidationError(
                f"DHW temperature must be a whole number between {DHW_TEMP_MIN} and {DHW_TEMP_MAX}"
            )
        return await self._write(
            "setBoilerTemp",
            self.SET_BOILER_DHW_TEMP,
            {"temp": int(value), "wifi_box_id": wifi_box},
        )
