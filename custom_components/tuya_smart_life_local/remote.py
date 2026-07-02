from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Iterable
import json
import logging
from typing import Any

from homeassistant.components.remote import (
    ATTR_ALTERNATIVE,
    ATTR_COMMAND_TYPE,
    ATTR_DELAY_SECS,
    ATTR_DEVICE,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_COMMAND
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from . import TuyaSmartLifeRuntime
from .const import DOMAIN
from .models import TuyaDeviceDescription

_LOGGER = logging.getLogger(__name__)

CODE_STORAGE_VERSION = 1
FLAG_STORAGE_VERSION = 1
FLAG_SAVE_DELAY = 15
DEFAULT_LEARN_TIMEOUT = 30

IrCommandPayload = str | dict[str, Any]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime: TuyaSmartLifeRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        TuyaIrHubRemote(runtime, hub)
        for hub in runtime.local.ir_hub_devices()
    ])


class TuyaIrHubRemote(RemoteEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:remote"
    _attr_supported_features = (
        RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND
    )
    _attr_is_on = True

    def __init__(
        self,
        runtime: TuyaSmartLifeRuntime,
        hub: TuyaDeviceDescription,
    ) -> None:
        self.runtime = runtime
        self.hub = hub
        self._attr_unique_id = f"{hub.dev_id}_ir_remote"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, hub.dev_id)},
            "name": hub.name,
            "manufacturer": "Tuya",
            "model": hub.product_id,
        }
        self._code_storage = Store(
            runtime.local.hass,
            CODE_STORAGE_VERSION,
            f"{DOMAIN}_{hub.dev_id}_ir_codes",
        )
        self._flag_storage = Store(
            runtime.local.hass,
            FLAG_STORAGE_VERSION,
            f"{DOMAIN}_{hub.dev_id}_ir_flags",
        )
        self._codes: dict[str, dict[str, str | list[str]]] = {}
        self._flags: defaultdict[str, int] = defaultdict(int)
        self._storage_loaded = False
        self._local_ok: bool | None = None
        self._lock = asyncio.Lock()

    @property
    def current_hub(self) -> TuyaDeviceDescription | None:
        hub = self.runtime.local.devices.get(self.hub.dev_id)
        if hub:
            self.hub = hub
        return hub

    @property
    def available(self) -> bool:
        hub = self.current_hub
        return self.runtime.local.has_local_path(hub) and self._local_ok is not False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._storage_loaded:
            return {}
        return {
            "learned_devices": sorted(self._codes),
            "learned_command_count": sum(
                len(commands) for commands in self._codes.values()
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_send_command(
        self,
        command: Iterable[str] | str,
        **kwargs: Any,
    ) -> None:
        hub = self.current_hub
        if not hub:
            raise RuntimeError(f"IR hub {self.hub.dev_id} is no longer available")
        await self._async_load_storage()
        commands = _ensure_list(command)
        subdevice = kwargs.get(ATTR_DEVICE)
        repeats = int(kwargs.get(ATTR_NUM_REPEATS) or 1)
        delay = float(kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS))
        payloads = self._extract_payloads(commands, subdevice)

        async with self._lock:
            sent_any = False
            for _ in range(max(repeats, 1)):
                for command_payloads in payloads:
                    if sent_any:
                        await asyncio.sleep(delay)
                    payload = self._select_payload(command_payloads, subdevice)
                    await self._send_payload(hub, payload, int(delay * 1000))
                    if len(command_payloads) > 1 and subdevice:
                        self._flags[subdevice] ^= 1
                    sent_any = True
            if sent_any:
                self._flag_storage.async_delay_save(
                    lambda: dict(self._flags),
                    FLAG_SAVE_DELAY,
                )

    async def async_learn_command(self, **kwargs: Any) -> None:
        hub = self.current_hub
        if not hub:
            raise RuntimeError(f"IR hub {self.hub.dev_id} is no longer available")
        await self._async_load_storage()
        commands = _ensure_list(kwargs.get(ATTR_COMMAND))
        if not commands:
            raise ValueError("command is required")
        subdevice = kwargs.get(ATTR_DEVICE)
        if not subdevice:
            raise ValueError("device is required")
        if kwargs.get(ATTR_COMMAND_TYPE) == "rf":
            raise ValueError("RF learning is not supported by this Tuya IR remote yet")
        alternative = bool(kwargs.get(ATTR_ALTERNATIVE, False))
        timeout = float(kwargs.get("timeout") or DEFAULT_LEARN_TIMEOUT)

        async with self._lock:
            for command in commands:
                learned = [
                    await self.runtime.local.async_learn_ir_code(
                        hub,
                        timeout=timeout,
                    )
                ]
                if alternative:
                    learned.append(
                        await self.runtime.local.async_learn_ir_code(
                            hub,
                            timeout=timeout,
                        )
                    )
                self._codes.setdefault(subdevice, {})[str(command)] = (
                    learned if len(learned) > 1 else learned[0]
                )
            await self._code_storage.async_save(self._codes)
            self.async_write_ha_state()

    async def async_delete_command(self, **kwargs: Any) -> None:
        await self._async_load_storage()
        commands = _ensure_list(kwargs.get(ATTR_COMMAND))
        subdevice = kwargs.get(ATTR_DEVICE)
        if not subdevice:
            raise ValueError("device is required")
        if subdevice not in self._codes:
            raise ValueError(f"Device not found: {subdevice}")
        for command in commands:
            self._codes[subdevice].pop(str(command), None)
        if not self._codes[subdevice]:
            self._codes.pop(subdevice, None)
            self._flags.pop(subdevice, None)
        await self._code_storage.async_save(self._codes)
        self._flag_storage.async_delay_save(lambda: dict(self._flags), FLAG_SAVE_DELAY)
        self.async_write_ha_state()

    async def _async_load_storage(self) -> None:
        if self._storage_loaded:
            return
        codes = await self._code_storage.async_load()
        flags = await self._flag_storage.async_load()
        if isinstance(codes, dict):
            self._codes = {
                str(device): {
                    str(command): value
                    for command, value in commands.items()
                    if isinstance(value, (str, list))
                }
                for device, commands in codes.items()
                if isinstance(commands, dict)
            }
        if isinstance(flags, dict):
            self._flags.update({str(key): int(value) for key, value in flags.items()})
        self._storage_loaded = True

    def _extract_payloads(
        self,
        commands: list[str],
        subdevice: str | None,
    ) -> list[list[IrCommandPayload]]:
        extracted: list[list[IrCommandPayload]] = []
        for command in commands:
            if command.startswith("dp201:"):
                extracted.append([_parse_dp201_payload(command[6:])])
                continue
            if command.startswith("json:"):
                extracted.append([_parse_dp201_payload(command[5:])])
                continue
            if command.startswith("b64:"):
                extracted.append([command[4:]])
                continue
            if command.startswith("tuya:"):
                extracted.append([command[5:]])
                continue
            if command.startswith("raw:"):
                extracted.append([command[4:]])
                continue
            if subdevice is None:
                raise ValueError("device must be specified for stored commands")
            try:
                stored = self._codes[subdevice][command]
            except KeyError as err:
                raise ValueError(
                    f"Command {command!r} not found for {subdevice!r}"
                ) from err
            if isinstance(stored, list):
                extracted.append([str(code) for code in stored])
            else:
                extracted.append([str(stored)])
        return extracted

    def _select_payload(
        self,
        payloads: list[IrCommandPayload],
        subdevice: str | None,
    ) -> IrCommandPayload:
        if len(payloads) <= 1 or not subdevice:
            return payloads[0]
        return payloads[self._flags[subdevice] % len(payloads)]

    async def _send_payload(
        self,
        hub: TuyaDeviceDescription,
        payload: IrCommandPayload,
        delay_ms: int,
    ) -> None:
        try:
            if isinstance(payload, dict):
                response = await self.runtime.local.async_send_ir_payload(hub, payload)
            else:
                response = await self.runtime.local.async_send_ir_code(
                    hub,
                    payload,
                    delay_ms=delay_ms,
                )
        except Exception as err:
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(f"Unable to send Tuya IR command: {err}") from err
        if isinstance(response, dict) and response.get("Error"):
            self._local_ok = False
            self._async_write_state_if_added()
            raise RuntimeError(
                f"Unable to send Tuya IR command: {response.get('Error')}"
            )
        self._local_ok = True
        self._async_write_state_if_added()
        _LOGGER.debug("Sent Tuya IR command via hub %s: %s", hub.dev_id, response)

    def _async_write_state_if_added(self) -> None:
        if self.entity_id:
            self.async_write_ha_state()


def _ensure_list(value: Iterable[str] | str | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _parse_dp201_payload(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as err:
        raise ValueError("DP201 payload must be valid JSON") from err
    if not isinstance(payload, dict):
        raise ValueError("DP201 payload must be a JSON object")
    return payload
