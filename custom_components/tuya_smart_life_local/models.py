from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
from typing import Any


@dataclass(slots=True)
class TuyaHome:
    id: str
    name: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TuyaMobileConfig:
    email: str
    password: str
    country_code: str
    api_region: str
    app_id: str
    app_secret: str | None
    cert_sha256: str | None
    bmp_key: str | None
    native_key_text: str | None
    package_name: str
    app_version: str
    sdk_version: str
    device_core_version: str
    os_system: str
    device_id: str | None = None
    endpoint: str | None = None


@dataclass(slots=True)
class TuyaSession:
    sid: str
    ecode: str | None
    uid: str | None
    region: str | None
    endpoint: str | None
    raw: dict[str, Any]


@dataclass(slots=True)
class TuyaDeviceDescription:
    dev_id: str
    name: str
    home_id: str
    home_name: str
    local_key: str | None
    ip: str | None
    mac: str | None
    uuid: str | None
    product_id: str | None
    kind: str
    parent_dev_id: str | None
    node_id: str | None
    online: bool | None
    protocol_version: str | None
    dps: dict[str, Any] = field(default_factory=dict)
    dp_names: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_child(self) -> bool:
        return self.kind == "child" and bool(self.parent_dev_id)

    @property
    def is_hub(self) -> bool:
        return self.kind == "hub"

    @property
    def local_controllable(self) -> bool:
        if self.is_child:
            return bool(self.parent_dev_id and self.node_id)
        return bool(self.local_key)


@dataclass(slots=True)
class TuyaIrAction:
    remote_id: str
    remote_name: str
    home_id: str
    home_name: str
    hub_dev_id: str
    action_id: str
    action_name: str
    action_dps: dict[str, Any]
    report_dps: dict[str, Any] = field(default_factory=dict)
    product_id: str | None = None
    category: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        return f"{self.remote_id}_ir_{self.action_id}"


@dataclass(slots=True)
class TuyaIrClimate:
    remote_id: str
    remote_name: str
    home_id: str
    home_name: str
    hub_dev_id: str
    actions: list[TuyaIrAction]
    product_id: str | None = None
    category: str | None = None

    @property
    def unique_id(self) -> str:
        return f"{self.remote_id}_ir_climate"


def home_id_from_raw(raw: dict[str, Any]) -> str:
    return str(raw.get("homeId") or raw.get("gid") or raw.get("id"))


def device_home_id_from_raw(raw: dict[str, Any]) -> str | None:
    for key in ("homeId", "gid", "groupId", "home_id", "group_id"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)

    home = raw.get("home")
    if isinstance(home, dict):
        value = home.get("homeId") or home.get("gid") or home.get("id")
        if value not in (None, ""):
            return str(value)

    group = raw.get("group")
    if isinstance(group, dict):
        value = group.get("groupId") or group.get("gid") or group.get("id")
        if value not in (None, ""):
            return str(value)

    return None


def device_parent_id(raw: dict[str, Any]) -> str | None:
    topo = raw.get("deviceTopo")
    if isinstance(topo, dict):
        parent = topo.get("parentDevId") or topo.get("meshId") or topo.get("gatewayId")
        if parent:
            return str(parent)

    communication = raw.get("communication")
    if isinstance(communication, dict):
        node = communication.get("communicationNode")
        if node and node != raw.get("devId"):
            return str(node)
    return None


def device_node_id(raw: dict[str, Any]) -> str | None:
    topo = raw.get("deviceTopo")
    if isinstance(topo, dict):
        node = topo.get("nodeId") or topo.get("cid")
        if node:
            return str(node)
    return raw.get("uuid") or raw.get("mac")


def communication_mode_types(raw: dict[str, Any]) -> list[int]:
    communication = raw.get("communication")
    if not isinstance(communication, dict):
        return []
    modes = communication.get("communicationModes")
    if not isinstance(modes, list):
        return []
    return [
        int(mode["type"])
        for mode in modes
        if isinstance(mode, dict) and mode.get("type") is not None
    ]


def dps_from_raw(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    info = raw.get("dataPointInfo")
    if not isinstance(info, dict):
        return {}, {}

    dps = info.get("dps")
    names = info.get("dpName")
    if not isinstance(dps, dict):
        dps = {}
    if not isinstance(names, dict):
        names = {}
    return {str(key): value for key, value in dps.items()}, {
        str(key): str(value) for key, value in names.items()
    }


def local_ip_from_raw(raw: dict[str, Any]) -> str | None:
    ip = raw.get("ip")
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(str(ip))
    except ValueError:
        return None
    if address.version == 4 and (address.is_private or address.is_link_local):
        return str(address)
    return None


def device_from_raw(
    raw: dict[str, Any],
    home: TuyaHome,
    hub_ids: set[str],
) -> TuyaDeviceDescription:
    dev_id = str(raw["devId"])
    parent_id = device_parent_id(raw)
    meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    mode_types = communication_mode_types(raw)
    if dev_id in hub_ids or meta.get("zigBleSubEnable") is True or 8 in mode_types:
        kind = "hub"
    elif parent_id:
        kind = "child"
    else:
        kind = "device"

    dps, dp_names = dps_from_raw(raw)
    return TuyaDeviceDescription(
        dev_id=dev_id,
        name=str(raw.get("name") or dev_id),
        home_id=home.id,
        home_name=home.name,
        local_key=raw.get("localKey") or None,
        ip=local_ip_from_raw(raw),
        mac=raw.get("mac") or None,
        uuid=raw.get("uuid") or None,
        product_id=raw.get("productId") or None,
        kind=kind,
        parent_dev_id=parent_id,
        node_id=device_node_id(raw),
        online=raw.get("cloudOnline"),
        protocol_version=None,
        dps=dps,
        dp_names=dp_names,
        raw=raw,
    )
