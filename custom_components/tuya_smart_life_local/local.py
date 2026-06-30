from __future__ import annotations

import asyncio
from dataclasses import fields
import ipaddress
import json
import logging
import socket
import time
import unicodedata
from collections.abc import Callable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers.storage import Store

from .models import TuyaDeviceDescription, TuyaIrAction, TuyaIrClimate, TuyaIrRemote

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORTS = (6666, 6667, 6699, 7000)
DISCOVERY_SCAN_SECONDS = 8
FORCE_SCAN_INTERVAL_SECONDS = 300
STATE_STREAM_TIMEOUT_SECONDS = 10
STATE_STREAM_RECONNECT_SECONDS = 5
STATE_STREAM_HEARTBEAT_SECONDS = 10
STATE_STREAM_REFRESH_DP_IDS = [4, 5, 6, 18, 19, 20]
LOCAL_CACHE_VERSION = 1
LOCAL_CACHE_KEY = "tuya_smart_life_local.local_cache"
SWITCH_BUTTON_DP_IDS = {str(dp_id) for dp_id in range(1, 9)}
FAN_PRODUCT_IDS = {"tqfl5ws2csdtdaak"}
FAN_POWER_DP_ID = "1"
FAN_SPEED_DP_ID = "3"
CHILD_PROTOCOL_FALLBACKS = ("3.5", "3.4", "3.3")
IR_HUB_DEFAULT_PROTOCOL_VERSION = "3.5"
NO_FALLBACK_RESPONSE = object()
BROADCAST_DEVICE_ID_KEYS = ("devId", "deviceId", "device_id", "id", "gwId")
BROADCAST_NODE_ID_KEYS = (
    "cid",
    "nodeId",
    "node_id",
    "node_id_hex",
    "subId",
    "sub_id",
)
BROADCAST_VERSION_KEYS = (
    "version",
    "ver",
    "pv",
    "protocolVersion",
    "protocol_version",
)
VALID_PROTOCOL_VERSIONS = ("3.1", "3.2", "3.3", "3.4", "3.5")
NON_BUTTON_NAME_PARTS = (
    "backlight",
    "child lock",
    "countdown",
    "do not disturb",
    "indicator",
    "led",
    "relay status",
)
IR_CLIMATE_CATEGORY_MARKERS = (
    "ac",
    "air",
    "aircondition",
    "air_condition",
    "climate",
    "conditioner",
    "hvac",
    "kt",
)
IR_FAN_CATEGORY_MARKERS = ("fan", "quat")
IR_LIGHT_CATEGORY_MARKERS = ("light", "den")
IR_MEDIA_REMOTE_KINDS = {"media_player"}
IR_BUTTON_REMOTE_KINDS = {"button", "unknown"}
IR_SEND_DP_ID = "201"
IR_RECEIVE_DP_ID = "202"
IR_LEARN_TIMEOUT_SECONDS = 30
IR_CLIMATE_ACTION_MARKERS = (
    "cool",
    "dry",
    "fan",
    "heat",
    "mode",
    "power",
    "temp",
    "temperature",
    "wind",
)
BINARY_CONTACT_CATEGORY_MARKERS = ("mcs", "contact", "door", "window", "cuasensor")
BINARY_MOTION_CATEGORY_MARKERS = ("pir", "motion", "movement", "body")
BINARY_PRESENCE_CATEGORY_MARKERS = ("hps", "presence", "occupancy", "human", "radar")
CONTACT_DP_MARKERS = ("doorcontact_state", "contact", "door", "window", "open")
MOTION_DP_MARKERS = ("pir", "motion", "movement", "body")
PRESENCE_DP_MARKERS = ("presence_state", "presence", "occupancy", "human", "radar")
BINARY_AUX_DP_MARKERS = (
    "battery",
    "bright",
    "humidity",
    "illuminance",
    "lux",
    "tamper",
    "temp",
    "voltage",
)
BINARY_PRIMARY_DP_IDS = {"1", "101"}
CONTEXT_BUTTON_CATEGORY_MARKERS = (
    "wxkg",
    "scene",
    "button",
    "switch",
    "remote",
    "context",
    "ngu canh",
    "nut",
)
CONTEXT_BUTTON_VALUES = {
    "single_click": "press",
    "single": "press",
    "press": "press",
    "click": "press",
    "single_press": "press",
    "double_click": "double",
    "double": "double",
    "double_press": "double",
    "long_press": "long",
    "long": "long",
    "hold": "long",
    "long_click": "long",
}
CONTEXT_BUTTON_IDLE_VALUES = {"scene", "", "none", "idle", "released"}
BINARY_ON_VALUES = {
    "1",
    "alarm",
    "detected",
    "motion",
    "movement",
    "occupied",
    "on",
    "open",
    "opened",
    "pir",
    "present",
    "presence",
    "true",
}
BINARY_OFF_VALUES = {
    "0",
    "clear",
    "close",
    "closed",
    "false",
    "no_motion",
    "none",
    "not_present",
    "off",
    "standby",
    "vacant",
}


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._callback(data, addr)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Tuya UDP discovery socket error: %s", exc)


