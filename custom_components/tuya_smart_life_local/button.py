from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .models import TuyaIrAction

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    entities = [
        TuyaIrActionButton(runtime.coordinator, runtime, action)
        for action in runtime.local.ir_action_buttons()
    ]
    async_add_entities(entities)


class TuyaIrActionButton(CoordinatorEntity[TuyaSmartLifeCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:remote"

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        action: TuyaIrAction,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.action = action
        self._local_ok: bool | None = None
        self._attr_unique_id = action.unique_id
        self._attr_name = action.action_name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, action.remote_id)},
            "name": action.remote_name,
            "manufacturer": "Tuya",
            "model": action.product_id,
            "via_device": (DOMAIN, action.hub_dev_id),
        }

    @property
    def current_action(self) -> TuyaIrAction | None:
        current = self.runtime.local.ir_actions.get(self.action.unique_id)
        if current:
            self.action = current
        return current

    @property
    def available(self) -> bool:
        action = self.current_action
        if not action:
            return False
        hub = self.runtime.local.devices.get(action.hub_dev_id)
        return bool(hub and hub.ip and hub.local_key) and self._local_ok is not False

    async def async_press(self) -> None:
        action = self.current_action
        if not action:
            raise RuntimeError(f"IR action {self.action.unique_id} is no longer available")
        try:
            response: Any = await self.runtime.local.async_publish_ir_action(action)
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR action {action.action_name}: {err}"
            ) from err
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR action {action.action_name}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        self._async_write_state_if_added()
        _LOGGER.debug(
            "Published Tuya IR action %s via hub %s: %s",
            action.action_name,
            action.hub_dev_id,
            response,
        )

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()
