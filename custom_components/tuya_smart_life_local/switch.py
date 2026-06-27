from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
        runtime.local.boolean_dps(),
        key=lambda item: (item[0].is_child, item[0].name, item[1]),
    )
    _remove_stale_registry_entries(hass, entry, runtime, switches)
    entities = [
        TuyaDpsSwitch(runtime.coordinator, runtime, device, dp_id, value)
        for device, dp_id, value in switches
    ]
    async_add_entities(entities)


def _remove_stale_registry_entries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: TuyaSmartLifeRuntime,
    switches: list[tuple[TuyaDeviceDescription, str, bool]],
) -> None:
    active_unique_ids = {f"{device.dev_id}_{dp_id}" for device, dp_id, _ in switches}
    entity_registry = er.async_get(hass)
    for entity in list(entity_registry.entities.values()):
        if entity.platform != DOMAIN or entity.config_entry_id != entry.entry_id:
            continue
        if entity.unique_id not in active_unique_ids:
            entity_registry.async_remove(entity.entity_id)

    active_device_ids = set(runtime.local.devices)
    device_registry = dr.async_get(hass)
    remove_device = getattr(device_registry, "async_remove_device", None)
    if not callable(remove_device):
        return
    for device in list(device_registry.devices.values()):
        if entry.entry_id not in device.config_entries:
            continue
        tuya_ids = {
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN
        }
        if tuya_ids and tuya_ids.isdisjoint(active_device_ids):
            remove_device(device.id)


class TuyaDpsSwitch(CoordinatorEntity[TuyaSmartLifeCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        device: TuyaDeviceDescription,
        dp_id: str,
        initial_value: bool,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.device = device
        self.dp_id = str(dp_id)
        self._state = initial_value
        self._local_ok: bool | None = None
        dp_name = device.dp_names.get(self.dp_id)
        self._attr_unique_id = f"{device.dev_id}_{self.dp_id}"
        self._attr_name = dp_name or f"DP {self.dp_id}"
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
            _LOGGER.debug("Unable to update Tuya status for %s: %s", device.dev_id, err)
            return
        if not isinstance(response, dict):
            return
        dps = response.get("dps")
        if not isinstance(dps, dict) and isinstance(response.get("data"), dict):
            dps = response["data"].get("dps")
        if not isinstance(dps, dict):
            if response.get("Error"):
                self._local_ok = False
                self._async_write_state_if_added()
            return
        self._local_ok = True
        value = dps.get(self.dp_id)
        if value is None and self.dp_id.isdecimal():
            value = dps.get(int(self.dp_id))
        if value is not None:
            if isinstance(value, bool):
                self._state = value
                device.dps[self.dp_id] = value

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()
