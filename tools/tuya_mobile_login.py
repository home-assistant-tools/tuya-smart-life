#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from tuya_mobile_crypto import (
    build_sign_input,
    derive_native_signing_key,
    extract_bmp_keys,
    request_sign,
)


DEFAULT_ENDPOINT = "https://a1.tuyaus.com/api.json"
DEFAULT_TOKEN_API = "smartlife.m.user.username.token.get"
DEFAULT_EMAIL_LOGIN_API = "smartlife.m.user.email.password.login"
DEFAULT_MOBILE_LOGIN_APIS = (
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

SENSITIVE_MARKERS = (
    "authorization",
    "cookie",
    "ecode",
    "key",
    "passwd",
    "password",
    "secret",
    "sid",
    "token",
)
PII_MARKERS = ("email", "mobile", "receiver", "username")
NO_POST_DATA = object()
LOGIN_OPTIONS = '{"group": 1,"mfaCode": ""}'


def env_value(name):
    value = os.environ.get(name)
    return value if value not in (None, "") else None


def required(value, label):
    if value:
        return value
    raise SystemExit(f"missing {label}")


def md5_hex(value):
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def rsa_pkcs1_v15_encrypt_hex(message, modulus_dec, exponent_dec):
    modulus = int(modulus_dec)
    exponent = int(exponent_dec)
    key_len = (modulus.bit_length() + 7) // 8
    message_bytes = message.encode("utf-8")
    padding_len = key_len - len(message_bytes) - 3
    if padding_len < 8:
        raise ValueError("message too long for RSA key")

    padding = (FIXED_RSA_SEED * ((padding_len // len(FIXED_RSA_SEED)) + 1))[
        :padding_len
    ]
    encoded = b"\x00\x02" + padding + b"\x00" + message_bytes
    cipher_int = pow(int.from_bytes(encoded, "big"), exponent, modulus)
    return cipher_int.to_bytes(key_len, "big").hex()


def stable_device_id(username, app_id, package_name):
    material = f"{package_name}|{app_id}|{username}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:44]


def is_email_username(username):
    return "@" in username.strip()


def normalize_mobile_username(username, country_code):
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


def mobile_username_candidates(username, country_code):
    mobile = normalize_mobile_username(username, country_code)
    candidates = [mobile]
    if mobile.startswith("0") and len(mobile) > 1:
        candidates.append(mobile[1:])
    return list(dict.fromkeys(candidates))


def should_try_next_mobile_login_api(response):
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


def parse_mobile_login_api(value):
    parts = value.split(":")
    if len(parts) == 1:
        return parts[0], "4.0", "extInfo"
    if len(parts) == 2:
        return parts[0], parts[1], "extInfo"
    return parts[0], parts[1], parts[2]


def redact(value, key="", show_secrets=False):
    if show_secrets:
        return value
    lowered = key.lower()
    if any(marker in lowered for marker in SENSITIVE_MARKERS):
        if value in (None, ""):
            return value
        return f"<present len={len(str(value))}>"
    if any(marker in lowered for marker in PII_MARKERS):
        if value in (None, ""):
            return value
        return "<present>"
    if isinstance(value, dict):
        return {
            item_key: redact(item_value, item_key, show_secrets=show_secrets)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item, key, show_secrets=show_secrets) for item in value]
    return value


class TuyaMobileClient:
    def __init__(self, args, native_key):
        self.args = args
        self.native_key = native_key
        self.device_id = args.device_id or stable_device_id(
            args.username, args.app_id, args.package_name
        )

    def request(self, api, version, payload=NO_POST_DATA, sid=None, extra=None):
        params = {
            "a": api,
            "v": version,
            "clientId": self.args.app_id,
            "deviceId": self.device_id,
            "appVersion": self.args.app_version,
            "chKey": self.args.ch_key,
            "ttid": self.args.ttid,
            "lang": self.args.lang,
            "os": "Android",
            "et": "0",
            "time": str(int(time.time())),
            "requestId": str(uuid.uuid4()),
            "sdkVersion": self.args.sdk_version,
            "deviceCoreVersion": self.args.device_core_version,
            "osSystem": self.args.os_system,
            "platform": "y",
            "channel": self.args.channel,
            "appRnVersion": self.args.app_rn_version,
            "bizData": "",
            "cp": "",
            "nd": "",
            "timeZoneId": self.args.time_zone_id,
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
            self.args.endpoint,
            data=urllib.parse.urlencode(params).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": f"ThingSmart/{self.args.app_version} Android",
                "Accept-Encoding": "identity",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.args.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return response.status, json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, {"success": False, "raw": body}

    def login(self):
        username = self.args.username.strip()
        token_payload = {
            "countryCode": self.args.country_code,
            "username": username,
            "isUid": False,
        }
        token_status, token_response = self.request(
            self.args.token_api, "2.0", token_payload
        )
        if not token_response.get("success"):
            return token_status, token_response, None, None

        token_result = token_response["result"]
        password_md5 = md5_hex(self.args.password)
        encrypted_password = rsa_pkcs1_v15_encrypt_hex(
            password_md5,
            token_result["publicKey"],
            token_result["exponent"],
        )
        if is_email_username(username):
            login_payload = {
                "countryCode": self.args.country_code,
                "email": username,
                "passwd": encrypted_password,
                "options": LOGIN_OPTIONS,
                "token": token_result["token"],
                "ifencrypt": 1,
            }
            login_status, login_response = self.request(
                self.args.login_api, "3.0", login_payload
            )
            return token_status, token_response, login_status, login_response

        last_status = None
        last_response = None
        for mobile in mobile_username_candidates(username, self.args.country_code):
            for api, version, mfa_field in self.args.mobile_login_apis:
                login_payload = {
                    "countryCode": self.args.country_code,
                    "mobile": mobile,
                    "passwd": encrypted_password,
                    mfa_field: LOGIN_OPTIONS,
                    "token": token_result["token"],
                    "ifencrypt": 1,
                }
                last_status, last_response = self.request(api, version, login_payload)
                if last_response.get("success"):
                    break
                if not should_try_next_mobile_login_api(last_response):
                    break
            if last_response and last_response.get("success"):
                break
            if last_response and not should_try_next_mobile_login_api(last_response):
                break
        return token_status, token_response, last_status, last_response

    def list_homes(self, sid):
        return self.request(*HOME_LIST_API, sid=sid)

    def list_home_devices(self, sid, home_id):
        home_id_text = str(home_id)
        calls = {
            "owned_devices": (
                OWNED_DEVICE_API,
                {"gid": home_id},
            ),
            "device_groups": (
                DEVICE_GROUP_API,
                {"gid": home_id},
            ),
            "device_relations": (
                DEVICE_RELATION_API,
                {"gid": home_id},
            ),
            "local_devices": (
                LOCAL_DEVICE_API,
                {"homeId": home_id, "groupType": "homeGroup"},
            ),
            "energy_devices": (
                ENERGY_DEVICE_API,
                {"groupId": home_id_text, "type": "energy"},
            ),
        }
        result = {}
        for label, ((api, version), payload) in calls.items():
            status, response = self.request(api, version, payload, sid=sid)
            result[label] = {
                "http_status": status,
                "success": response.get("success"),
                "errorCode": response.get("errorCode"),
                "errorMsg": response.get("errorMsg"),
                "result": response.get("result"),
            }
        return result


def result_count(value):
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("deviceList", "list", "devices", "datas"):
            if isinstance(value.get(key), list):
                return len(value[key])
    return None


def home_id_from_home(home):
    return home.get("homeId") or home.get("gid") or home.get("id")


def device_parent_id(device):
    topo = device.get("deviceTopo") if isinstance(device, dict) else None
    if isinstance(topo, dict):
        parent = topo.get("parentDevId") or topo.get("meshId") or topo.get("gatewayId")
        if parent:
            return parent
    communication = device.get("communication") if isinstance(device, dict) else None
    if isinstance(communication, dict):
        node = communication.get("communicationNode")
        if node and node != device.get("devId"):
            return node
    return None


def communication_mode_types(device):
    communication = device.get("communication") if isinstance(device, dict) else None
    if not isinstance(communication, dict):
        return []
    modes = communication.get("communicationModes")
    if not isinstance(modes, list):
        return []
    return [mode.get("type") for mode in modes if isinstance(mode, dict)]


def simplify_device(device, hub_ids, show_secrets=False):
    parent_id = device_parent_id(device)
    dev_id = device.get("devId")
    meta = device.get("meta") if isinstance(device.get("meta"), dict) else {}
    mode_types = communication_mode_types(device)
    is_hub = dev_id in hub_ids or meta.get("zigBleSubEnable") is True or 8 in mode_types
    if is_hub:
        kind = "hub"
    elif parent_id:
        kind = "child"
    else:
        kind = "device"
    local_key = device.get("localKey")
    return {
        "name": device.get("name"),
        "devId": dev_id,
        "kind": kind,
        "parentDevId": parent_id,
        "localKey": redact(local_key, "localKey", show_secrets=show_secrets),
        "ip": device.get("ip") or None,
        "mac": device.get("mac") or None,
        "uuid": device.get("uuid") or None,
        "productId": device.get("productId"),
        "online": device.get("cloudOnline"),
        "communicationModeTypes": mode_types,
    }


def summarize_home_devices(home, device_response, show_secrets=False):
    owned = device_response.get("owned_devices", {}).get("result")
    owned = owned if isinstance(owned, list) else []
    hub_ids = {device_parent_id(device) for device in owned}
    hub_ids.discard(None)
    devices = [
        simplify_device(device, hub_ids, show_secrets=show_secrets)
        for device in owned
        if isinstance(device, dict)
    ]
    return {
        "homeId": home_id_from_home(home),
        "homeName": home.get("name"),
        "counts": {
            label: result_count(data.get("result"))
            for label, data in device_response.items()
        },
        "devices": devices,
        "raw": device_response if show_secrets else redact(device_response),
    }


def native_key_from_args(args):
    if args.native_key_text:
        return args.native_key_text.encode("utf-8")

    app_secret = required(args.app_secret, "--app-secret or TUYA_APP_SECRET")
    cert_sha256 = required(args.cert_sha256, "--cert-sha256 or TUYA_CERT_SHA256")

    if args.bmp_key:
        bmp_key = args.bmp_key
    else:
        bmp = required(args.bmp, "--bmp or TUYA_BMP")
        keys = extract_bmp_keys(args.app_id, bmp)
        if args.key_index < 0 or args.key_index >= len(keys):
            raise SystemExit(f"key index out of range; BMP contained {len(keys)} key(s)")
        bmp_key = keys[args.key_index]

    text_key = derive_native_signing_key(
        args.package_name,
        cert_sha256,
        bmp_key,
        app_secret,
    )
    return text_key.encode("utf-8")


def load_password(args):
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    value = env_value(args.password_env)
    if value:
        return value
    raise SystemExit(
        f"missing password: set {args.password_env} or use --password-stdin"
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Call Tuya Smart mobile email/phone password login with native request signing."
    )
    parser.add_argument(
        "--username",
        default=env_value("TUYA_USERNAME") or env_value("TUYA_EMAIL"),
        help="Login email or phone number; defaults to TUYA_USERNAME or TUYA_EMAIL",
    )
    parser.add_argument("--email", dest="username", help=argparse.SUPPRESS)
    parser.add_argument(
        "--password-env",
        default="TUYA_PASSWORD",
        help="Environment variable containing the password",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of an environment variable",
    )
    parser.add_argument("--country-code", default=env_value("TUYA_COUNTRY_CODE") or "84")

    parser.add_argument(
        "--endpoint", default=env_value("TUYA_ENDPOINT") or DEFAULT_ENDPOINT
    )
    parser.add_argument(
        "--token-api", default=env_value("TUYA_TOKEN_API") or DEFAULT_TOKEN_API
    )
    parser.add_argument(
        "--login-api",
        default=env_value("TUYA_LOGIN_API") or DEFAULT_EMAIL_LOGIN_API,
        help="Email login API override",
    )
    parser.add_argument(
        "--mobile-login-api",
        action="append",
        default=[],
        help="Mobile login API override as api[:version[:mfa_field]]; may repeat",
    )
    parser.add_argument(
        "--timeout", type=int, default=int(env_value("TUYA_TIMEOUT") or "20")
    )

    parser.add_argument(
        "--app-id",
        default=env_value("TUYA_APP_ID"),
        required=env_value("TUYA_APP_ID") is None,
    )
    parser.add_argument("--app-secret", default=env_value("TUYA_APP_SECRET"))
    parser.add_argument("--cert-sha256", default=env_value("TUYA_CERT_SHA256"))
    parser.add_argument("--bmp", default=env_value("TUYA_BMP"))
    parser.add_argument("--bmp-key", default=env_value("TUYA_BMP_KEY"))
    parser.add_argument(
        "--key-index", type=int, default=int(env_value("TUYA_BMP_KEY_INDEX") or "0")
    )
    parser.add_argument("--native-key-text", default=env_value("TUYA_NATIVE_KEY_TEXT"))

    parser.add_argument(
        "--package-name", default=env_value("TUYA_PACKAGE_NAME") or "com.tuya.smart"
    )
    parser.add_argument("--device-id", default=env_value("TUYA_DEVICE_ID"))
    parser.add_argument("--app-version", default=env_value("TUYA_APP_VERSION") or "7.8.6")
    parser.add_argument("--sdk-version", default=env_value("TUYA_SDK_VERSION") or "5.24.0")
    parser.add_argument(
        "--device-core-version",
        default=env_value("TUYA_DEVICE_CORE_VERSION") or "5.17.0",
    )
    parser.add_argument("--os-system", default=env_value("TUYA_OS_SYSTEM") or "15")
    parser.add_argument("--app-rn-version", default=env_value("TUYA_APP_RN_VERSION") or "5.84")
    parser.add_argument("--ch-key", default=env_value("TUYA_CH_KEY") or "3f7060ea")
    parser.add_argument("--ttid", default=env_value("TUYA_TTID") or "international")
    parser.add_argument("--channel", default=env_value("TUYA_CHANNEL") or "oem")
    parser.add_argument("--lang", default=env_value("TUYA_LANG") or "vi_VN")
    parser.add_argument(
        "--time-zone-id",
        default=env_value("TUYA_TIME_ZONE_ID") or "Asia/Ho_Chi_Minh",
    )
    parser.add_argument(
        "--print-full-redacted",
        action="store_true",
        help="Print redacted token and login responses instead of a compact summary",
    )
    parser.add_argument(
        "--action",
        choices=("login", "homes", "devices", "all"),
        default=env_value("TUYA_ACTION") or "login",
        help="What to call after signing in",
    )
    parser.add_argument(
        "--home-id",
        action="append",
        default=[],
        help="Limit --action devices to one home id; may be repeated",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    parser.add_argument(
        "--show-secrets",
        action="store_true",
        help="Do not redact session ids, ecode, local keys, or token fields",
    )
    args = parser.parse_args()
    args.username = required(args.username, "--username, TUYA_USERNAME, or TUYA_EMAIL")
    if args.mobile_login_api:
        args.mobile_login_apis = [
            parse_mobile_login_api(value) for value in args.mobile_login_api
        ]
    else:
        args.mobile_login_apis = list(DEFAULT_MOBILE_LOGIN_APIS)
    args.password = load_password(args)
    return args


def main():
    args = parse_args()
    client = TuyaMobileClient(args, native_key_from_args(args))
    token_status, token_response, login_status, login_response = client.login()

    if not login_response or not login_response.get("success"):
        print(f"token_http_status={token_status} token_success={token_response.get('success')}")
        print(f"login_http_status={login_status} login_success={login_response.get('success') if login_response else None}")
        if login_response:
            print(f"login_error={redact(login_response, show_secrets=args.show_secrets)}")
        return 1

    sid = login_response["result"]["sid"]

    if args.action == "login":
        if args.print_full_redacted or args.json:
            print(
                json.dumps(
                    {
                        "token_http_status": token_status,
                        "token_response": redact(
                            token_response, show_secrets=args.show_secrets
                        ),
                        "login_http_status": login_status,
                        "login_response": redact(
                            login_response, show_secrets=args.show_secrets
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(
                f"token_http_status={token_status} "
                f"token_success={token_response.get('success')}"
            )
            print(
                f"login_http_status={login_status} "
                f"login_success={login_response.get('success')}"
            )
            print(
                f"login_status={login_response.get('status')} "
                f"errorCode={login_response.get('errorCode')}"
            )
            result = login_response.get("result")
            if isinstance(result, dict):
                print(f"uid={result.get('uid')}")
                domain = result.get("domain")
                region = domain.get("regionCode") if isinstance(domain, dict) else None
                print(f"region={region}")
                print(f"sid={redact(result.get('sid'), 'sid', args.show_secrets)}")
                print(f"ecode={redact(result.get('ecode'), 'ecode', args.show_secrets)}")
        return 0

    homes_status, homes_response = client.list_homes(sid)
    if not homes_response.get("success"):
        print(
            json.dumps(
                {
                    "homes_http_status": homes_status,
                    "homes_response": redact(
                        homes_response, show_secrets=args.show_secrets
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    homes = homes_response.get("result") if isinstance(homes_response, dict) else []
    homes = homes if isinstance(homes, list) else []

    if args.action == "homes":
        output = {
            "homes_http_status": homes_status,
            "homes": redact(homes, show_secrets=args.show_secrets),
        }
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(f"homes_http_status={homes_status} homes_count={len(homes)}")
            for home in homes:
                print(
                    f"home name={home.get('name')} "
                    f"homeId={home_id_from_home(home)} role={home.get('role')}"
                )
        return 0

    selected_home_ids = set(args.home_id)
    selected_homes = [
        home
        for home in homes
        if not selected_home_ids or str(home_id_from_home(home)) in selected_home_ids
    ]
    device_summaries = []
    for home in selected_homes:
        home_id = home_id_from_home(home)
        device_response = client.list_home_devices(sid, home_id)
        device_summaries.append(
            summarize_home_devices(
                home, device_response, show_secrets=args.show_secrets
            )
        )

    output = {
        "homes_count": len(homes),
        "selected_homes_count": len(selected_homes),
        "homes": device_summaries,
    }
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(
            f"homes_count={len(homes)} selected_homes_count={len(selected_homes)}"
        )
        for summary in device_summaries:
            counts = summary["counts"]
            print(f"home name={summary['homeName']} homeId={summary['homeId']}")
            print(
                "counts "
                + " ".join(
                    f"{key}={value}" for key, value in counts.items()
                )
            )
            for device in summary["devices"]:
                print(
                    f"device kind={device['kind']} name={device['name']} "
                    f"devId={device['devId']} parent={device['parentDevId']} "
                    f"ip={device['ip']} mac={device['mac']} "
                    f"localKey={device['localKey']}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
