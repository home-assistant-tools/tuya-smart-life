#!/usr/bin/env python3
import argparse
from fractions import Fraction
import hashlib
import hmac
import json
from pathlib import Path
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


def request_sign(sign_input, native_key):
    return hmac.new(native_key, sign_input.encode("utf-8"), hashlib.sha256).hexdigest()


def java_string_hash(value):
    result = 0
    for byte in value.encode("utf-8"):
        result = ((result << 5) - result + byte) & 0xFFFFFFFF
    if result & 0x80000000:
        result -= 0x100000000
    return result


def cyclic_bytes_to_hex(data, offset, count):
    length = len(data)
    return "".join(f"{data[(offset + i) % length]:02X}" for i in range(count))


def read_u32_be_cyclic(data, offset):
    length = len(data)
    return (
        (data[offset % length] << 24)
        | (data[(offset + 1) % length] << 16)
        | (data[(offset + 2) % length] << 8)
        | data[(offset + 3) % length]
    )


def extract_bmp_keys(app_id, bmp_path):
    content = Path(bmp_path).read_bytes()
    if len(content) <= 0x36:
        raise ValueError("BMP content is too short")

    pixel_data = content[0x36:]
    hash_value = java_string_hash(app_id)
    if hash_value < 0:
        hash_value = -hash_value
    cursor = (hash_value % len(pixel_data)) // 2

    key_count = pixel_data[(cursor + 1) % len(pixel_data)]
    coeff_count = pixel_data[(cursor + 2) % len(pixel_data)]
    if key_count < 1 or key_count > 4:
        raise ValueError("invalid key count for this app id and BMP")
    if coeff_count < 1:
        raise ValueError("invalid coefficient count for this app id and BMP")

    offset = cursor ^ read_u32_be_cyclic(pixel_data, cursor + 3)
    keys = []
    for _ in range(key_count):
        points = []
        for _ in range(coeff_count):
            x_offset = offset % len(pixel_data)
            x_length = pixel_data[x_offset]
            x_value = int(cyclic_bytes_to_hex(pixel_data, x_offset + 1, x_length), 16)

            y_offset = (x_offset + x_length + 1) % len(pixel_data)
            y_length = pixel_data[y_offset]
            y_value = int(cyclic_bytes_to_hex(pixel_data, y_offset + 1, y_length), 16)

            next_offset = (y_offset + y_length + 1) % len(pixel_data)
            offset = x_offset ^ read_u32_be_cyclic(pixel_data, next_offset)
            points.append((x_value, y_value))

        key_int = interpolate_at_zero(points)
        key_hex = f"{key_int:X}"
        if len(key_hex) % 2:
            key_hex = "0" + key_hex
        keys.append(bytes.fromhex(key_hex).decode("utf-8"))

    return keys


def interpolate_at_zero(points):
    result = Fraction(0)
    for i, (x_i, y_i) in enumerate(points):
        term = Fraction(y_i)
        for j, (x_j, _) in enumerate(points):
            if i != j:
                term *= Fraction(-x_j, x_i - x_j)
        result += term
    if result.denominator != 1:
        raise ValueError("interpolated BMP key is not an integer")
    return result.numerator


def normalize_cert_sha256(value):
    stripped = value.replace(":", "").replace(" ", "").lower()
    if len(stripped) != 64 or any(ch not in "0123456789abcdef" for ch in stripped):
        raise ValueError("certificate SHA-256 must contain 64 hex characters")
    return ":".join(stripped[i : i + 2].upper() for i in range(0, len(stripped), 2))


def derive_native_signing_key(package_name, cert_sha256, bmp_key, app_secret):
    cert = normalize_cert_sha256(cert_sha256)
    return f"{package_name}_{cert}_{bmp_key}_{app_secret}"


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


def command_request_sign(args):
    if bool(args.native_key_hex) == bool(args.native_key_text):
        raise SystemExit("provide exactly one of --native-key-hex or --native-key-text")
    native_key = (
        bytes.fromhex(args.native_key_hex)
        if args.native_key_hex
        else args.native_key_text.encode("utf-8")
    )
    print(request_sign(args.input, native_key))


def command_extract_bmp_key(args):
    keys = extract_bmp_keys(args.app_id, args.bmp)
    for index, key in enumerate(keys):
        if args.show_key:
            print(f"{index}:{key}")
        else:
            print(f"{index}:len={len(key)} sha256={hashlib.sha256(key.encode('utf-8')).hexdigest()}")


def command_derive_native_key(args):
    if args.bmp_key:
        bmp_key = args.bmp_key
    else:
        if not args.app_id or not args.bmp:
            raise SystemExit("provide --bmp-key, or provide both --app-id and --bmp")
        keys = extract_bmp_keys(args.app_id, args.bmp)
        if args.key_index < 0 or args.key_index >= len(keys):
            raise SystemExit(f"key index out of range; BMP contained {len(keys)} key(s)")
        bmp_key = keys[args.key_index]
    native_key = derive_native_signing_key(
        args.package_name,
        args.cert_sha256,
        bmp_key,
        args.app_secret,
    )
    if args.show_key:
        print(native_key)
    else:
        print(f"len={len(native_key)} sha256={hashlib.sha256(native_key.encode('utf-8')).hexdigest()}")


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

    request = subparsers.add_parser("request-sign", help="Compute final native request sign when the native signing key is known")
    request.add_argument("--input", required=True, help="Canonical sign input from sign-input")
    request.add_argument("--native-key-hex", help="Hex of raw native signing key bytes")
    request.add_argument("--native-key-text", help="Native signing key as UTF-8 text")
    request.set_defaults(func=command_request_sign)

    bmp_key = subparsers.add_parser("extract-bmp-key", help="Extract secret key material from Tuya t_s.bmp/fixed_key BMP content")
    bmp_key.add_argument("--app-id", required=True, help="Tuya app id/client id used to select bytes in the BMP")
    bmp_key.add_argument("--bmp", required=True, help="Path to t_s.bmp or compatible Tuya security BMP")
    bmp_key.add_argument("--show-key", action="store_true", help="Print the extracted key instead of only length/hash")
    bmp_key.set_defaults(func=command_extract_bmp_key)

    native_key = subparsers.add_parser("derive-native-key", help="Derive the command 1 native signing key for current Thing/Tuya SDK builds")
    native_key.add_argument("--package-name", required=True, help="Android package name, for example com.tuya.smart")
    native_key.add_argument("--cert-sha256", required=True, help="APK signing certificate SHA-256, with or without colons")
    native_key.add_argument("--app-secret", required=True, help="Tuya app secret from app config")
    native_key.add_argument("--app-id", help="Tuya app id/client id; required when --bmp-key is not supplied")
    native_key.add_argument("--bmp", help="Path to t_s.bmp; required when --bmp-key is not supplied")
    native_key.add_argument("--bmp-key", help="Pre-extracted BMP key/secret2")
    native_key.add_argument("--key-index", type=int, default=0, help="BMP key index to use when the BMP contains multiple keys")
    native_key.add_argument("--show-key", action="store_true", help="Print the derived native key instead of only length/hash")
    native_key.set_defaults(func=command_derive_native_key)

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
