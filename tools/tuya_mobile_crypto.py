#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys


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


def md5_hex(value):
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.md5(value).hexdigest()


def swap_sign_string(value):
    return value[8:16] + value[0:8] + value[24:32] + value[16:24]


def post_data_md5_hex(post_data):
    return swap_sign_string(md5_hex(post_data)) if post_data else ""


def build_sign_input(params):
    normalized = dict(params)
    if normalized.get("postData"):
        normalized["postData"] = post_data_md5_hex(normalized["postData"])
    parts = []
    for key in sorted(normalized):
        value = normalized.get(key)
        if key in SIGN_KEYS and value not in (None, ""):
            parts.append(f"{key}={value}")
    return "||".join(parts)


def verify_response_signature(response, key):
    result = response.get("result")
    timestamp = response.get("t")
    sign = response.get("sign")
    if result is None or timestamp is None or not sign:
        return False
    sign_input = f"result={result}||t={timestamp}||{key.decode('utf-8')}"
    return sign.lower() == md5_hex(sign_input).lower()


def command_sign_input(args):
    params = json.loads(args.params)
    print(build_sign_input(params))


def command_post_md5(args):
    print(post_data_md5_hex(args.post_data))


def command_decrypt_response(args):
    raise SystemExit("decrypt-response is implemented in tools/tuya_mobile_crypto.js")


def main():
    parser = argparse.ArgumentParser(description="Tuya mobile API crypto helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sign_input = subparsers.add_parser("sign-input", help="Build the Java-side string passed to native sign command 1")
    sign_input.add_argument("params", help="JSON object containing request params")
    sign_input.set_defaults(func=command_sign_input)

    post_md5 = subparsers.add_parser("post-md5", help="Compute swapped MD5 used for signed postData")
    post_md5.add_argument("post_data")
    post_md5.set_defaults(func=command_post_md5)

    decrypt = subparsers.add_parser("decrypt-response", help="Decrypt an et=3 response when the native key is known")
    decrypt.add_argument("--key-hex", required=True, help="Hex of raw AES key bytes from getEncryptoKey")
    decrypt.add_argument("--response", required=True, help="Encrypted JSON response body")
    decrypt.add_argument("--verify", action="store_true", help="Verify response sign before decrypting")
    decrypt.set_defaults(func=command_decrypt_response)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
