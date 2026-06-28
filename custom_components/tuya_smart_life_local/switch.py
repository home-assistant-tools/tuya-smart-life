from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .models import TuyaDeviceDescription

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    switches = sorted(
        runtime.local.switch_button_dps(),
        key=lambda item: (item[0].is_child, item[0].name, item[1]),
    )
    entities = [
        TuyaDpsSwitch(runtime.coordinator, runtime, device, dp_id, value, label)
        for device, dp_id, value, label in switches
    ]
    async_add_entities(entities)


class TuyaDpsSwitch(CoordinatorEntity[TuyaSmartLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        device: TuyaDeviceDescription,
        dp_id: str,
        initial_value: bool,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.device = device
        self.dp_id = str(dp_id)
        self._state = initial_value
        self._local_ok: bool | None = None
        self._remove_dps_listener: CALLBACK_TYPE | None = None
        self._attr_unique_id = f"{device.dev_id}_{self.dp_id}"
        self._attr_name = label
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
    def is_on(self) -> bool | None:
        return self._state

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
            "Tuya switch DPS update entity=%s device=%s dp=%s dps=%s",
            self.entity_id,
            dev_id,
            self.dp_id,
            dps,
        )
        value = dps.get(self.dp_id)
        if isinstance(value, bool):
            self._local_ok = True
            self._state = value
            self.async_write_ha_state()
        else:
            _LOGGER.debug(
                "Tuya switch ignored DPS update entity=%s device=%s dp=%s value=%r",
                self.entity_id,
                dev_id,
                self.dp_id,
                value,
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, value: bool) -> None:
        device = self.current_device
        if not device:
            raise RuntimeError(f"Device {self.device.dev_id} is no longer available")
        response = await self.runtime.local.async_set_dp(device, self.dp_id, value)
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to set Tuya DP {self.dp_id} for {device.dev_id}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        self._state = value
        device.dps[self.dp_id] = value
        self.async_write_ha_state()

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()
