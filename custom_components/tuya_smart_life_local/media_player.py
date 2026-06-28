from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .coordinator import TuyaSmartLifeCoordinator
from .models import TuyaIrAction, TuyaIrRemote

_LOGGER = logging.getLogger(__name__)

POWER_ON_ALIASES = ("power on", "turn on", "on", "open")
POWER_OFF_ALIASES = ("power off", "turn off", "off", "close")
POWER_TOGGLE_ALIASES = ("power", "toggle", "on off", "on/off")
VOLUME_UP_ALIASES = ("volume up", "vol up", "vol+", "volume +")
VOLUME_DOWN_ALIASES = ("volume down", "vol down", "vol-", "volume -")
MUTE_ALIASES = ("mute", "sound off")
NEXT_ALIASES = ("channel up", "ch up", "ch+", "program up", "next")
PREVIOUS_ALIASES = ("channel down", "ch down", "ch-", "program down", "previous")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            TuyaIrMediaPlayer(runtime.coordinator, runtime, remote)
            for remote in runtime.local.ir_media_players()
        ]
    )


class TuyaIrMediaPlayer(
    CoordinatorEntity[TuyaSmartLifeCoordinator],
    MediaPlayerEntity,
):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:television"

    def __init__(
        self,
        coordinator: TuyaSmartLifeCoordinator,
        runtime: TuyaSmartLifeRuntime,
        remote: TuyaIrRemote,
    ) -> None:
        super().__init__(coordinator)
        self.runtime = runtime
        self.remote = remote
        self._state = STATE_OFF
        self._local_ok: bool | None = None
        self._attr_unique_id = remote.unique_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, remote.remote_id)},
            "name": remote.remote_name,
            "manufacturer": "Tuya",
            "model": remote.product_id,
            "via_device": (DOMAIN, remote.hub_dev_id),
        }
        if remote.dev_type_id == 7:
            self._attr_icon = "mdi:speaker"
        elif remote.dev_type_id == 6:
            self._attr_icon = "mdi:projector"
        elif remote.dev_type_id in {1, 3}:
            self._attr_icon = "mdi:set-top-box"

    @property
    def current_remote(self) -> TuyaIrRemote | None:
        for remote in self.runtime.local.ir_media_players():
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
    def state(self) -> str:
        return self._state

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        remote = self.current_remote
        actions = remote.actions if remote else []
        features = MediaPlayerEntityFeature(0)
        if _find_action(actions, POWER_ON_ALIASES) or _find_action(actions, POWER_TOGGLE_ALIASES):
            features |= MediaPlayerEntityFeature.TURN_ON
        if _find_action(actions, POWER_OFF_ALIASES) or _find_action(actions, POWER_TOGGLE_ALIASES):
            features |= MediaPlayerEntityFeature.TURN_OFF
        if _find_action(actions, VOLUME_UP_ALIASES) and _find_action(actions, VOLUME_DOWN_ALIASES):
            features |= MediaPlayerEntityFeature.VOLUME_STEP
        if _find_action(actions, MUTE_ALIASES):
            features |= MediaPlayerEntityFeature.VOLUME_MUTE
        if _find_action(actions, NEXT_ALIASES):
            features |= MediaPlayerEntityFeature.NEXT_TRACK
        if _find_action(actions, PREVIOUS_ALIASES):
            features |= MediaPlayerEntityFeature.PREVIOUS_TRACK
        return features

    async def async_turn_on(self) -> None:
        await self._send(self._find(POWER_ON_ALIASES) or self._find(POWER_TOGGLE_ALIASES))
        self._state = STATE_ON
        self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        await self._send(self._find(POWER_OFF_ALIASES) or self._find(POWER_TOGGLE_ALIASES))
        self._state = STATE_OFF
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        await self._send(self._find(VOLUME_UP_ALIASES))

    async def async_volume_down(self) -> None:
        await self._send(self._find(VOLUME_DOWN_ALIASES))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._send(self._find(MUTE_ALIASES))

    async def async_media_next_track(self) -> None:
        await self._send(self._find(NEXT_ALIASES))

    async def async_media_previous_track(self) -> None:
        await self._send(self._find(PREVIOUS_ALIASES))

    def _find(self, aliases: tuple[str, ...]) -> TuyaIrAction | None:
        remote = self.current_remote
        return _find_action(remote.actions if remote else [], aliases)

    async def _send(self, action: TuyaIrAction | None) -> None:
        if not action:
            raise RuntimeError(f"No Tuya IR media action found for {self.remote.remote_name}")
        try:
            response = await self.runtime.local.async_publish_ir_action(action)
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR media action {action.action_name}: {err}"
            ) from err
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to publish Tuya IR media action {action.action_name}: "
                f"{response.get('Error')}"
            )
        self._local_ok = True
        _LOGGER.debug(
            "Published Tuya IR media action %s via hub %s: %s",
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
