from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from .const import DEFAULT_APP_RN_VERSION, DEFAULT_CH_KEY
from .models import (
    TuyaDeviceDescription,
    TuyaHome,
    TuyaIrAction,
    TuyaMobileConfig,
    TuyaSession,
    device_from_raw,
    device_parent_id,
    home_id_from_raw,
)

_LOGGER = logging.getLogger(__name__)

TOKEN_API = ("smartlife.m.user.username.token.get", "2.0")
EMAIL_LOGIN_API = ("smartlife.m.user.email.password.login", "3.0")
MOBILE_LOGIN_APIS = (
    ("smartlife.m.user.mobile.passwd.login", "4.0", "options"),
    ("smartlife.m.user.mobile.passwd.login", "4.0", "extInfo"),
    ("thing.m.user.mobile.passwd.login", "4.0", "extInfo"),
)
HOME_LIST_API = ("m.life.home.space.list", "1.0")
OWNED_DEVICE_API = ("m.life.my.group.device.list", "2.2")
DEVICE_GROUP_API = ("m.life.my.group.device.group.list", "4.3")
DEVICE_RELATION_API = ("m.life.my.group.device.relation.list", "3.2")
LOCAL_DEVICE_API = ("m.life.app.smart.local.device.list", "1.1")
ENERGY_DEVICE_API = ("m.energy.home.device.list", "3.0")
ACTION_DEVICE_API = ("thing.m.linkage.dev.list", "3.0")
ACTION_DEVICE_API_V4 = ("thing.m.linkage.dev.list", "4.0")
ACTION_FUNCTION_API = ("thing.m.linkage.function.list", "3.0")

NO_POST_DATA = object()
LOGIN_OPTIONS = '{"group": 1,"mfaCode": ""}'
IR_ACTION_EXECUTORS = {"irIssue", "irIssueVii"}
IR_TEXT_MARKERS = (
    "infrared",
    "infraredid",
    "infragwid",
    "infraredgwid",
    "infraredgatewayid",
    "irissue",
    "remoteid",
)
HUB_ID_KEYS = (
    "gwId",
    "gatewayId",
    "infraGwId",
    "infraredGwId",
    "infraredGatewayId",
    "parentDevId",
    "communicationId",
    "communicationNode",
    "meshId",
)

SIGN_KEYS = {
    "a",
    "v",
    "lat",
    "lon",
    "lang",
    "deviceId",
    "appVersion",
    "ttid",
    "h5",
    "h5Token",
    "os",
    "clientId",
    "postData",
    "time",
    "requestId",
    "et",
    "n4h5",
    "sid",
    "chKey",
    "sp",
}

FIXED_RSA_SEED = bytes(
    [
        0xAA,
        0xFD,
        0x12,
        0xF6,
        0x59,
        0xCA,
        0xE6,
        0x34,
        0x89,
        0xB4,
        0x79,
        0xE5,
        0x07,
        0x6D,
        0xDE,
        0xC2,
        0xF0,
        0x6C,
        0xB5,
        0x8F,
    ]
)


class TuyaMobileApiError(Exception):
    """Raised when the mobile API returns an error."""


