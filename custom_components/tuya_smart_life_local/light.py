from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .models import TuyaIrAction, TuyaIrRemote

_LOGGER = logging.getLogger(__name__)

ON_ALIASES = ("power on", "turn on", "switch on", "on", "open")
OFF_ALIASES = ("power off", "turn off", "switch off", "off", "close")
TOGGLE_ALIASES = ("power", "toggle", "on off", "on/off")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            TuyaIrLight(runtime.coordinator, runtime, remote)
            for remote in runtime.local.ir_lights()
        ]
    )


class TuyaIrLight(CoordinatorEntity[TuyaSmartLifeCoordinator], LightEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:lightbulb"
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

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
        for remote in self.runtime.local.ir_lights():
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

    async def async_turn_on(self, **kwargs: Any) -> None:
        action = self._find(ON_ALIASES) or self._find(TOGGLE_ALIASES) or self._first_action()
        await self._send(action)
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        action = self._find(OFF_ALIASES) or self._find(TOGGLE_ALIASES) or self._first_action()
        await self._send(action)
        self._is_on = False
        self.async_write_ha_state()

    def _find(self, aliases: tuple[str, ...]) -> TuyaIrAction | None:
        remote = self.current_remote
        return _find_action(remote.actions if remote else [], aliases)

    def _first_action(self) -> TuyaIrAction | None:
        remote = self.current_remote
        return remote.actions[0] if remote and remote.actions else None

    async def _send(self, action: TuyaIrAction | None) -> None:
        if not action:
            raise RuntimeError(f"No Tuya IR light action found for {self.remote.remote_name}")
        try:
            response = await self.runtime.local.async_publish_ir_action(action)
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR light action {action.action_name}: {err}"
            ) from err
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR light action {action.action_name}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        _LOGGER.debug(
            "Published Tuya IR light action %s via hub %s: %s",
            action.action_name,
            action.hub_dev_id,
            response,
        )

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


def _find_action(actions: list[TuyaIrAction], aliases: tuple[str, ...]) -> TuyaIrAction | None:
    normalized_aliases = {_normalize(alias) for alias in aliases}
    for action in actions:
        text = _normalize(f"{action.action_id} {action.action_name}")
        if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in normalized_aliases):
            return action
    return None


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
