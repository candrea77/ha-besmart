"""PATCH 0.8: base class of the per-thermostat settings entities.

PATCH 0.10: also BesmartBoilerEntity, base of the boiler entities, which
are unavailable while the boiler is confirmed offline
(candrea77/ha-besmart#10).

Climatic curve (select), max/min flow temperature and room sensor influence
(number): installer parameters written with PUT Thermostats/setting through
BesmartDataUpdateCoordinator.async_set_thermostat_settings(). Values come
from the polled thermostat data (no extra cloud calls); like the official
app, the entities are unavailable while the boiler is offline.
"""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import parse_value
from .const import DOMAIN
from .coordinator import BesmartDataUpdateCoordinator, BoilerLink


def thermostat_setting_entities(coordinator: BesmartDataUpdateCoordinator, config_entry: ConfigEntry, classes):
    """One entity of each class per thermostat of a wifi box with a boiler."""
    return [
        cls(coordinator, config_entry, device.wifi_box, thermostat.get("id"), thermostat.get("name"), device.thermostat_device_info(thermostat.get("id")))
        for device in coordinator.interface_devices
        if device.boiler is not None
        for thermostat in device.thermostats
        for cls in classes
    ]


class BesmartThermostatSettingEntity(CoordinatorEntity[BesmartDataUpdateCoordinator]):
    """A thermostat setting (field of Thermostats/setting)."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    # Cloud field; also the unique_id suffix and the translation key.
    FIELD: str

    def __init__(self, coordinator, config_entry, wifi_box, room_id, room_name, device_info):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._room_id = room_id
        self._room_name = room_name
        # unique_id = <entry_id>:<thermostat id>_<field>: the cloud id, stable
        # when the thermostat is renamed in the Riello app.
        self._attr_unique_id = f"{config_entry.entry_id}:{room_id}_{self.FIELD}"
        self._attr_device_info = device_info
        # PATCH 0.8 (b2): no {room} in the name: the device (named
        # after the room) already prefixes it.

    @property
    def _value(self) -> float | None:
        data = self.coordinator.thermostat_data(self._wifi_box, self._room_id)
        return parse_value(data.get(self.FIELD)) if data else None

    @property
    def available(self) -> bool:
        """Boiler online, boiler and thermostat data present, valid value."""
        return (
            super().available
            and self.coordinator.boiler_data(self._wifi_box) is not None
            and self._boiler_online()
            and self._value is not None
        )

    def _boiler_online(self) -> bool:
        """PATCH 0.10: this thermostat's read, immediately (missing = offline)."""
        return self.coordinator.boiler_link(self._wifi_box, self._room_id) is BoilerLink.ONLINE

    def _check_online(self) -> None:
        """Refuse a change while the boiler is offline."""
        if not self._boiler_online():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="boiler_offline",
                translation_placeholders={"room": str(self._room_name)},
            )

    async def _async_write(self, value) -> None:
        """Write this field (read-modify-write) and refresh."""
        self._check_online()
        await self.coordinator.async_set_thermostat_settings(
            self._wifi_box, self._room_id, self._room_name, {self.FIELD: value}
        )
        await self.coordinator.async_request_refresh()


class BesmartBoilerEntity(CoordinatorEntity[BesmartDataUpdateCoordinator]):
    """PATCH 0.10: base of the entities fed by the boiler data of a wifi box.

    Unavailable while the coordinator says the boiler is confirmed offline
    (boiler_entity_available(), the single rule shared with 0.11.0), so
    values the cloud no longer updates are not shown as current.
    """

    # Cloud field of the entity, passed to boiler_entity_available().
    DATA_KEY: str = ""
    # False when the entity has a value without the boiler payload
    # (modulation: its own endpoint, unknown when the read fails).
    REQUIRES_BOILER_DATA = True

    _wifi_box: str

    @property
    def available(self) -> bool:
        """Coordinator ok, boiler data present, boiler not offline."""
        return (
            super().available
            and (
                not self.REQUIRES_BOILER_DATA
                or self.coordinator.boiler_data(self._wifi_box) is not None
            )
            and self.coordinator.boiler_entity_available(self._wifi_box, self.DATA_KEY)
        )