def md5_hex(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.md5(value).hexdigest()


def swap_sign_string(value: str) -> str:
    return value[8:16] + value[0:8] + value[24:32] + value[16:24]


def post_data_md5_hex(post_data: str | None) -> str:
    return swap_sign_string(md5_hex(post_data)) if post_data else ""


def build_sign_input(params: dict[str, Any]) -> str:
    normalized = dict(params)
    if normalized.get("postData"):
        normalized["postData"] = post_data_md5_hex(normalized["postData"])
    parts = []
    for key in sorted(normalized):
        value = normalized.get(key)
        if key in SIGN_KEYS and value not in (None, ""):
            parts.append(f"{key}={value}")
    return "||".join(parts)


def request_sign(sign_input: str, native_key: bytes) -> str:
    return hmac.new(native_key, sign_input.encode(), hashlib.sha256).hexdigest()


def normalize_cert_sha256(value: str) -> str:
    stripped = value.replace(":", "").replace(" ", "").lower()
    if len(stripped) != 64 or any(ch not in "0123456789abcdef" for ch in stripped):
        raise ValueError("certificate SHA-256 must contain 64 hex characters")
    return ":".join(stripped[i : i + 2].upper() for i in range(0, len(stripped), 2))


def derive_native_signing_key(
    package_name: str,
    cert_sha256: str,
    bmp_key: str,
    app_secret: str,
) -> str:
    cert = normalize_cert_sha256(cert_sha256)
    return f"{package_name}_{cert}_{bmp_key}_{app_secret}"


def rsa_pkcs1_v15_encrypt_hex(
    message: str,
    modulus_dec: str,
    exponent_dec: str,
) -> str:
    modulus = int(modulus_dec)
    exponent = int(exponent_dec)
    key_len = (modulus.bit_length() + 7) // 8
    message_bytes = message.encode()
    padding_len = key_len - len(message_bytes) - 3
    if padding_len < 8:
        raise ValueError("message too long for RSA key")

    padding = (FIXED_RSA_SEED * ((padding_len // len(FIXED_RSA_SEED)) + 1))[
        :padding_len
    ]
    encoded = b"\x00\x02" + padding + b"\x00" + message_bytes
    cipher_int = pow(int.from_bytes(encoded, "big"), exponent, modulus)
    return cipher_int.to_bytes(key_len, "big").hex()


def stable_device_id(username: str, app_id: str, package_name: str) -> str:
    material = f"{package_name}|{app_id}|{username}".encode()
    return hashlib.sha256(material).hexdigest()[:44]


def is_email_username(username: str) -> bool:
    return "@" in username.strip()


def normalize_mobile_username(username: str, country_code: str) -> str:
    value = username.strip()
    for char in (" ", "-", "(", ")"):
        value = value.replace(char, "")
    code = str(country_code).strip().lstrip("+")
    if value.startswith("+"):
        digits = value[1:]
        if code and digits.startswith(code) and len(digits) > len(code):
            return digits[len(code) :]
        return digits
    if code and value.startswith(f"00{code}") and len(value) > len(code) + 2:
        return value[len(code) + 2 :]
    return value


def mobile_username_candidates(username: str, country_code: str) -> list[str]:
    mobile = normalize_mobile_username(username, country_code)
    candidates = [mobile]
    if mobile.startswith("0") and len(mobile) > 1:
        candidates.append(mobile[1:])
    return list(dict.fromkeys(candidates))


def should_try_next_mobile_login_api(response: dict[str, Any]) -> bool:
    code = str(response.get("errorCode") or response.get("code") or "")
    msg = str(
        response.get("errorMsg") or response.get("msg") or response.get("status") or ""
    )
    text = f"{code}:{msg}".upper()
    auth_markers = (
        "CAPTCHA",
        "LOCK",
        "MFA",
        "PASSWD",
        "PASSWORD",
        "VERIFY",
    )
    return not any(marker in text for marker in auth_markers)


class TuyaSmartLifeMobileApi:
    """Tuya Smart Life mobile API client using the reversed native signature."""

    def __init__(self, config: TuyaMobileConfig) -> None:
        self.config = config
        self.device_id = config.device_id or stable_device_id(
            config.email, config.app_id, config.package_name
        )
        self.native_key = self._native_key()

    def _native_key(self) -> bytes:
        if self.config.native_key_text:
            return self.config.native_key_text.encode()
        if not (self.config.app_secret and self.config.cert_sha256 and self.config.bmp_key):
            raise TuyaMobileApiError(
                "Provide native_key_text, or app_secret + cert_sha256 + bmp_key"
            )
        return derive_native_signing_key(
            self.config.package_name,
            self.config.cert_sha256,
            self.config.bmp_key,
            self.config.app_secret,
        ).encode()

    def request(
        self,
        api: str,
        version: str,
        payload: dict[str, Any] | object = NO_POST_DATA,
        sid: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        params: dict[str, Any] = {
            "a": api,
            "v": version,
            "clientId": self.config.app_id,
            "deviceId": self.device_id,
            "appVersion": self.config.app_version,
            "chKey": DEFAULT_CH_KEY,
            "ttid": "international",
            "lang": "vi_VN",
            "os": "Android",
            "et": "0",
            "time": str(int(time.time())),
            "requestId": str(uuid.uuid4()),
            "sdkVersion": self.config.sdk_version,
            "deviceCoreVersion": self.config.device_core_version,
            "osSystem": self.config.os_system,
            "platform": "y",
            "channel": "oem",
            "appRnVersion": DEFAULT_APP_RN_VERSION,
            "bizData": "",
            "cp": "",
            "nd": "",
            "timeZoneId": "Asia/Ho_Chi_Minh",
        }
        if sid:
            params["sid"] = sid
        if payload is not NO_POST_DATA:
            params["postData"] = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            )
        if extra:
            params.update(extra)

        params["sign"] = request_sign(build_sign_input(params), self.native_key)
        request = urllib.request.Request(
            self.config.endpoint,
            data=urllib.parse.urlencode(params).encode(),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": f"ThingSmart/{self.config.app_version} Android",
                "Accept-Encoding": "identity",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read().decode(errors="replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError as err:
                raise TuyaMobileApiError(body) from err

    def login(self) -> TuyaSession:
        username = self.config.email.strip()
        _, token_response = self.request(
            *TOKEN_API,
            {
                "countryCode": self.config.country_code,
                "username": username,
                "isUid": False,
            },
        )
        self._raise_for_response(token_response, "login token")
        token = token_response["result"]

        password_md5 = md5_hex(self.config.password)
        encrypted_password = rsa_pkcs1_v15_encrypt_hex(
            password_md5,
            token["publicKey"],
            token["exponent"],
        )
        if is_email_username(username):
            _, login_response = self.request(
                *EMAIL_LOGIN_API,
                {
                    "countryCode": self.config.country_code,
                    "email": username,
                    "passwd": encrypted_password,
                    "options": LOGIN_OPTIONS,
                    "token": token["token"],
                    "ifencrypt": 1,
                },
            )
            self._raise_for_response(login_response, "email password login")
        else:
            login_response = self._mobile_password_login(
                username,
                encrypted_password,
                str(token["token"]),
            )
        return self._session_from_login_response(login_response)

    def _mobile_password_login(
        self,
        username: str,
        encrypted_password: str,
        token: str,
    ) -> dict[str, Any]:
        last_context = "mobile password login"
        last_response: dict[str, Any] | None = None
        for mobile in mobile_username_candidates(username, self.config.country_code):
            for api, version, mfa_field in MOBILE_LOGIN_APIS:
                payload = {
                    "countryCode": self.config.country_code,
                    "mobile": mobile,
                    "passwd": encrypted_password,
                    mfa_field: LOGIN_OPTIONS,
                    "token": token,
                    "ifencrypt": 1,
                }
                _, response = self.request(api, version, payload)
                if response.get("success"):
                    return response
                last_context = f"mobile password login {api}"
                last_response = response
                if not should_try_next_mobile_login_api(response):
                    break
            if last_response and not should_try_next_mobile_login_api(last_response):
                break

        self._raise_for_response(last_response or {}, last_context)
        raise TuyaMobileApiError(f"{last_context} failed")

    @staticmethod
    def _session_from_login_response(login_response: dict[str, Any]) -> TuyaSession:
        result = login_response["result"]
        domain = result.get("domain") if isinstance(result.get("domain"), dict) else {}
        return TuyaSession(
            sid=result["sid"],
            ecode=result.get("ecode"),
            uid=result.get("uid"),
            region=domain.get("regionCode"),
            raw=result,
        )

    def list_homes(self, session: TuyaSession) -> list[TuyaHome]:
        _, response = self.request(*HOME_LIST_API, sid=session.sid)
        self._raise_for_response(response, "home list")
        homes = response.get("result") or []
        if not isinstance(homes, list):
            return []
        return [
            TuyaHome(id=home_id_from_raw(home), name=str(home.get("name")), raw=home)
            for home in homes
            if isinstance(home, dict)
        ]

    def list_home_devices(
        self,
        session: TuyaSession,
        home: TuyaHome,
    ) -> list[TuyaDeviceDescription]:
        _, response = self.request(*OWNED_DEVICE_API, {"gid": home.id}, sid=session.sid)
        self._raise_for_response(response, f"device list for {home.name}")
        raw_devices = response.get("result") or []
        if not isinstance(raw_devices, list):
            return []

        parent_ids = {
            parent_id
            for device in raw_devices
            if isinstance(device, dict)
            for parent_id in [device_parent_id(device)]
            if parent_id
        }
        return [
            device_from_raw(device, home, parent_ids)
            for device in raw_devices
            if isinstance(device, dict) and device.get("devId")
        ]

    def list_action_device_ids(
        self,
        session: TuyaSession,
        home: TuyaHome,
    ) -> dict[str, Any]:
        last_response: dict[str, Any] | None = None
        for api, version in (ACTION_DEVICE_API, ACTION_DEVICE_API_V4):
            _, response = self.request(
                api,
                version,
                {"gid": home.id, "sourceType": "action"},
                sid=session.sid,
            )
            if response.get("success"):
                result = response.get("result")
                return result if isinstance(result, dict) else {}
            last_response = response
            _LOGGER.debug(
                "Tuya action device list %s v%s for %s failed: %s",
                api,
                version,
                home.id,
                response,
            )
        self._raise_for_response(
            last_response or {},
            f"action device list for {home.name}",
        )
        return {}

    def list_action_functions(
        self,
        session: TuyaSession,
        home: TuyaHome,
        dev_id: str,
    ) -> list[dict[str, Any]]:
        payloads = (
            {"params": {"gid": home.id, "devId": dev_id}},
            {"params": {"gid": str(home.id), "devId": dev_id}},
            {"devId": dev_id},
        )
        last_response: dict[str, Any] | None = None
        for payload in payloads:
            _, response = self.request(
                *ACTION_FUNCTION_API,
                payload,
                sid=session.sid,
            )
            if response.get("success"):
                result = response.get("result")
                return result if isinstance(result, list) else []
            last_response = response
            _LOGGER.debug(
                "Tuya action function list for %s payload=%s failed: %s",
                dev_id,
                payload,
                response,
            )
        self._raise_for_response(
            last_response or {},
            f"action function list for {dev_id}",
        )
        return []

    def list_home_ir_actions(
        self,
        session: TuyaSession,
        home: TuyaHome,
        devices: list[TuyaDeviceDescription],
    ) -> list[TuyaIrAction]:
        try:
            action_devices = self.list_action_device_ids(session, home)
        except TuyaMobileApiError as err:
            _LOGGER.warning("Unable to fetch Tuya IR action devices for %s: %s", home.id, err)
            return []

        device_names = action_devices.get("deviceIds")
        exts = action_devices.get("exts")
        if not isinstance(device_names, dict):
            device_names = {}
        if not isinstance(exts, dict):
            exts = {}

        devices_by_id = {device.dev_id: device for device in devices}
        candidate_ids = {str(dev_id) for dev_id in device_names}
        candidate_ids.update(str(dev_id) for dev_id in exts)
        for device in devices:
            if _looks_like_ir_device(device.raw):
                candidate_ids.add(device.dev_id)

        hub_ids = {device.dev_id for device in devices if device.is_hub}
        actions: list[TuyaIrAction] = []
        for remote_id in sorted(candidate_ids):
            remote = devices_by_id.get(remote_id)
            ext = exts.get(remote_id)
            ext = ext if isinstance(ext, dict) else {}
            hub_dev_id = _infer_ir_hub_id(remote, ext, hub_ids)
            if not hub_dev_id:
                continue
            try:
                functions = self.list_action_functions(session, home, remote_id)
            except TuyaMobileApiError as err:
                _LOGGER.debug(
                    "Unable to fetch Tuya IR functions for %s in %s: %s",
                    remote_id,
                    home.id,
                    err,
                )
                continue
            remote_name = str(
                (remote.name if remote else None)
                or device_names.get(remote_id)
                or ext.get("name")
                or remote_id
            )
            category = _remote_category(remote, ext, functions)
            product_id = (
                (remote.product_id if remote else None)
                or _first_text(ext, ("productId", "product_id", "pid"))
            )
            force_ir = (
                remote is None
                or _looks_like_ir_device(ext)
                or _looks_like_ir_device(remote.raw)
            )
            actions.extend(
                _ir_actions_from_functions(
                    home,
                    remote_id,
                    remote_name,
                    hub_dev_id,
                    remote,
                    functions,
                    force_ir,
                    category,
                    product_id,
                    ext,
                )
            )
        return actions

    def fetch_devices(
        self,
        selected_home_ids: set[str],
    ) -> tuple[
        list[TuyaHome],
        list[TuyaDeviceDescription],
        list[TuyaIrAction],
        TuyaSession,
    ]:
        session = self.login()
        homes = self.list_homes(session)
        devices: list[TuyaDeviceDescription] = []
        ir_actions: list[TuyaIrAction] = []
        for home in homes:
            if home.id in selected_home_ids:
                home_devices = self.list_home_devices(session, home)
                devices.extend(home_devices)
                ir_actions.extend(
                    self.list_home_ir_actions(session, home, home_devices)
                )
        return homes, devices, ir_actions, session

    @staticmethod
    def _raise_for_response(response: dict[str, Any], context: str) -> None:
        if response.get("success"):
            return
        code = response.get("errorCode") or response.get("code") or "unknown_error"
        msg = response.get("errorMsg") or response.get("msg") or response.get("status")
        raise TuyaMobileApiError(f"{context} failed: {code}: {msg}")


def _ir_actions_from_functions(
    home: TuyaHome,
    remote_id: str,
    remote_name: str,
    hub_dev_id: str,
    remote: TuyaDeviceDescription | None,
    functions: list[dict[str, Any]],
    force_ir: bool = False,
    category: str | None = None,
    product_id: str | None = None,
    ext: dict[str, Any] | None = None,
) -> list[TuyaIrAction]:
    actions: list[TuyaIrAction] = []
    seen: set[tuple[str, str]] = set()
    for function in functions:
        if not isinstance(function, dict):
            continue
        if not force_ir and not _looks_like_ir_action_function(function):
            continue
        function_name = str(
            function.get("functionName")
            or function.get("name")
            or function.get("functionCode")
            or "IR"
        )
        function_id = str(
            function.get("id")
            or function.get("functionCode")
            or _slug(function_name)
        )
        for detail in _action_details(function):
            for action_dps, report_dps, suffix, label in _action_payloads(
                function,
                detail,
            ):
                key = (suffix, json.dumps(action_dps, sort_keys=True, default=str))
                if key in seen:
                    continue
                seen.add(key)
                action_name = " ".join(part for part in (function_name, label) if part)
                if not action_name:
                    action_name = "IR Action"
                action_id = _slug(f"{function_id}_{suffix or action_name}")
                actions.append(
                    TuyaIrAction(
                        remote_id=remote_id,
                        remote_name=remote_name,
                        home_id=home.id,
                        home_name=home.name,
                        hub_dev_id=hub_dev_id,
                        action_id=action_id,
                        action_name=action_name,
                        action_dps={str(key): value for key, value in action_dps.items()},
                        report_dps={
                            str(key): value for key, value in report_dps.items()
                        },
                        product_id=product_id or (remote.product_id if remote else None),
                        category=category,
                        raw={
                            "function": function,
                            "detail": detail,
                            "remote": remote.raw if remote else {},
                            "ext": ext or {},
                        },
                    )
                )
    return actions


def _action_details(function: dict[str, Any]) -> list[dict[str, Any]]:
    data_points = function.get("dataPoints")
    if isinstance(data_points, list):
        return [item for item in data_points if isinstance(item, dict)]
    return [function]


def _action_payloads(
    function: dict[str, Any],
    detail: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any], str, str]]:
    payloads: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []
    for source_key in (
        "tasks",
        "taskMap",
        "taskList",
        "valueRangeJson",
        "values",
        "executorProperty",
    ):
        source = _json_value(detail.get(source_key))
        payloads.extend(_payloads_from_source(function, detail, source_key, source))

    direct = _extract_action_maps(function)
    if direct:
        action_dps, report_dps, label = direct
        payloads.append((action_dps, report_dps, "function", label))
    return [
        payload
        for payload in payloads
        if payload[0] and _looks_like_dps_map(payload[0])
    ]


def _payloads_from_source(
    function: dict[str, Any],
    detail: dict[str, Any],
    source_key: str,
    source: Any,
) -> list[tuple[dict[str, Any], dict[str, Any], str, str]]:
    dp_id = str(
        detail.get("dpId")
        or detail.get("id")
        or function.get("id")
        or function.get("functionCode")
        or ""
    )
    dp_name = str(detail.get("dpName") or detail.get("name") or "").strip()

    direct = _extract_action_maps(source)
    if direct:
        action_dps, report_dps, label = direct
        return [(action_dps, report_dps, source_key, label or dp_name)]

    payloads: list[tuple[dict[str, Any], dict[str, Any], str, str]] = []
    if isinstance(source, dict):
        for key, value in source.items():
            parsed = _json_value(value)
            direct = _extract_action_maps(parsed)
            if direct:
                action_dps, report_dps, label = direct
                payloads.append(
                    (
                        action_dps,
                        report_dps,
                        f"{source_key}_{key}",
                        label or _label_from_task(key, value),
                    )
                )
                continue
            if dp_id and _is_scalar(parsed):
                payloads.append(
                    (
                        {dp_id: parsed},
                        {dp_id: parsed},
                        f"{source_key}_{key}",
                        _label_from_task(key, value),
                    )
                )
        return payloads

    if isinstance(source, list):
        for index, item in enumerate(source):
            parsed = _json_value(item)
            direct = _extract_action_maps(parsed)
            if direct:
                action_dps, report_dps, label = direct
                payloads.append(
                    (
                        action_dps,
                        report_dps,
                        f"{source_key}_{index}",
                        label or _label_from_task(index, item),
                    )
                )
            elif isinstance(parsed, dict):
                value = (
                    parsed.get("value")
                    or parsed.get("code")
                    or parsed.get("key")
                    or parsed.get("dpValue")
                )
                if dp_id and _is_scalar(value):
                    label = str(
                        parsed.get("label")
                        or parsed.get("name")
                        or parsed.get("display")
                        or value
                    )
                    payloads.append(
                        (
                            {dp_id: value},
                            {dp_id: value},
                            f"{source_key}_{index}_{value}",
                            label,
                        )
                    )
    return payloads


def _extract_action_maps(value: Any) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    value = _json_value(value)
    if not isinstance(value, dict):
        return None

    action_source = (
        value.get("actionDps")
        or value.get("executorProperty")
        or value.get("executor_property")
        or value.get("dps")
    )
    report_source = (
        value.get("reportDps")
        or value.get("extraProperty")
        or value.get("extra_property")
        or value.get("report")
    )
    action_dps = _json_object(action_source)
    report_dps = _json_object(report_source)
    if action_dps:
        return action_dps, report_dps or {}, _display_label(value)

    if _looks_like_dps_map(value):
        return value, {}, _display_label(value)
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        if text[0] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return value
    return value


def _json_object(value: Any) -> dict[str, Any]:
    value = _json_value(value)
    return value if isinstance(value, dict) else {}


def _looks_like_dps_map(value: dict[str, Any]) -> bool:
    if not value:
        return False
    ignored = {
        "name",
        "label",
        "display",
        "actionDisplay",
        "actionDisplayNew",
        "executorProperty",
        "extraProperty",
        "actionDps",
        "reportDps",
    }
    useful_keys = [key for key in value if str(key) not in ignored]
    if not useful_keys:
        return False
    if any(str(key).isdigit() for key in useful_keys):
        return True
    return len(useful_keys) <= 4 and all(_is_scalar(value[key]) for key in useful_keys)


def _looks_like_ir_action_function(function: dict[str, Any]) -> bool:
    executor = str(
        function.get("actionExecutor")
        or function.get("executor")
        or function.get("functionType")
        or ""
    )
    if executor in IR_ACTION_EXECUTORS:
        return True
    text = json.dumps(function, ensure_ascii=False, default=str).lower()
    return any(marker.lower() in text for marker in IR_TEXT_MARKERS)


def _looks_like_ir_device(raw: dict[str, Any]) -> bool:
    if not raw:
        return False
    text = json.dumps(raw, ensure_ascii=False, default=str).lower()
    return any(marker.lower() in text for marker in IR_TEXT_MARKERS)


def _infer_ir_hub_id(
    remote: TuyaDeviceDescription | None,
    ext: dict[str, Any],
    hub_ids: set[str],
) -> str | None:
    if remote and remote.parent_dev_id:
        return remote.parent_dev_id

    candidates: list[str] = []
    for key in HUB_ID_KEYS:
        value = ext.get(key)
        if value:
            candidates.append(str(value))
    if remote:
        for key in HUB_ID_KEYS:
            value = _nested_value(remote.raw, key)
            if value:
                candidates.append(str(value))

    for candidate in candidates:
        if candidate in hub_ids:
            return candidate
    if candidates:
        return candidates[0]
    if len(hub_ids) == 1:
        return next(iter(hub_ids))
    return None


def _remote_category(
    remote: TuyaDeviceDescription | None,
    ext: dict[str, Any],
    functions: list[dict[str, Any]],
) -> str | None:
    if remote:
        value = _first_text(
            remote.raw,
            (
                "category",
                "categoryId",
                "category_id",
                "remoteType",
                "remote_type",
                "productCategory",
            ),
        )
        if value:
            return value
    value = _first_text(
        ext,
        (
            "category",
            "categoryId",
            "category_id",
            "remoteType",
            "remote_type",
            "productCategory",
        ),
    )
    if value:
        return value
    for function in functions:
        value = _first_text(
            function,
            (
                "category",
                "categoryId",
                "category_id",
                "remoteType",
                "remote_type",
                "productCategory",
            ),
        )
        if value:
            return value
    return None


def _first_text(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        found = _nested_value(value, key)
        if found is not None and str(found).strip():
            return str(found).strip()
    return None


def _nested_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _nested_value(child, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _nested_value(child, key)
            if found is not None:
                return found
    return None


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _label_from_task(key: Any, value: Any) -> str:
    if isinstance(value, dict):
        return _display_label(value) or str(key)
    parsed = _json_value(value)
    if isinstance(parsed, dict):
        return _display_label(parsed) or str(key)
    return str(key)


def _display_label(value: dict[str, Any]) -> str:
    for key in (
        "label",
        "name",
        "display",
        "actionDisplayNew",
        "actionDisplay",
        "dpName",
    ):
        label = value.get(key)
        if label:
            return str(label)
    return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return slug[:80] or "action"
