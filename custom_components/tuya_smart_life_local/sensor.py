from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
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
    environment_sensors = sorted(
        runtime.local.environment_sensor_dps(),
        key=lambda item: (item[0].is_child, item[0].name, item[1]),
    )
    entities: list[SensorEntity] = [
        TuyaContextButtonSensor(runtime.coordinator, runtime, device, state, channels)
        for device, state, channels in buttons
    ]
    entities.extend(
        TuyaDpsSensor(runtime.coordinator, runtime, device, dp_id, value, kind, label)
        for device, dp_id, value, kind, label in environment_sensors
    )
    async_add_entities(entities)


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


class TuyaDpsSensor(
    CoordinatorEntity[TuyaSmartLifeCoordinator],
    SensorEntity,
):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        device: TuyaDeviceDescription,
        dp_id: str,
        initial_value: float,
        kind: str,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.device = device
        self.dp_id = str(dp_id)
        self.kind = kind
        self._state = initial_value
        self._remove_dps_listener: CALLBACK_TYPE | None = None
        self._attr_unique_id = f"{device.dev_id}_{self.dp_id}_{kind}"
        self._attr_name = label
        self._attr_device_class = _device_class_for_kind(kind)
        self._attr_native_unit_of_measurement = _unit_for_kind(kind)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.dev_id)},
            "name": device.name,
            "manufacturer": "Tuya",
            "model": device.product_id,
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
    def native_value(self) -> float:
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
        if dev_id != self.device.dev_id or self.dp_id not in dps:
            return
        for device, dp_id, value, kind, _label in self.runtime.local.environment_sensor_dps():
            if device.dev_id == dev_id and str(dp_id) == self.dp_id and kind == self.kind:
                self._state = value
                self.async_write_ha_state()
                return


def _device_class_for_kind(kind: str) -> SensorDeviceClass | None:
    if kind == "temperature":
        return SensorDeviceClass.TEMPERATURE
    if kind == "humidity":
        return SensorDeviceClass.HUMIDITY
    return None


def _unit_for_kind(kind: str) -> str | None:
    if kind == "temperature":
        return UnitOfTemperature.CELSIUS
    if kind == "humidity":
        return PERCENTAGE
    return None
