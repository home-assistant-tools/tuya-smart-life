from __future__ import annotations

import asyncio
from dataclasses import fields
import ipaddress
import json
import logging
import socket
import time
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant

from .models import TuyaDeviceDescription

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PORTS = (6666, 6667, 6699, 7000)
DISCOVERY_SCAN_SECONDS = 8
FORCE_SCAN_INTERVAL_SECONDS = 300
SWITCH_BUTTON_DP_IDS = {str(dp_id) for dp_id in range(1, 9)}
FAN_PRODUCT_IDS = {"tqfl5ws2csdtdaak"}
FAN_POWER_DP_ID = "1"
FAN_SPEED_DP_ID = "3"
CHILD_PROTOCOL_FALLBACKS = ("3.4", "3.5", "3.3")
NO_FALLBACK_RESPONSE = object()
NON_BUTTON_NAME_PARTS = (
    "backlight",
    "child lock",
    "countdown",
    "do not disturb",
    "indicator",
    "led",
    "relay status",
)


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
        self.devices: dict[str, TuyaDeviceDescription] = {}
        self.transports: list[asyncio.DatagramTransport] = []
        self._tinytuya_devices: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._scan_task: asyncio.Task[None] | None = None
        self._last_force_scan = 0.0

    async def async_start(self) -> None:
        if self.transports:
            return
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
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self._tinytuya_devices.clear()

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
        gw_id = payload.get("gwId") or payload.get("devId") or payload.get("id")
        if not gw_id:
            return
        device = self.devices.get(str(gw_id))
        if not device:
            return

        ip = _lan_ip(payload.get("ip") or fallback_ip)
        version = payload.get("version") or payload.get("ver")
        changed = False
        if ip and ip != device.ip:
            device.ip = str(ip)
            changed = True
        if version and str(version) != device.protocol_version:
            device.protocol_version = str(version)
            changed = True
        if changed:
            self._tinytuya_devices.pop(device.dev_id, None)
            _LOGGER.debug(
                "Tuya broadcast updated %s ip=%s version=%s",
                device.dev_id,
                device.ip,
                device.protocol_version,
            )

    def switch_button_dps(self) -> list[tuple[TuyaDeviceDescription, str, bool, str]]:
        items: list[tuple[TuyaDeviceDescription, str, bool, str]] = []
        for device in self.devices.values():
            if not device.local_controllable or device.is_hub:
                continue
            for dp_id, value in device.dps.items():
                if _is_fan_device(device) and str(dp_id) == FAN_POWER_DP_ID:
                    continue
                if isinstance(value, bool) and _is_switch_button_dp(device, dp_id):
                    items.append(
                        (device, dp_id, value, _switch_button_label(device, dp_id))
                    )
        return items

    def fan_devices(self) -> list[TuyaDeviceDescription]:
        return [
            device
            for device in self.devices.values()
            if device.local_controllable and not device.is_hub and _is_fan_device(device)
        ]

    def hub_devices(self) -> list[TuyaDeviceDescription]:
        return [device for device in self.devices.values() if device.is_hub]

    def boolean_dps(self) -> list[tuple[TuyaDeviceDescription, str, bool]]:
        return [
            (device, dp_id, value)
            for device, dp_id, value, _ in self.switch_button_dps()
        ]

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
        response = self._set_dp_once(dev_id, dp_id, value)
        if _is_key_or_version_error(response):
            fallback = self._try_child_protocol_fallback(dev_id, dp_id, value)
            if fallback is not NO_FALLBACK_RESPONSE:
                return fallback
        return response

    def _set_dp_once(self, dev_id: str, dp_id: int, value: Any) -> Any:
        device = self._tinytuya_device(dev_id)
        if not device:
            raise RuntimeError(f"Device {dev_id} is missing local metadata or IP")
        if hasattr(device, "set_value"):
            return device.set_value(dp_id, value)
        return device.set_status(value, switch=dp_id)

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
        for version in _child_protocol_candidates(parent.protocol_version):
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

    @staticmethod
    def _make_tinytuya_device(
        tinytuya: Any,
        device: TuyaDeviceDescription,
        ip: str,
        local_key: str,
        parent: Any | None,
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
            tuya_device.set_socketPersistent(False)
        if hasattr(tuya_device, "set_socketNODELAY"):
            tuya_device.set_socketNODELAY(True)
        return tuya_device


def _protocol_version(value: str | None) -> float:
    if not value:
        return 3.3
    parts = str(value).strip().split(".")
    try:
        return float(".".join(parts[:2]))
    except ValueError:
        _LOGGER.debug("Unknown Tuya protocol version %s, falling back to 3.3", value)
        return 3.3


def _child_protocol_candidates(current: str | None) -> list[str]:
    current_text = str(current).strip() if current else ""
    versions = [version for version in CHILD_PROTOCOL_FALLBACKS if version != current_text]
    if current_text and current_text not in versions:
        versions.append(current_text)
    return versions


def _is_key_or_version_error(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    text = " ".join(
        str(response.get(key) or "")
        for key in ("Error", "Err", "error", "message", "Payload")
    ).lower()
    return "key or version" in text


def _is_fan_device(device: TuyaDeviceDescription) -> bool:
    product_id = (device.product_id or "").strip().lower()
    if product_id in FAN_PRODUCT_IDS:
        return True
    name = device.name.strip().lower()
    if ("fan" in name or "quạt" in name) and isinstance(
        device.dps.get(FAN_POWER_DP_ID), bool
    ):
        return True
    return False


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
