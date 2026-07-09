"""Binary sensors for the BeSMART integration.

PATCH 0.6: boiler operating states exposed as binary sensors, read from the
boiler payload already fetched by the coordinator (no extra cloud calls):

- Flame        (cloud key ``flame_status``):   burner flame present.
- Circulator   (cloud key ``heating_status``): central heating active, i.e.
  the boiler is running for the heating circuit (circulator pump on).
- DHW Tap      (cloud key ``dhw_active``):     domestic hot water draw in
  progress (a hot water tap is open).

All keys hold "0"/"1" strings (occasionally ints); anything else parses as
unknown (None).
"""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import BesmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator: BesmartDataUpdateCoordinator = config_entry.runtime_data

    new_entities = []
    for device in coordinator.interface_devices:
        wifi_box = device.wifi_box
        # Boiler binary sensors, only if this wifi box actually reports a boiler.
        if device.boiler is not None:
            new_entities.append(
                BoilerFlameBinarySensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            new_entities.append(
                BoilerCirculatorBinarySensor(coordinator, config_entry, wifi_box, device.device_info)
            )
            new_entities.append(
                BoilerDhwTapBinarySensor(coordinator, config_entry, wifi_box, device.device_info)
            )

    if new_entities:
        async_add_entities(new_entities)


class BesmartBoilerBinarySensor(
    CoordinatorEntity[BesmartDataUpdateCoordinator], BinarySensorEntity
):
    """Base class for boiler-derived binary sensors."""

    _attr_has_entity_name = True

    # Key of the boiler data dict to expose; set by subclasses.
    DATA_KEY: str = ""

    def __init__(self, coordinator, config_entry, wifi_box, device_info):
        """Initialize the boiler binary sensor."""
        super().__init__(coordinator)
        self._wifi_box = wifi_box
        self._attr_unique_id = f"{config_entry.entry_id}:{wifi_box}:{self.DATA_KEY}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.boiler_data(self._wifi_box) is not None

    @property
    def is_on(self):
        """Return True/False from the "0"/"1" cloud value, None if unknown."""
        data = self.coordinator.boiler_data(self._wifi_box) or {}
        value = data.get(self.DATA_KEY)
        if value is None:
            return None
        try:
            return bool(int(value))
        except (ValueError, TypeError):
            return None


class BoilerFlameBinarySensor(BesmartBoilerBinarySensor):
    """Burner flame present."""

    DATA_KEY = "flame_status"
    _attr_name = "Boiler Flame"

    @property
    def icon(self):
        """Return a flame icon based on the state."""
        return "mdi:fire" if self.is_on else "mdi:fire-off"


class BoilerCirculatorBinarySensor(BesmartBoilerBinarySensor):
    """Central heating active (circulator pump running)."""

    DATA_KEY = "heating_status"
    _attr_name = "Boiler Circulator"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    @property
    def icon(self):
        """Return a pump icon based on the state."""
        return "mdi:pump" if self.is_on else "mdi:pump-off"


class BoilerDhwTapBinarySensor(BesmartBoilerBinarySensor):
    """Domestic hot water draw in progress (hot water tap open)."""

    DATA_KEY = "dhw_active"
    _attr_name = "DHW Tap"

    @property
    def icon(self):
        """Return a faucet icon based on the state."""
        return "mdi:faucet" if self.is_on else "mdi:water-pump-off"
