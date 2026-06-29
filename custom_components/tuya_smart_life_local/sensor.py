from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .models import TuyaDeviceDescription


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    buttons = sorted(
        runtime.local.context_button_sensors(),
        key=lambda item: (item[0].is_child, item[0].name),
    )
    async_add_entities(
        TuyaContextButtonSensor(runtime.coordinator, runtime, device, state, channels)
        for device, state, channels in buttons
    )


class TuyaContextButtonSensor(
    CoordinatorEntity[TuyaSmartLifeCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_name = "Action"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        device: TuyaDeviceDescription,
        initial_state: str | None,
        channels: list[str],
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.device = device
        self._state = initial_state
        self._remove_dps_listener: CALLBACK_TYPE | None = None
        self._attr_unique_id = f"{device.dev_id}_action"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.dev_id)},
            "name": device.name,
            "manufacturer": "Tuya",
            "model": device.product_id,
        }
        self._attr_extra_state_attributes = {
            "channels": channels,
            "actions": [
                f"{channel}_{press_type}"
                for channel in channels
                for press_type in ("press", "double", "long")
            ],
        }
        if device.parent_dev_id:
            self._attr_device_info["via_device"] = (DOMAIN, device.parent_dev_id)

    @property
    def current_device(self) -> TuyaDeviceDescription | None:
        current = self.runtime.local.devices.get(self.device.dev_id)
        if current:
            self.device = current
        return current

    @property
    def available(self) -> bool:
        return self.current_device is not None

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_added_to_hass(self) -> None:
        self._remove_dps_listener = self.runtime.local.async_add_dps_listener(
            self._handle_dps_update
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_dps_listener:
            self._remove_dps_listener()
            self._remove_dps_listener = None

    @callback
    def _handle_dps_update(self, dev_id: str, dps: dict[str, Any]) -> None:
        if dev_id != self.device.dev_id:
            return
        for device, state, channels in self.runtime.local.context_button_sensors():
            if device.dev_id != dev_id:
                continue
            if state is None:
                return
            self._state = state
            self._attr_extra_state_attributes = {
                "channels": channels,
                "actions": [
                    f"{channel}_{press_type}"
                    for channel in channels
                    for press_type in ("press", "double", "long")
                ],
            }
            self.async_write_ha_state()
            return
