from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .local import FAN_POWER_DP_ID, FAN_SPEED_DP_ID
from .models import TuyaDeviceDescription

_LOGGER = logging.getLogger(__name__)

SPEED_COUNT = 5
PERCENTAGE_STEP = round(100 / SPEED_COUNT)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    devices = sorted(runtime.local.fan_devices(), key=lambda device: device.name)
    async_add_entities(
        [TuyaDpsFan(runtime.coordinator, runtime, device) for device in devices]
    )


class TuyaDpsFan(CoordinatorEntity[TuyaSmartLifeCoordinator], FanEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_percentage_step = PERCENTAGE_STEP
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | getattr(FanEntityFeature, "TURN_ON", FanEntityFeature(0))
        | getattr(FanEntityFeature, "TURN_OFF", FanEntityFeature(0))
    )

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        device: TuyaDeviceDescription,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.device = device
        self._local_ok: bool | None = None
        self._attr_unique_id = f"{device.dev_id}_fan"
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
        device = self.current_device
        if not device:
            return False
        if device.is_child:
            parent = self.runtime.local.devices.get(device.parent_dev_id or "")
            has_path = bool(parent and parent.ip and parent.local_key)
        else:
            has_path = bool(device.ip and device.local_key)
        return has_path and self._local_ok is not False

    @property
    def is_on(self) -> bool | None:
        device = self.current_device
        if not device:
            return None
        value = device.dps.get(FAN_POWER_DP_ID)
        return value if isinstance(value, bool) else None

    @property
    def percentage_step(self) -> float:
        return PERCENTAGE_STEP

    @property
    def percentage(self) -> int | None:
        if self.is_on is False:
            return 0
        device = self.current_device
        if not device:
            return None
        return _speed_to_percentage(device.dps.get(FAN_SPEED_DP_ID))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if percentage is not None and percentage > 0:
            await self._async_set_speed(percentage)
        await self._async_set_dp(FAN_POWER_DP_ID, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_dp(FAN_POWER_DP_ID, False)

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return
        await self._async_set_speed(percentage)
        if self.is_on is not True:
            await self._async_set_dp(FAN_POWER_DP_ID, True)

    async def _async_set_speed(self, percentage: int) -> None:
        speed = _percentage_to_speed(percentage)
        await self._async_set_dp(FAN_SPEED_DP_ID, str(speed))

    async def _async_set_dp(self, dp_id: str, value: Any) -> None:
        device = self.current_device
        if not device:
            raise RuntimeError(f"Device {self.device.dev_id} is no longer available")
        response = await self.runtime.local.async_set_dp(device, dp_id, value)
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to set Tuya DP {dp_id} for {device.dev_id}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        device.dps[str(dp_id)] = value
        self.async_write_ha_state()

    async def async_update(self) -> None:
        if not self.available:
            return
        device = self.current_device
        if not device:
            return
        try:
            response = await self.runtime.local.async_status(device)
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            _LOGGER.debug("Unable to update Tuya fan status for %s: %s", device.dev_id, err)
            return
        dps = _dps_from_status(response)
        if not dps:
            if isinstance(response, dict) and response.get("Error"):
                self._local_ok = False
                self._async_write_state_if_added()
            return
        self._local_ok = True
        for dp_id in (FAN_POWER_DP_ID, FAN_SPEED_DP_ID):
            value = dps.get(dp_id)
            if value is None and dp_id.isdecimal():
                value = dps.get(int(dp_id))
            if value is not None:
                device.dps[dp_id] = value

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


def _dps_from_status(response: Any) -> dict[Any, Any] | None:
    if not isinstance(response, dict):
        return None
    dps = response.get("dps")
    if not isinstance(dps, dict) and isinstance(response.get("data"), dict):
        dps = response["data"].get("dps")
    return dps if isinstance(dps, dict) else None


def _speed_to_percentage(value: Any) -> int | None:
    try:
        speed = int(value)
    except (TypeError, ValueError):
        return None
    if speed <= 0:
        return 0
    return max(1, min(100, round(speed * 100 / SPEED_COUNT)))


def _percentage_to_speed(percentage: int) -> int:
    if percentage >= 100:
        return SPEED_COUNT
    return max(1, min(SPEED_COUNT, round(percentage * SPEED_COUNT / 100)))
