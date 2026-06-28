from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .local import FAN_POWER_DP_ID, FAN_SPEED_DP_ID
from .models import TuyaDeviceDescription, TuyaIrAction, TuyaIrRemote

_LOGGER = logging.getLogger(__name__)

SPEED_COUNT = 5
PERCENTAGE_STEP = round(100 / SPEED_COUNT)
IR_ON_ALIASES = ("power on", "turn on", "switch on", "on", "open")
IR_OFF_ALIASES = ("power off", "turn off", "switch off", "off", "close")
IR_TOGGLE_ALIASES = ("power", "toggle", "on off", "on/off")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    devices = sorted(runtime.local.fan_devices(), key=lambda device: device.name)
    entities = [TuyaDpsFan(runtime.coordinator, runtime, device) for device in devices]
    entities.extend(
        TuyaIrFan(runtime.coordinator, runtime, remote)
        for remote in runtime.local.ir_fans()
    )
    async_add_entities(entities)


class TuyaDpsFan(CoordinatorEntity[TuyaSmartLifeCoordinator], FanEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
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
        self._remove_dps_listener: CALLBACK_TYPE | None = None
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
        _LOGGER.debug(
            "Tuya fan DPS update entity=%s device=%s dps=%s",
            self.entity_id,
            dev_id,
            dps,
        )
        if FAN_POWER_DP_ID in dps or FAN_SPEED_DP_ID in dps:
            self._local_ok = True
            self.async_write_ha_state()
        else:
            _LOGGER.debug(
                "Tuya fan ignored DPS update entity=%s device=%s dps=%s",
                self.entity_id,
                dev_id,
                dps,
            )

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

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


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


class TuyaIrFan(CoordinatorEntity[TuyaSmartLifeCoordinator], FanEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:fan"
    _attr_percentage_step = 1
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | getattr(FanEntityFeature, "TURN_ON", FanEntityFeature(0))
        | getattr(FanEntityFeature, "TURN_OFF", FanEntityFeature(0))
    )

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        remote: TuyaIrRemote,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.remote = remote
        self._is_on = False
        self._percentage = 66
        self._local_ok: bool | None = None
        self._attr_unique_id = remote.unique_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, remote.remote_id)},
            "name": remote.remote_name,
            "manufacturer": "Tuya",
            "model": remote.product_id,
            "via_device": (DOMAIN, remote.hub_dev_id),
        }

    @property
    def current_remote(self) -> TuyaIrRemote | None:
        for remote in self.runtime.local.ir_fans():
            if remote.unique_id == self.remote.unique_id:
                self.remote = remote
                return remote
        return None

    @property
    def available(self) -> bool:
        remote = self.current_remote
        if not remote:
            return False
        hub = self.runtime.local.devices.get(remote.hub_dev_id)
        return bool(hub and hub.ip and hub.local_key) and self._local_ok is not False

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def percentage(self) -> int:
        return self._percentage if self._is_on else 0

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        action = self._find(IR_ON_ALIASES) or self._find(IR_TOGGLE_ALIASES) or self._first_action()
        await self._send(action)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        action = self._find(IR_OFF_ALIASES) or self._find(IR_TOGGLE_ALIASES) or self._first_action()
        await self._send(action)
        self._is_on = False
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        if percentage <= 0:
            await self.async_turn_off()
            return
        action = self._find(_speed_aliases(percentage))
        if action:
            await self._send(action)
        elif not self._is_on:
            await self.async_turn_on()
        self._percentage = max(1, min(100, percentage))
        self._is_on = True
        self.async_write_ha_state()

    def _find(self, aliases: tuple[str, ...]) -> TuyaIrAction | None:
        remote = self.current_remote
        return _find_ir_action(remote.actions if remote else [], aliases)

    def _first_action(self) -> TuyaIrAction | None:
        remote = self.current_remote
        return remote.actions[0] if remote and remote.actions else None

    async def _send(self, action: TuyaIrAction | None) -> None:
        if not action:
            raise RuntimeError(f"No Tuya IR fan action found for {self.remote.remote_name}")
        try:
            response = await self.runtime.local.async_publish_ir_action(action)
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR fan action {action.action_name}: {err}"
            ) from err
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR fan action {action.action_name}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        _LOGGER.debug(
            "Published Tuya IR fan action %s via hub %s: %s",
            action.action_name,
            action.hub_dev_id,
            response,
        )

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


def _speed_aliases(percentage: int) -> tuple[str, ...]:
    if percentage >= 85:
        return ("speed high", "fan high", "wind high", "high", "strong")
    if percentage <= 40:
        return ("speed low", "fan low", "wind low", "low")
    return ("speed medium", "speed mid", "fan medium", "fan mid", "medium", "mid")


def _find_ir_action(actions: list[TuyaIrAction], aliases: tuple[str, ...]) -> TuyaIrAction | None:
    normalized_aliases = {_normalize_ir_text(alias) for alias in aliases}
    for action in actions:
        text = _normalize_ir_text(f"{action.action_id} {action.action_name}")
        if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in normalized_aliases):
            return action
    return None


def _normalize_ir_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