class TuyaLocalRuntime:
    """Local Tuya discovery and command runtime."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(
            hass,
            LOCAL_CACHE_VERSION,
            LOCAL_CACHE_KEY,
        )
        self._local_cache: dict[str, dict[str, Any]] = {}
        self.devices: dict[str, TuyaDeviceDescription] = {}
        self.ir_actions: dict[str, TuyaIrAction] = {}
        self.transports: list[asyncio.DatagramTransport] = []
        self._tinytuya_devices: dict[str, Any] = {}
        self._stream_tinytuya_devices: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._scan_task: asyncio.Task[None] | None = None
        self._state_stream_tasks: dict[str, asyncio.Task[None]] = {}
        self._state_callbacks: list[Callable[[str, dict[str, Any]], None]] = []
        self._last_heartbeat: dict[str, float] = {}
        self._stream_synced: set[str] = set()
        self._last_force_scan = 0.0

    async def async_start(self) -> None:
        if self.transports:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            raw_devices = stored.get("devices")
            if isinstance(raw_devices, dict):
                self._local_cache = {
                    str(dev_id): metadata
                    for dev_id, metadata in raw_devices.items()
                    if isinstance(metadata, dict)
                }
        for port in DISCOVERY_PORTS:
            try:
                transport, _ = await self._create_udp_endpoint(port)
            except OSError as err:
                _LOGGER.warning("Unable to listen for Tuya UDP on %s: %s", port, err)
                continue
            self.transports.append(transport)
        self._scan_task = self.hass.loop.create_task(self._scan_loop())

    async def _create_udp_endpoint(
        self,
        port: int,
    ) -> tuple[asyncio.DatagramTransport, asyncio.DatagramProtocol]:
        try:
            return await self.hass.loop.create_datagram_endpoint(
                lambda: _DiscoveryProtocol(self._handle_datagram),
                local_addr=("0.0.0.0", port),
                reuse_port=True,
                allow_broadcast=True,
            )
        except TypeError:
            return await self.hass.loop.create_datagram_endpoint(
                lambda: _DiscoveryProtocol(self._handle_datagram),
                local_addr=("0.0.0.0", port),
                allow_broadcast=True,
            )

    async def async_stop(self) -> None:
        for task in self._state_stream_tasks.values():
            task.cancel()
        for task in list(self._state_stream_tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._state_stream_tasks.clear()
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
        for transport in self.transports:
            transport.close()
        self.transports.clear()
        for device in self._tinytuya_devices.values():
            _close_tinytuya_device(device)
        for device in self._stream_tinytuya_devices.values():
            _close_tinytuya_device(device)
        self._tinytuya_devices.clear()
        self._stream_tinytuya_devices.clear()
        self._last_heartbeat.clear()
        self._stream_synced.clear()

    async def _scan_loop(self) -> None:
        while True:
            try:
                await self.async_scan_once()
            except Exception:
                _LOGGER.debug("Tuya UDP scan failed", exc_info=True)
            await asyncio.sleep(60)

    async def async_scan_once(self) -> None:
        await self.hass.async_add_executor_job(self._scan_once)

    def _scan_once(self) -> None:
        try:
            import tinytuya
            from tinytuya import scanner
        except ImportError:
            return

        results = None
        scan_devices = _tinytuya_scan_devices(self.devices.values())
        force_networks: list[str] | bool = False
        now = time.monotonic()
        if scan_devices and now - self._last_force_scan >= FORCE_SCAN_INTERVAL_SECONDS:
            force_networks = _candidate_force_scan_networks(self.devices.values())
            self._last_force_scan = now

        try:
            if scan_devices:
                results = scanner.devices(
                    verbose=False,
                    scantime=DISCOVERY_SCAN_SECONDS,
                    color=False,
                    poll=False,
                    forcescan=force_networks,
                    discover=True,
                    wantids=[device["id"] for device in scan_devices],
                    assume_yes=True,
                    tuyadevices=scan_devices,
                )
            else:
                results = tinytuya.deviceScan(
                    verbose=False,
                    maxretry=2,
                    color=False,
                    poll=False,
                    forcescan=False,
                )
        except (AttributeError, TypeError):
            results = tinytuya.deviceScan(
                verbose=False,
                maxretry=2,
                color=False,
                poll=False,
                forcescan=False,
            )
        except Exception:
            _LOGGER.debug("TinyTuya deviceScan failed", exc_info=True)
            return

        if not isinstance(results, dict):
            return
        for ip, payload in results.items():
            if isinstance(payload, dict):
                self._apply_discovery_payload(payload, str(ip))

    def update_devices(self, devices: list[TuyaDeviceDescription]) -> None:
        existing = self.devices
        next_devices: dict[str, TuyaDeviceDescription] = {}

        # Preserve broadcast-discovered IP/version across cloud metadata refreshes.
        for device in devices:
            cached = self._local_cache.get(device.dev_id, {})
            cached_ip = _lan_ip(cached.get("ip"))
            if cached_ip and not _is_lan_ip(device.ip):
                device.ip = cached_ip
            cached_version = _valid_protocol_version(cached.get("protocol_version"))
            if cached_version and not device.protocol_version:
                device.protocol_version = cached_version
            if device.is_hub and not device.protocol_version:
                device.protocol_version = IR_HUB_DEFAULT_PROTOCOL_VERSION
            old = existing.get(device.dev_id)
            if old:
                if _is_lan_ip(old.ip) and old.ip != device.ip:
                    device.ip = old.ip
                if old.protocol_version:
                    device.protocol_version = old.protocol_version
                for field in fields(TuyaDeviceDescription):
                    setattr(old, field.name, getattr(device, field.name))
                next_devices[device.dev_id] = old
            else:
                next_devices[device.dev_id] = device

        self.devices = next_devices
        self._tinytuya_devices.clear()
        self._ensure_state_stream_tasks()
        self._save_local_cache()

    def update_ir_actions(self, actions: list[TuyaIrAction]) -> None:
        self.ir_actions = {action.unique_id: action for action in actions}

    def async_add_dps_listener(
        self,
        listener: Callable[[str, dict[str, Any]], None],
    ) -> CALLBACK_TYPE:
        self._state_callbacks.append(listener)

        def remove_listener() -> None:
            if listener in self._state_callbacks:
                self._state_callbacks.remove(listener)

        return remove_listener

    def _ensure_state_stream_tasks(self) -> None:
        desired = self._state_stream_device_ids()

        for dev_id, task in list(self._state_stream_tasks.items()):
            if dev_id not in desired:
                _LOGGER.debug("Stopping Tuya state stream task for %s", dev_id)
                task.cancel()
                self._state_stream_tasks.pop(dev_id, None)
                _close_tinytuya_device(self._stream_tinytuya_devices.pop(dev_id, None))
                self._last_heartbeat.pop(dev_id, None)
                self._stream_synced.discard(dev_id)

        for dev_id in desired:
            task = self._state_stream_tasks.get(dev_id)
            if task and not task.done():
                continue
            _LOGGER.debug("Starting Tuya state stream task for %s", dev_id)
            self._state_stream_tasks[dev_id] = self.hass.loop.create_task(
                self._state_stream_loop(dev_id)
            )

    def _state_stream_device_ids(self) -> set[str]:
        ids: set[str] = set()
        ir_hub_ids = {action.hub_dev_id for action in self.ir_actions.values()}
        for device in self.devices.values():
            if device.is_hub and not any(
                child.parent_dev_id == device.dev_id for child in self.devices.values()
            ) and device.dev_id not in ir_hub_ids and not _looks_like_ir_hub(device):
                continue
            if device.is_child:
                stream_dev_id = device.parent_dev_id
            else:
                stream_dev_id = device.dev_id
            stream_device = self.devices.get(stream_dev_id or "")
            if stream_device and stream_device.ip and stream_device.local_key:
                ids.add(stream_device.dev_id)
        return ids

    async def _state_stream_loop(self, dev_id: str) -> None:
        while dev_id in self.devices:
            try:
                payloads = await self.hass.async_add_executor_job(
                    self._receive_state_stream,
                    dev_id,
                )
                for payload in payloads:
                    self._handle_state_stream_payload(dev_id, payload)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.debug(
                    "Tuya state stream for %s failed: %s",
                    dev_id,
                    err,
                    exc_info=True,
                )
                _close_tinytuya_device(self._stream_tinytuya_devices.pop(dev_id, None))
                self._last_heartbeat.pop(dev_id, None)
                self._stream_synced.discard(dev_id)
                _LOGGER.debug(
                    "Tuya state stream for %s will reconnect in %ss",
                    dev_id,
                    STATE_STREAM_RECONNECT_SECONDS,
                )
                await asyncio.sleep(STATE_STREAM_RECONNECT_SECONDS)

    def _receive_state_stream(self, dev_id: str) -> list[dict[str, Any]]:
        device = self._stream_tinytuya_device(dev_id)
        if not device:
            return []

        payloads: list[dict[str, Any]] = []
        if dev_id not in self._stream_synced:
            self._stream_synced.add(dev_id)
            _LOGGER.debug("Tuya state stream %s initial refresh/sync started", dev_id)
            payloads.extend(self._sync_state_stream(dev_id, device))

        now = time.monotonic()
        if now - self._last_heartbeat.get(dev_id, 0.0) >= STATE_STREAM_HEARTBEAT_SECONDS:
            self._last_heartbeat[dev_id] = now
            try:
                import tinytuya

                heartbeat = device.generate_payload(tinytuya.HEART_BEAT)
                device.send(heartbeat)
                _LOGGER.debug("Tuya state stream %s heartbeat sent", dev_id)
            except Exception:
                _LOGGER.debug(
                    "Unable to send Tuya stream heartbeat for %s",
                    dev_id,
                    exc_info=True,
                )

        payload = device.receive()
        if isinstance(payload, dict):
            _LOGGER.debug(
                "Tuya state stream %s received %s",
                dev_id,
                _state_stream_payload_summary(payload),
            )
            payloads.append(payload)
        return payloads

    def _sync_state_stream(self, dev_id: str, stream_device: Any) -> list[dict[str, Any]]:
        """Prime a persistent Tuya LAN stream with one DPS query per endpoint."""
        try:
            import tinytuya
        except ImportError:
            return []

        root = self.devices.get(dev_id)
        if not root or not root.ip or not root.local_key:
            return []

        payloads: list[dict[str, Any]] = []
        if hasattr(stream_device, "updatedps"):
            try:
                response = stream_device.updatedps(STATE_STREAM_REFRESH_DP_IDS)
            except Exception:
                _LOGGER.debug(
                    "Unable to refresh Tuya state stream for %s",
                    dev_id,
                    exc_info=True,
                )
            else:
                if isinstance(response, dict):
                    _LOGGER.debug(
                        "Tuya state stream %s refresh response %s",
                        dev_id,
                        _state_stream_payload_summary(response),
                    )
                    payloads.append(response)
                else:
                    _LOGGER.debug(
                        "Tuya state stream %s refresh response type=%s value=%r",
                        dev_id,
                        type(response).__name__,
                        response,
                    )

        endpoints = [root] if (not root.is_hub or root.dps) else []
        endpoints.extend(
            child
            for child in self.devices.values()
            if child.parent_dev_id == dev_id and child.node_id
        )

        for endpoint in endpoints:
            if endpoint.dev_id == dev_id:
                tuya_device = stream_device
            else:
                tuya_device = self._make_tinytuya_device(
                    tinytuya,
                    endpoint,
                    root.ip,
                    root.local_key,
                    stream_device,
                    persistent=True,
                )
            try:
                response = tuya_device.status()
            except Exception:
                _LOGGER.debug(
                    "Unable to sync Tuya state stream for %s via %s",
                    endpoint.dev_id,
                    dev_id,
                    exc_info=True,
                )
                continue
            if isinstance(response, dict):
                if endpoint.dev_id != dev_id and endpoint.node_id:
                    response.setdefault("cid", endpoint.node_id)
                _LOGGER.debug(
                    "Tuya state stream %s sync endpoint=%s node=%s response=%s",
                    dev_id,
                    endpoint.dev_id,
                    endpoint.node_id,
                    _state_stream_payload_summary(response),
                )
                payloads.append(response)
            else:
                _LOGGER.debug(
                    "Tuya state stream %s sync endpoint=%s node=%s response type=%s value=%r",
                    dev_id,
                    endpoint.dev_id,
                    endpoint.node_id,
                    type(response).__name__,
                    response,
                )

        _LOGGER.debug(
            "Synced Tuya state stream for %s endpoints=%s payloads=%s",
            dev_id,
            len(endpoints),
            len(payloads),
        )
        return payloads

    def _handle_state_stream_payload(
        self,
        stream_dev_id: str,
        payload: dict[str, Any],
    ) -> None:
        dps = _dps_from_payload(payload)
        if not dps:
            _LOGGER.debug(
                "Tuya state stream %s ignored payload without DPS: %s",
                stream_dev_id,
                _state_stream_payload_summary(payload),
            )
            return
        target = self._state_stream_target_device(stream_dev_id, payload)
        if not target:
            _LOGGER.debug(
                "Tuya state stream %s could not map payload target: %s",
                stream_dev_id,
                _state_stream_payload_summary(payload),
            )
            return
        changed = _apply_device_dps(target, dps)
        if changed:
            _LOGGER.debug(
                "Tuya state stream updated %s from stream=%s dps=%s callbacks=%s",
                target.dev_id,
                stream_dev_id,
                changed,
                len(self._state_callbacks),
            )
            for listener in list(self._state_callbacks):
                self.hass.loop.call_soon_threadsafe(listener, target.dev_id, changed)
        else:
            _LOGGER.debug(
                "Tuya state stream %s mapped to %s but DPS unchanged: %s",
                stream_dev_id,
                target.dev_id,
                dps,
            )

    def _state_stream_target_device(
        self,
        stream_dev_id: str,
        payload: dict[str, Any],
    ) -> TuyaDeviceDescription | None:
        cid = payload.get("cid")
        data = payload.get("data")
        if cid is None and isinstance(data, dict):
            cid = data.get("cid")
        if cid is not None:
            candidates = [str(cid)]
            for device in self.devices.values():
                if not device.is_child:
                    continue
                if _identifier_matches(device.node_id, candidates):
                    _LOGGER.debug(
                        "Tuya state stream %s payload cid=%s matched child node_id=%s dev_id=%s",
                        stream_dev_id,
                        cid,
                        device.node_id,
                        device.dev_id,
                    )
                    return device
                if _identifier_matches(device.uuid, candidates):
                    _LOGGER.debug(
                        "Tuya state stream %s payload cid=%s matched child uuid dev_id=%s",
                        stream_dev_id,
                        cid,
                        device.dev_id,
                    )
                    return device
                if _identifier_matches(device.mac, candidates):
                    _LOGGER.debug(
                        "Tuya state stream %s payload cid=%s matched child mac dev_id=%s",
                        stream_dev_id,
                        cid,
                        device.dev_id,
                    )
                    return device
            _LOGGER.debug(
                "Tuya state stream %s payload cid=%s did not match any child",
                stream_dev_id,
                cid,
            )
        return self.devices.get(stream_dev_id)

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            import tinytuya
        except ImportError:
            return

        payload = None
        try:
            payload = tinytuya.decrypt_udp(data)
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode(errors="replace")
            if isinstance(payload, str):
                payload = json.loads(payload)
        except Exception:
            _LOGGER.debug("Unable to decrypt Tuya UDP broadcast", exc_info=True)
            return

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    self._apply_discovery_payload(item, addr[0])
            return
        if isinstance(payload, dict):
            self._apply_discovery_payload(payload, addr[0])

    def _apply_discovery_payload(self, payload: dict[str, Any], fallback_ip: str) -> None:
        for record, source_ip in _iter_broadcast_records(payload, fallback_ip):
            self._apply_discovery_record(record, source_ip)

    def _apply_discovery_record(self, payload: dict[str, Any], fallback_ip: str) -> None:
        device = self._broadcast_device(payload)
        if not device:
            return

        ip = _lan_ip(payload.get("ip") or fallback_ip)
        version = _broadcast_protocol_version(payload)
        changed = False

        if ip and not device.is_child and ip != device.ip:
            device.ip = str(ip)
            changed = True
        if version and version != device.protocol_version:
            device.protocol_version = version
            changed = True
        if changed:
            self._clear_tinytuya_cache_for(device.dev_id)
            stream_dev_id = device.parent_dev_id if device.is_child else device.dev_id
            if stream_dev_id:
                _close_tinytuya_device(
                    self._stream_tinytuya_devices.pop(stream_dev_id, None)
                )
                self._last_heartbeat.pop(stream_dev_id, None)
                self._stream_synced.discard(stream_dev_id)
            _LOGGER.debug(
                "Tuya broadcast updated %s ip=%s version=%s",
                device.dev_id,
                device.ip,
                device.protocol_version,
            )
            self._ensure_state_stream_tasks()
            self._save_local_cache()

    def _save_local_cache(self) -> None:
        devices: dict[str, dict[str, str]] = {}
        for dev_id, device in self.devices.items():
            metadata: dict[str, str] = {}
            ip = _lan_ip(device.ip)
            if ip:
                metadata["ip"] = ip
            version = _valid_protocol_version(device.protocol_version)
            if version:
                metadata["protocol_version"] = version
            if metadata:
                devices[dev_id] = metadata
        if devices == self._local_cache:
            return
        self._local_cache = devices
        self.hass.loop.call_soon_threadsafe(
            self.hass.async_create_task,
            self._store.async_save({"devices": devices}),
        )

    def _broadcast_device(
        self,
        payload: dict[str, Any],
    ) -> TuyaDeviceDescription | None:
        node_candidates = _broadcast_candidates(payload, BROADCAST_NODE_ID_KEYS)
        if node_candidates:
            for device in self.devices.values():
                if not device.is_child:
                    continue
                if _identifier_matches(device.node_id, node_candidates):
                    return device
                if _identifier_matches(device.uuid, node_candidates):
                    return device
                if _identifier_matches(device.mac, node_candidates):
                    return device

        for candidate in _broadcast_candidates(payload, BROADCAST_DEVICE_ID_KEYS):
            device = self.devices.get(candidate)
            if device:
                return device

        return None

    def _clear_tinytuya_cache_for(self, dev_id: str) -> None:
        self._tinytuya_devices.pop(dev_id, None)
        for device in self.devices.values():
            if device.parent_dev_id == dev_id:
                self._tinytuya_devices.pop(device.dev_id, None)

    def switch_button_dps(self) -> list[tuple[TuyaDeviceDescription, str, bool, str]]:
        items: list[tuple[TuyaDeviceDescription, str, bool, str]] = []
        for device in self.devices.values():
            if not device.local_controllable or device.is_hub:
                continue
            for dp_id, value in device.dps.items():
                if _is_fan_device(device) and str(dp_id) == FAN_POWER_DP_ID:
                    continue
                if _binary_sensor_kind(device, str(dp_id)):
                    continue
                if isinstance(value, bool) and _is_switch_button_dp(device, dp_id):
                    items.append(
                        (device, dp_id, value, _switch_button_label(device, dp_id))
                    )
        return items

    def binary_sensor_dps(
        self,
    ) -> list[tuple[TuyaDeviceDescription, str, bool, str, str]]:
        items: list[tuple[TuyaDeviceDescription, str, bool, str, str]] = []
        for device in self.devices.values():
            if not device.local_controllable or device.is_hub:
                continue
            for dp_id, value in device.dps.items():
                kind = _binary_sensor_kind(device, dp_id)
                if not kind:
                    continue
                state = _normalize_binary_sensor_value(value, kind)
                if state is None:
                    continue
                items.append(
                    (
                        device,
                        str(dp_id),
                        state,
                        kind,
                        _binary_sensor_label(device, dp_id, kind),
                    )
                )
        return items

    def context_button_sensors(
        self,
    ) -> list[tuple[TuyaDeviceDescription, str | None, list[str]]]:
        items: list[tuple[TuyaDeviceDescription, str | None, list[str]]] = []
        for device in self.devices.values():
            if not device.local_controllable or device.is_hub:
                continue
            channels = _context_button_channels(device)
            if not channels:
                continue
            items.append((device, _context_button_state(device), channels))
        return items

    def fan_devices(self) -> list[TuyaDeviceDescription]:
        return [
            device
            for device in self.devices.values()
            if device.local_controllable and not device.is_hub and _is_fan_device(device)
        ]

    def hub_devices(self) -> list[TuyaDeviceDescription]:
        return [device for device in self.devices.values() if device.is_hub]

    def ir_hub_devices(self) -> list[TuyaDeviceDescription]:
        hub_ids = {action.hub_dev_id for action in self.ir_actions.values()}
        hubs = [
            device
            for device in self.devices.values()
            if device.is_hub and (device.dev_id in hub_ids or _looks_like_ir_hub(device))
        ]
        return sorted(hubs, key=lambda device: (device.home_name, device.name))

    def boolean_dps(self) -> list[tuple[TuyaDeviceDescription, str, bool]]:
        return [
            (device, dp_id, value)
            for device, dp_id, value, _ in self.switch_button_dps()
        ]

    def ir_action_buttons(self) -> list[TuyaIrAction]:
        return sorted(
            (
                action
                for action in self.ir_actions.values()
                if _is_ir_button_action(action)
            ),
            key=lambda action: (action.home_name, action.remote_name, action.action_name),
        )

    def ir_climates(self) -> list[TuyaIrClimate]:
        grouped: dict[str, list[TuyaIrAction]] = {}
        for action in self.ir_actions.values():
            grouped.setdefault(action.remote_id, []).append(action)

        climates: list[TuyaIrClimate] = []
        for remote_id, actions in grouped.items():
            if not _is_ir_climate_remote(actions):
                continue
            first = actions[0]
            climates.append(
                TuyaIrClimate(
                    remote_id=remote_id,
                    remote_name=first.remote_name,
                    home_id=first.home_id,
                    home_name=first.home_name,
                    hub_dev_id=first.hub_dev_id,
                    actions=sorted(actions, key=lambda action: action.action_name),
                    product_id=first.product_id,
                    category=first.category,
                )
            )
        return sorted(
            climates,
            key=lambda climate: (climate.home_name, climate.remote_name),
        )

    def ir_fans(self) -> list[TuyaIrRemote]:
        return self._ir_remotes_for_kinds({"fan"})

    def ir_lights(self) -> list[TuyaIrRemote]:
        return self._ir_remotes_for_kinds({"light"})

    def ir_media_players(self) -> list[TuyaIrRemote]:
        return self._ir_remotes_for_kinds(IR_MEDIA_REMOTE_KINDS)

    def _ir_remotes_for_kinds(self, kinds: set[str]) -> list[TuyaIrRemote]:
        grouped: dict[str, list[TuyaIrAction]] = {}
        for action in self.ir_actions.values():
            kind = _ir_remote_kind([action])
            if kind in kinds:
                grouped.setdefault(action.remote_id, []).append(action)

        remotes: list[TuyaIrRemote] = []
        for remote_id, actions in grouped.items():
            first = actions[0]
            remotes.append(
                TuyaIrRemote(
                    remote_id=remote_id,
                    remote_name=first.remote_name,
                    home_id=first.home_id,
                    home_name=first.home_name,
                    hub_dev_id=first.hub_dev_id,
                    kind=_ir_remote_kind(actions),
                    actions=sorted(actions, key=lambda action: action.action_name),
                    product_id=first.product_id,
                    category=first.category,
                    dev_type_id=first.dev_type_id,
                    brand_name=_ir_remote_brand_name(actions),
                    raw=_ir_remote_raw(actions),
                )
            )
        return sorted(remotes, key=lambda remote: (remote.home_name, remote.remote_name))

    async def async_status(self, device: TuyaDeviceDescription) -> dict[str, Any]:
        return await self.hass.async_add_executor_job(self._status, device.dev_id)

    async def async_set_dp(
        self,
        device: TuyaDeviceDescription,
        dp_id: str,
        value: Any,
    ) -> Any:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._set_dp,
                device.dev_id,
                int(dp_id),
                value,
            )

    async def async_publish_ir_action(self, action: TuyaIrAction) -> Any:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._publish_ir_action,
                action,
            )

    async def async_send_ir_code(
        self,
        hub: TuyaDeviceDescription,
        code: str,
        *,
        delay_ms: int = 300,
    ) -> Any:
        payload = {
            "control": "send_ir",
            "head": "",
            # Tuya learned IR codes are sent with a non-zero leading marker.
            # Hubs discard the marker and use the remaining base64 pulse data.
            "key1": f"1{code}",
            "type": 0,
            "delay": int(delay_ms),
        }
        return await self.async_send_ir_payload(hub, payload)

    async def async_send_ir_payload(
        self,
        hub: TuyaDeviceDescription,
        payload: dict[str, Any],
    ) -> Any:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._send_ir_payload,
                hub.dev_id,
                payload,
            )

    async def async_learn_ir_code(
        self,
        hub: TuyaDeviceDescription,
        *,
        timeout: float = IR_LEARN_TIMEOUT_SECONDS,
    ) -> str:
        hub = self.devices.get(hub.dev_id) or hub
        hub.dps[IR_RECEIVE_DP_ID] = None
        wait_task = self.hass.loop.create_task(
            self._async_wait_for_ir_code(hub.dev_id, timeout)
        )
        try:
            await self.async_send_ir_payload(hub, {"control": "study"})
            return await wait_task
        finally:
            if not wait_task.done():
                wait_task.cancel()
                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass
            try:
                await self.async_send_ir_payload(hub, {"control": "study_exit"})
            except Exception:
                _LOGGER.debug(
                    "Unable to stop Tuya IR learning for %s",
                    hub.dev_id,
                    exc_info=True,
                )

    async def _async_wait_for_ir_code(self, hub_dev_id: str, timeout: float) -> str:
        hub = self.devices.get(hub_dev_id)
        previous = hub.dps.get(IR_RECEIVE_DP_ID) if hub else None
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()

        def listener(dev_id: str, changed: dict[str, Any]) -> None:
            if dev_id != hub_dev_id or IR_RECEIVE_DP_ID not in changed or future.done():
                return
            code = changed.get(IR_RECEIVE_DP_ID)
            if code and code != previous:
                future.set_result(str(code))

        remove_listener = self.async_add_dps_listener(listener)
        try:
            if hub:
                current = hub.dps.get(IR_RECEIVE_DP_ID)
                if current and current != previous:
                    return str(current)
            return await asyncio.wait_for(future, timeout)
        finally:
            remove_listener()

    def _status(self, dev_id: str) -> dict[str, Any]:
        response = self._status_once(dev_id)
        if _is_key_or_version_error(response):
            fallback = self._try_child_protocol_fallback(dev_id, None, None)
            if fallback is not NO_FALLBACK_RESPONSE:
                response = fallback
        if isinstance(response, dict):
            return response
        return {}

    def _status_once(self, dev_id: str) -> Any:
        device = self._tinytuya_device(dev_id)
        if not device:
            raise RuntimeError(f"Device {dev_id} is missing local metadata or IP")
        return device.status()

    def _set_dp(self, dev_id: str, dp_id: int, value: Any) -> Any:
        try:
            stream_response = self._set_dp_via_state_stream(dev_id, dp_id, value)
        except Exception:
            _LOGGER.debug(
                "Unable to send Tuya DP %s for %s via state stream",
                dp_id,
                dev_id,
                exc_info=True,
            )
            stream_response = NO_FALLBACK_RESPONSE
            self._release_state_stream_for_command(dev_id)
        if stream_response is not NO_FALLBACK_RESPONSE:
            return stream_response
        self._release_state_stream_for_command(dev_id)
        response = self._set_dp_once(dev_id, dp_id, value)
        if _is_key_or_version_error(response):
            fallback = self._try_child_protocol_fallback(dev_id, dp_id, value)
            if fallback is not NO_FALLBACK_RESPONSE:
                return fallback
        return response

    def _set_dp_via_state_stream(self, dev_id: str, dp_id: int, value: Any) -> Any:
        stream_dev_id = self._command_stream_device_id(dev_id)
        if not stream_dev_id:
            return NO_FALLBACK_RESPONSE
        stream_device = self._stream_tinytuya_devices.get(stream_dev_id)
        if not stream_device:
            return NO_FALLBACK_RESPONSE

        target = self.devices.get(dev_id)
        parent = self.devices.get(stream_dev_id)
        if not target or not parent or not parent.ip or not parent.local_key:
            return NO_FALLBACK_RESPONSE

        if target.dev_id == stream_dev_id:
            tuya_device = stream_device
        else:
            try:
                import tinytuya
            except ImportError:
                return NO_FALLBACK_RESPONSE
            tuya_device = self._make_tinytuya_device(
                tinytuya,
                target,
                parent.ip,
                parent.local_key,
                stream_device,
                persistent=True,
            )

        _LOGGER.debug(
            "Sending Tuya DP %s for %s via state stream %s",
            dp_id,
            dev_id,
            stream_dev_id,
        )
        if hasattr(tuya_device, "set_value"):
            return _call_tinytuya_writer(
                tuya_device.set_value,
                dp_id,
                value,
                nowait=True,
            )
        try:
            return tuya_device.set_status(value, switch=dp_id, nowait=True)
        except TypeError:
            return tuya_device.set_status(value, switch=dp_id)

    def _set_dp_once(self, dev_id: str, dp_id: int, value: Any) -> Any:
        device = self._tinytuya_device(dev_id)
        if not device:
            raise RuntimeError(f"Device {dev_id} is missing local metadata or IP")
        if hasattr(device, "set_value"):
            return device.set_value(dp_id, value)
        return device.set_status(value, switch=dp_id)

    def _publish_ir_action(self, action: TuyaIrAction | str) -> Any:
        if isinstance(action, str):
            found = self.ir_actions.get(action)
            if not found:
                raise RuntimeError(f"IR action {action} is no longer available")
            action = found
        hub = self.devices.get(action.hub_dev_id)
        if not hub:
            raise RuntimeError(f"IR hub {action.hub_dev_id} is no longer available")
        self._release_state_stream_for_command(hub.dev_id)
        response = self._publish_ir_dps_once(action)
        if _is_key_or_version_error(response) or _is_unexpected_payload_error(response):
            _LOGGER.debug(
                "Retrying Tuya IR action %s via hub %s after response %s",
                action.action_name,
                action.hub_dev_id,
                response,
            )
            self._tinytuya_devices.pop(hub.dev_id, None)
            response = self._publish_ir_dps_once(action)
        if _is_key_or_version_error(response) and _is_dp201_ir_action(action):
            _LOGGER.warning(
                "Tuya IR hub %s returned a key/version response after publishing %s; "
                "treating the DP201 fire-and-forget IR command as sent: %s",
                action.hub_dev_id,
                action.action_name,
                response,
            )
            return None
        if _is_unexpected_payload_error(response):
            _LOGGER.warning(
                "Tuya IR hub %s returned an unexpected payload after publishing %s; "
                "treating the fire-and-forget IR command as sent: %s",
                action.hub_dev_id,
                action.action_name,
                response,
            )
            return None
        return response

    def _publish_ir_dps_once(self, action: TuyaIrAction) -> Any:
        device = self._tinytuya_device(action.hub_dev_id)
        if not device:
            raise RuntimeError(
                f"IR hub {action.hub_dev_id} is missing local metadata or IP"
            )

        normalized_dps = _normalize_command_dps(action.action_dps)
        if set(normalized_dps) == {"201"}:
            _close_tinytuya_device(self._tinytuya_devices.pop(action.hub_dev_id, None))
            device = self._tinytuya_device(action.hub_dev_id)
            if not device:
                raise RuntimeError(
                    f"IR hub {action.hub_dev_id} is missing local metadata or IP"
                )
        _LOGGER.debug(
            "Publishing Tuya IR action %s to hub %s for remote %s with DPS %s",
            action.action_name,
            action.hub_dev_id,
            action.remote_id,
            normalized_dps,
        )
        # IR hubs accept the app's DP 201 payload reliably through individual writes.
        # Some hubs acknowledge multi-DP writes without actually emitting IR.
        if set(normalized_dps) == {"201"}:
            if hasattr(device, "set_value"):
                return _call_tinytuya_writer(
                    device.set_value,
                    201,
                    normalized_dps["201"],
                    nowait=True,
                )
            return _call_tinytuya_writer(
                device.set_status,
                normalized_dps["201"],
                switch=201,
                nowait=True,
            )
        if hasattr(device, "set_multiple_values"):
            return _call_tinytuya_writer(
                device.set_multiple_values,
                normalized_dps,
                nowait=False,
            )
        if hasattr(device, "set_status_multiple"):
            return device.set_status_multiple(normalized_dps)

        response: Any = None
        for dp_id, value in normalized_dps.items():
            switch = int(dp_id) if str(dp_id).isdecimal() else dp_id
            if hasattr(device, "set_value"):
                response = _call_tinytuya_writer(
                    device.set_value,
                    switch,
                    value,
                    nowait=False,
                )
            else:
                response = device.set_status(value, switch=switch)
        return response

    def _send_ir_payload(self, hub_dev_id: str, payload: dict[str, Any]) -> Any:
        hub = self.devices.get(hub_dev_id)
        if not hub:
            raise RuntimeError(f"IR hub {hub_dev_id} is no longer available")
        self._release_state_stream_for_command(hub.dev_id)
        value = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        _close_tinytuya_device(self._tinytuya_devices.pop(hub.dev_id, None))
        response = self._set_dp_once(hub.dev_id, int(IR_SEND_DP_ID), value)
        if _is_key_or_version_error(response) or _is_unexpected_payload_error(response):
            _LOGGER.debug(
                "Retrying Tuya IR payload via hub %s after response %s",
                hub.dev_id,
                response,
            )
            _close_tinytuya_device(self._tinytuya_devices.pop(hub.dev_id, None))
            response = self._set_dp_once(hub.dev_id, int(IR_SEND_DP_ID), value)
        if _is_key_or_version_error(response) or _is_unexpected_payload_error(response):
            _LOGGER.warning(
                "Tuya IR hub %s returned %s after DP201 payload; treating "
                "fire-and-forget command as sent",
                hub.dev_id,
                response,
            )
            return None
        return response

    def _try_child_protocol_fallback(
        self,
        dev_id: str,
        dp_id: int | None,
        value: Any,
    ) -> Any:
        device = self.devices.get(dev_id)
        if not device or not device.is_child:
            return NO_FALLBACK_RESPONSE
        parent = self.devices.get(device.parent_dev_id or "")
        if not parent or not parent.ip or not parent.local_key:
            return NO_FALLBACK_RESPONSE

        original_parent_version = parent.protocol_version
        original_child_version = device.protocol_version
        last_response: Any = None
        for version in _child_protocol_candidates(
            device.protocol_version,
            parent.protocol_version,
        ):
            parent.protocol_version = version
            device.protocol_version = version
            self._tinytuya_devices.pop(parent.dev_id, None)
            self._tinytuya_devices.pop(device.dev_id, None)
            response = (
                self._status_once(dev_id)
                if dp_id is None
                else self._set_dp_once(dev_id, dp_id, value)
            )
            last_response = response
            if not _is_key_or_version_error(response):
                _LOGGER.debug(
                    "Tuya child %s worked with protocol %s via hub %s",
                    dev_id,
                    version,
                    parent.dev_id,
                )
                return response

        parent.protocol_version = original_parent_version
        device.protocol_version = original_child_version
        self._tinytuya_devices.pop(parent.dev_id, None)
        self._tinytuya_devices.pop(device.dev_id, None)
        return last_response

    def _release_state_stream_for_command(self, dev_id: str) -> None:
        stream_dev_id = self._command_stream_device_id(dev_id)
        if not stream_dev_id:
            return
        stream_device = self._stream_tinytuya_devices.pop(stream_dev_id, None)
        if not stream_device:
            return
        _LOGGER.debug(
            "Closing Tuya state stream %s before command for %s",
            stream_dev_id,
            dev_id,
        )
        _close_tinytuya_device(stream_device)
        time.sleep(1.0)
        self._last_heartbeat.pop(stream_dev_id, None)
        self._stream_synced.discard(stream_dev_id)
        self._tinytuya_devices.pop(stream_dev_id, None)
        for device in self.devices.values():
            if device.parent_dev_id == stream_dev_id:
                self._tinytuya_devices.pop(device.dev_id, None)

    def _command_stream_device_id(self, dev_id: str) -> str | None:
        device = self.devices.get(dev_id)
        if not device:
            return None
        return device.parent_dev_id if device.is_child else device.dev_id

    def _tinytuya_device(self, dev_id: str) -> Any:
        existing = self._tinytuya_devices.get(dev_id)
        if existing:
            return existing

        device = self.devices.get(dev_id)
        if not device:
            return None

        try:
            import tinytuya
        except ImportError as err:
            raise RuntimeError("tinytuya is not installed") from err

        if device.is_child:
            parent = self.devices.get(device.parent_dev_id or "")
            if not parent or not parent.ip or not parent.local_key:
                return None
            parent_obj = self._tinytuya_device(parent.dev_id)
            if not parent_obj:
                return None
            tinytuya_device = self._make_tinytuya_device(
                tinytuya,
                device,
                parent.ip,
                parent.local_key,
                parent_obj,
            )
        else:
            if not device.ip or not device.local_key:
                return None
            tinytuya_device = self._make_tinytuya_device(
                tinytuya,
                device,
                device.ip,
                device.local_key,
                None,
            )

        self._tinytuya_devices[dev_id] = tinytuya_device
        return tinytuya_device

    def _stream_tinytuya_device(self, dev_id: str) -> Any:
        existing = self._stream_tinytuya_devices.get(dev_id)
        if existing:
            return existing

        device = self.devices.get(dev_id)
        if not device or device.is_child or not device.ip or not device.local_key:
            return None

        try:
            import tinytuya
        except ImportError as err:
            raise RuntimeError("tinytuya is not installed") from err

        tinytuya_device = self._make_tinytuya_device(
            tinytuya,
            device,
            device.ip,
            device.local_key,
            None,
            persistent=True,
        )
        _LOGGER.debug(
            "Opening Tuya state stream for %s ip=%s version=%s",
            dev_id,
            device.ip,
            device.protocol_version,
        )
        self._stream_tinytuya_devices[dev_id] = tinytuya_device
        return tinytuya_device

    @staticmethod
    def _make_tinytuya_device(
        tinytuya: Any,
        device: TuyaDeviceDescription,
        ip: str,
        local_key: str,
        parent: Any | None,
        persistent: bool = False,
    ) -> Any:
        version = _protocol_version(device.protocol_version)
        kwargs: dict[str, Any] = {"version": version}
        if parent is not None:
            kwargs["parent"] = parent
            if device.node_id:
                kwargs["cid"] = device.node_id
                kwargs["node_id"] = device.node_id

        try:
            tuya_device = tinytuya.OutletDevice(
                device.dev_id,
                ip,
                local_key,
                **kwargs,
            )
        except TypeError:
            kwargs.pop("node_id", None)
            try:
                tuya_device = tinytuya.OutletDevice(
                    device.dev_id,
                    ip,
                    local_key,
                    **kwargs,
                )
            except TypeError:
                kwargs.pop("cid", None)
                kwargs.pop("parent", None)
                tuya_device = tinytuya.OutletDevice(
                    device.dev_id,
                    ip,
                    local_key,
                    **kwargs,
                )

        if hasattr(tuya_device, "set_version"):
            tuya_device.set_version(version)
        if hasattr(tuya_device, "set_socketPersistent"):
            tuya_device.set_socketPersistent(persistent)
        if hasattr(tuya_device, "set_socketNODELAY"):
            tuya_device.set_socketNODELAY(True)
        if persistent and hasattr(tuya_device, "set_socketRetryLimit"):
            tuya_device.set_socketRetryLimit(1)
        if persistent and hasattr(tuya_device, "set_socketTimeout"):
            tuya_device.set_socketTimeout(STATE_STREAM_TIMEOUT_SECONDS)
        return tuya_device


def _iter_broadcast_records(
    payload: dict[str, Any],
    fallback_ip: str,
) -> list[tuple[dict[str, Any], str]]:
    records: list[tuple[dict[str, Any], str]] = []
    pending: list[tuple[Any, str]] = [(payload, fallback_ip)]
    while pending:
        item, source_ip = pending.pop()
        if isinstance(item, list):
            pending.extend((value, source_ip) for value in item)
            continue
        if not isinstance(item, dict):
            continue

        item_ip = _lan_ip(item.get("ip")) or source_ip
        records.append((item, item_ip))
        for value in item.values():
            if isinstance(value, (dict, list)):
                pending.append((value, item_ip))
    return records


def _broadcast_candidates(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> list[str]:
    candidates: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            candidates.append(text)
    return candidates


def _identifier_matches(value: str | None, candidates: list[str]) -> bool:
    if not value:
        return False
    value_forms = set(_identifier_forms(value))
    return any(
        value_forms.intersection(_identifier_forms(candidate))
        for candidate in candidates
    )


def _identifier_forms(value: Any) -> tuple[str, ...]:
    text = str(value).strip()
    if not text:
        return ()
    compact = text.replace(":", "").replace("-", "").lower()
    lower = text.lower()
    if compact == lower:
        return (text, lower)
    return (text, lower, compact)


def _broadcast_protocol_version(payload: dict[str, Any]) -> str | None:
    for key in BROADCAST_VERSION_KEYS:
        version = _valid_protocol_version(payload.get(key))
        if version:
            return version
    return None


def _valid_protocol_version(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for version in VALID_PROTOCOL_VERSIONS:
        if text == version or text.startswith(f"{version}."):
            return version
    return None


def _protocol_version(value: str | None) -> float:
    if not value:
        return 3.3
    parts = str(value).strip().split(".")
    try:
        return float(".".join(parts[:2]))
    except ValueError:
        _LOGGER.debug("Unknown Tuya protocol version %s, falling back to 3.3", value)
        return 3.3


def _child_protocol_candidates(*preferred: str | None) -> list[str]:
    versions: list[str] = []
    for value in (*preferred, *CHILD_PROTOCOL_FALLBACKS):
        version = _valid_protocol_version(value)
        if version and version not in versions:
            versions.append(version)
    return versions


def _is_key_or_version_error(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    text = " ".join(
        str(response.get(key) or "")
        for key in ("Error", "Err", "error", "message", "Payload")
    ).lower()
    return "key or version" in text


def _is_unexpected_payload_error(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    text = " ".join(
        str(response.get(key) or "")
        for key in ("Error", "Err", "error", "message", "Payload")
    ).lower()
    return "unexpected payload" in text


def _is_dp201_ir_action(action: TuyaIrAction) -> bool:
    return set(map(str, action.action_dps)) == {"201"}


def _call_tinytuya_writer(method: Any, *args: Any, nowait: bool, **kwargs: Any) -> Any:
    try:
        return method(*args, nowait=nowait, **kwargs)
    except TypeError:
        return method(*args, **kwargs)


def _ir_action_schema_kind(action: TuyaIrAction) -> str | None:
    schema = action.raw.get("schema") if isinstance(action.raw, dict) else None
    if isinstance(schema, dict):
        kind = schema.get("kind")
        return str(kind) if kind else None
    return None


def _is_ir_button_action(action: TuyaIrAction) -> bool:
    kind = _ir_remote_kind([action])
    if kind in {"climate", "fan", "light"}:
        return False
    # Media remotes keep their raw keys as buttons because HA's media_player
    # model only covers a small command subset.
    return _ir_action_schema_kind(action) != "climate"


def _ir_remote_kind(actions: list[TuyaIrAction]) -> str:
    explicit = next((action.remote_kind for action in actions if action.remote_kind), None)
    if explicit:
        return explicit
    if _is_ir_climate_remote(actions):
        return "climate"
    category = _ascii_fold(" ".join(action.category or "" for action in actions)).lower()
    if any(marker in category for marker in IR_FAN_CATEGORY_MARKERS):
        return "fan"
    if any(marker in category for marker in IR_LIGHT_CATEGORY_MARKERS):
        return "light"
    text = _ascii_fold(
        " ".join(
            [
                actions[0].remote_name if actions else "",
                category,
                " ".join(action.action_name for action in actions),
            ]
        )
    ).lower()
    if any(marker in text for marker in IR_FAN_CATEGORY_MARKERS):
        return "fan"
    if any(marker in text for marker in IR_LIGHT_CATEGORY_MARKERS):
        return "light"
    if any(
        marker in text
        for marker in (
            "tv",
            "set top",
            "stb",
            "projector",
            "audio",
            "dvd",
            "box",
        )
    ):
        return "media_player"
    return "button"


def _ir_remote_brand_name(actions: list[TuyaIrAction]) -> str | None:
    for action in actions:
        raw = action.raw if isinstance(action.raw, dict) else {}
        brand = raw.get("brandName")
        if brand:
            return str(brand)
        remote = raw.get("remote")
        if isinstance(remote, dict):
            brand = remote.get("brandName") or remote.get("brand_name")
            if brand:
                return str(brand)
    return None


def _ir_remote_raw(actions: list[TuyaIrAction]) -> dict[str, Any]:
    for action in actions:
        raw = action.raw if isinstance(action.raw, dict) else {}
        remote = raw.get("remote")
        if isinstance(remote, dict):
            return remote
    return {}


def _is_ir_climate_remote(actions: list[TuyaIrAction]) -> bool:
    if not actions:
        return False
    if any(action.remote_kind == "climate" or action.dev_type_id == 5 for action in actions):
        return True
    category = " ".join(action.category or "" for action in actions).lower()
    if any(marker in category for marker in IR_CLIMATE_CATEGORY_MARKERS):
        return True

    remote_name = actions[0].remote_name.lower()
    if "air " in remote_name or remote_name.startswith("air"):
        return True

    text = " ".join(
        " ".join(
            [
                " ".join(action.action_dps),
                " ".join(action.report_dps),
                action.action_name,
                json.dumps(action.raw, ensure_ascii=False, default=str),
            ]
        )
        for action in actions
    ).lower()
    if not any(marker in text for marker in ("temp", "temperature")):
        return False
    return any(marker in text for marker in IR_CLIMATE_ACTION_MARKERS)


def _normalize_command_dps(dps: dict[str, Any]) -> dict[Any, Any]:
    normalized: dict[Any, Any] = {}
    for dp_id, value in dps.items():
        key: Any = int(dp_id) if str(dp_id).isdecimal() else str(dp_id)
        normalized[key] = value
    return normalized


def _dps_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    dps = payload.get("dps")
    if not isinstance(dps, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            dps = data.get("dps")
    if not isinstance(dps, dict):
        return {}
    return {str(key): value for key, value in dps.items()}


def _state_stream_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    nested_dps = data.get("dps") if isinstance(data, dict) else None
    return {
        "devId": payload.get("devId"),
        "cid": payload.get("cid")
        or (data.get("cid") if isinstance(data, dict) else None),
        "t": payload.get("t") or (data.get("t") if isinstance(data, dict) else None),
        "dps": payload.get("dps") if isinstance(payload.get("dps"), dict) else nested_dps,
        "keys": sorted(str(key) for key in payload.keys()),
        "data_keys": sorted(str(key) for key in data.keys())
        if isinstance(data, dict)
        else None,
        "error": payload.get("Error") or payload.get("Err") or payload.get("error"),
    }


def _apply_device_dps(
    device: TuyaDeviceDescription,
    dps: dict[str, Any],
) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for dp_id, value in dps.items():
        key = str(dp_id)
        if device.dps.get(key) != value:
            device.dps[key] = value
            changed[key] = value
    return changed


def _close_tinytuya_device(device: Any | None) -> None:
    if not device:
        return
    close = getattr(device, "close", None)
    if callable(close):
        try:
            close()
            return
        except Exception:
            pass
    sock = getattr(device, "socket", None)
    if sock:
        try:
            sock.close()
        except Exception:
            pass
        try:
            device.socket = None
        except Exception:
            pass


def _is_fan_device(device: TuyaDeviceDescription) -> bool:
    product_id = (device.product_id or "").strip().lower()
    if product_id in FAN_PRODUCT_IDS:
        return True
    name = _ascii_fold(device.name).strip().lower()
    if ("fan" in name or "quat" in name) and isinstance(
        device.dps.get(FAN_POWER_DP_ID), bool
    ):
        return True
    return False


def _looks_like_ir_hub(device: TuyaDeviceDescription) -> bool:
    if not device.is_hub:
        return False
    if IR_SEND_DP_ID in device.dps or IR_RECEIVE_DP_ID in device.dps:
        return True
    text = _device_text(device)
    return any(
        marker in text
        for marker in (
            "infrared",
            "ir blaster",
            "ir remote",
            "remote control",
            "universal remote",
            "hong ngoai",
            "irrf",
        )
    )


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return normalized.encode("ascii", "ignore").decode("ascii")


def _device_text(device: TuyaDeviceDescription) -> str:
    raw = device.raw if isinstance(device.raw, dict) else {}
    values: list[str] = [
        device.name,
        device.product_id or "",
        str(raw.get("category") or ""),
        str(raw.get("categoryCode") or ""),
        str(raw.get("category_code") or ""),
        str(raw.get("productType") or ""),
        str(raw.get("iconUrl") or ""),
    ]
    product_info = raw.get("productInfo")
    if isinstance(product_info, dict):
        values.extend(
            str(product_info.get(key) or "")
            for key in ("category", "categoryCode", "category_code", "name")
        )
    return _ascii_fold(" ".join(values)).lower()


def _dp_text(device: TuyaDeviceDescription, dp_id: str) -> str:
    return _ascii_fold(
        " ".join(
            (
                str(dp_id),
                device.dp_names.get(str(dp_id), ""),
                str(device.dps.get(str(dp_id), "")),
            )
        )
    ).lower()


def _has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _binary_sensor_kind(device: TuyaDeviceDescription, dp_id: str) -> str | None:
    device_text = _device_text(device)
    dp_text = _dp_text(device, str(dp_id))
    if "doorcontact_state" in dp_text:
        return "door"
    if "presence_state" in dp_text:
        return "occupancy"
    if _has_any_marker(dp_text, CONTACT_DP_MARKERS):
        return "door"
    if _has_any_marker(dp_text, PRESENCE_DP_MARKERS):
        return "occupancy"
    if _has_any_marker(dp_text, MOTION_DP_MARKERS):
        return "motion"
    if _has_any_marker(dp_text, BINARY_AUX_DP_MARKERS):
        return None
    if str(dp_id) not in BINARY_PRIMARY_DP_IDS:
        return None
    if _has_any_marker(device_text, BINARY_CONTACT_CATEGORY_MARKERS):
        return "door"
    if _has_any_marker(device_text, BINARY_PRESENCE_CATEGORY_MARKERS):
        return "occupancy"
    if _has_any_marker(device_text, BINARY_MOTION_CATEGORY_MARKERS):
        return "motion"
    return None


def _normalize_binary_sensor_value(value: Any, kind: str) -> bool | None:
    if isinstance(value, bool):
        if kind == "door":
            # Tuya mcs doorcontact_state reports True when closed.
            return not value
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _ascii_fold(str(value)).strip().lower()
    if kind == "door":
        if text in {"true", "closed", "close"}:
            return False
        if text in {"false", "open", "opened"}:
            return True
    if text in BINARY_ON_VALUES:
        return True
    if text in BINARY_OFF_VALUES:
        return False
    return None


def _binary_sensor_label(
    device: TuyaDeviceDescription,
    dp_id: str,
    kind: str,
) -> str:
    name = device.dp_names.get(str(dp_id), "").strip()
    if name:
        return name
    if kind == "door":
        return "Door"
    if kind == "motion":
        return "Motion"
    if kind == "occupancy":
        return "Occupancy"
    return f"Sensor {dp_id}"


def _is_context_button_device(device: TuyaDeviceDescription) -> bool:
    text = _device_text(device)
    if _has_any_marker(text, CONTEXT_BUTTON_CATEGORY_MARKERS):
        return True
    for dp_id, value in device.dps.items():
        if _context_button_press_type(value):
            return True
        dp_text = _dp_text(device, str(dp_id))
        if any(part in dp_text for part in ("single", "double", "long", "press")):
            return True
    return False


def _context_button_channels(device: TuyaDeviceDescription) -> list[str]:
    if not _is_context_button_device(device):
        return []
    channels: list[str] = []
    for dp_id, value in sorted(device.dps.items(), key=lambda item: _dp_sort_key(item[0])):
        channel = _context_button_channel(device, str(dp_id))
        if not channel:
            continue
        value_text = _ascii_fold(str(value)).strip().lower()
        if (
            _context_button_press_type(value)
            or value_text in CONTEXT_BUTTON_IDLE_VALUES
            or "scene" in _dp_text(device, str(dp_id))
            or any(part in _dp_text(device, str(dp_id)) for part in ("single", "double", "long", "press"))
        ):
            if channel not in channels:
                channels.append(channel)
    return channels


def _context_button_channel(
    device: TuyaDeviceDescription,
    dp_id: str,
) -> str | None:
    name = device.dp_names.get(str(dp_id), "")
    text = _ascii_fold(name).lower()
    for token in text.replace("_", " ").replace("-", " ").split():
        if token.isdecimal():
            return token
    return str(dp_id) if str(dp_id).isdecimal() and int(dp_id) < 100 else None


def _context_button_state(device: TuyaDeviceDescription) -> str | None:
    for dp_id, value in sorted(device.dps.items(), key=lambda item: _dp_sort_key(item[0])):
        press_type = _context_button_press_type(value)
        if not press_type:
            continue
        channel = _context_button_channel(device, str(dp_id))
        if channel:
            return f"{channel}_{press_type}"
    return None


def _context_button_press_type(value: Any) -> str | None:
    text = _ascii_fold(str(value)).strip().lower().replace("-", "_").replace(" ", "_")
    if text in CONTEXT_BUTTON_VALUES:
        return CONTEXT_BUTTON_VALUES[text]
    if "double" in text:
        return "double"
    if "long" in text or "hold" in text:
        return "long"
    if "single" in text or "press" in text or "click" in text:
        return "press"
    return None


def _dp_sort_key(dp_id: Any) -> tuple[int, str]:
    text = str(dp_id)
    return (int(text), text) if text.isdecimal() else (9999, text)


def _is_switch_button_dp(device: TuyaDeviceDescription, dp_id: str) -> bool:
    name = device.dp_names.get(str(dp_id), "").strip().lower()
    if name and any(part in name for part in NON_BUTTON_NAME_PARTS):
        return False
    if name:
        return True
    return str(dp_id) in SWITCH_BUTTON_DP_IDS


def _switch_button_label(device: TuyaDeviceDescription, dp_id: str) -> str:
    name = device.dp_names.get(str(dp_id), "").strip()
    if name:
        return name
    return f"Button {dp_id}"


def _tinytuya_scan_devices(
    devices: Any,
) -> list[dict[str, str]]:
    scan_devices: list[dict[str, str]] = []
    for device in devices:
        if not device.local_key:
            continue
        record = {
            "id": device.dev_id,
            "name": device.name,
            "key": device.local_key,
        }
        if device.mac:
            record["mac"] = device.mac
        scan_devices.append(record)
    return scan_devices


def _candidate_force_scan_networks(devices: Any) -> list[str]:
    networks: set[str] = set()
    primary_network = _primary_lan_network()
    if primary_network:
        networks.add(primary_network)
    for device in devices:
        network = _network_for_lan_ip(device.ip)
        if network:
            networks.add(network)
    return sorted(networks)


def _primary_lan_network() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    return _network_for_lan_ip(ip)


def _network_for_lan_ip(value: str | None) -> str | None:
    ip = _lan_ip(value)
    if not ip:
        return None
    try:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False))
    except ValueError:
        return None


def _lan_ip(value: Any) -> str | None:
    if not value:
        return None
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return None
    if address.version != 4:
        return None
    if address.is_loopback or address.is_multicast or address.is_unspecified:
        return None
    if address.is_private or address.is_link_local:
        return str(address)
    return None


def _is_lan_ip(value: Any) -> bool:
    return _lan_ip(value) is not None
