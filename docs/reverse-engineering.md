# Reverse Engineering And MITM Notes

This document keeps the reverse-engineering, MITM, replay, signing, and crypto
notes separate from the user-facing Home Assistant installation guide.

For the full API map and Android evidence, see:

- [Tuya Smart Android API findings](tuya-smart-android-api-findings.md)

## Scope

The Home Assistant integration in this repository already bundles the recovered
mobile signing profile needed for normal users. You do not need the material in
this document to install or use the integration.

These notes are useful when you want to:

- replay captured mobile API calls
- inspect MITM captures
- test fresh mobile API login calls outside the app
- verify request signing
- investigate encrypted `et=3` request/response handling
- update the integration for a newer Tuya/Smart Life app version

Do not commit APKs, decompiled source, account credentials, session tokens, or
raw local keys.

## Main API Findings

The Android app uses Tuya/Thing ATOP-style calls through `POST /api.json`
instead of ordinary REST paths. Important envelope fields include `a`, `v`,
`sid`, `postData`, `time`, `requestId`, `clientId`, `deviceId`, `chKey`, `et`,
and `sign`.

Confirmed plaintext `et=0` calls used by the integration:

| Purpose | API | Version | Main payload |
| --- | --- | --- | --- |
| Login token | `smartlife.m.user.username.token.get` | `2.0` | `countryCode`, `username`, `isUid` |
| Email/password login | `smartlife.m.user.email.password.login` | `3.0` | `countryCode`, `email`, `passwd`, `token`, `ifencrypt` |
| Mobile/password login | `smartlife.m.user.mobile.passwd.login` or `thing.m.user.mobile.passwd.login` | `4.0` | `countryCode`, `mobile`, `passwd`, `token`, `ifencrypt`, MFA metadata |
| Home list | `m.life.home.space.list` | `1.0` | none |
| Device list in home | `m.life.my.group.device.list` | `2.2` | `gid` |
| Device detail | `thing.m.device.get` | `4.1` | `devId` |
| Device relation/order | `m.life.my.group.device.relation.list` | `3.2` | `gid` |
| Energy/compact device list | `m.energy.home.device.list` | `3.0` | `groupId`, optional `type` |
| Local/direct device list | `m.life.app.smart.local.device.list` | `1.1` | `homeId`, `groupType=homeGroup` |

Some post-login batch/plugin APIs still require SDK encryption with `et=3`.

## Switch Button DPS Source

For switches and gang devices, controllable buttons come from the device DPS
metadata returned by mobile APIs:

- `dataPointInfo.dps`: current DP values
- `dataPointInfo.dpName`: optional DP labels
- `dataPointInfo.dpsTime`: last update timestamps

Live checks against the current account showed:

- `m.life.my.group.device.list` v2.2 returns `dataPointInfo.dps`.
- `thing.m.device.get` v4.1 returns the same `dataPointInfo` block.
- `dataPointInfo.dpName` can be empty for all devices, so the integration must
  fall back to DP ids.
- `m.energy.home.device.list` v3.0 returns a compact device list without DPS
  labels.
- `m.life.app.smart.local.device.list` v1.1 can return an empty object for a
  home even when normal device listing works.

The integration exposes boolean DPS values that look like switch buttons/gangs
and avoids known auxiliary fields such as indicator/backlight/countdown when
they can be recognized.

## Capture Replay Tool

`tools/replay_tuya_capture_request.py` replays a signed Tuya mobile request from
a local mitmproxy capture:

```bash
python3 tools/replay_tuya_capture_request.py /path/to/capture.mitm --list
python3 tools/replay_tuya_capture_request.py /path/to/capture.mitm --api m.life.home.space.list
python3 tools/replay_tuya_capture_request.py /path/to/capture.mitm --api m.life.app.smart.local.device.list
```

This helper reuses the captured signed envelope, session fields, and encrypted
`postData`. It does not generate fresh signatures or decrypt encrypted `result`
payloads by itself.

## Standalone Login Tool

`tools/tuya_mobile_login.py` performs a fresh email/password or mobile/password
login using the recovered mobile request signature. Keep credentials and
extracted app material in environment variables:

```bash
export TUYA_USERNAME='user@example.com'
# or: export TUYA_USERNAME='0912345678'
export TUYA_PASSWORD='...'
export TUYA_APP_ID='<client-id>'
export TUYA_APP_SECRET='<app-secret>'
export TUYA_CERT_SHA256='<apk-cert-sha256>'
export TUYA_BMP='/path/to/t_s.bmp'

python3 tools/tuya_mobile_login.py
python3 tools/tuya_mobile_login.py --username 0912345678
python3 tools/tuya_mobile_login.py --action homes
python3 tools/tuya_mobile_login.py --action devices --home-id <home-id>
python3 tools/tuya_mobile_login.py --action devices --json
```

The script redacts session secrets by default. Device output includes
hub/child classification, parent id, local key presence, IP, MAC, UUID, product
id, and online state. Use `--show-secrets` only when you intentionally need raw
`sid`, `ecode`, tokens, or local keys.

## Mobile Crypto Helpers

`tools/tuya_mobile_crypto.js` implements the Java-side request signing input
format, swapped MD5 for encrypted `postData`, and `et=3` response decryption
when the per-request AES key is known:

```bash
node tools/tuya_mobile_crypto.js post-md5 '{"homeId":92258848}'
node tools/tuya_mobile_crypto.js sign-input '{"a":"m.life.home.space.list","v":"1.0"}'
node tools/tuya_mobile_crypto.js request-sign --native-key-hex <key> --input '<canonical-input>'
node tools/tuya_mobile_crypto.js decrypt-response --key-hex <key> --response '<json>'
```

`tools/tuya_mobile_crypto.py` includes native signing-key derivation helpers:

```bash
python3 tools/tuya_mobile_crypto.py extract-bmp-key --app-id <client-id> --bmp /path/to/t_s.bmp
python3 tools/tuya_mobile_crypto.py derive-native-key \
  --package-name <android-package> \
  --cert-sha256 <apk-cert-sha256> \
  --app-id <client-id> \
  --app-secret <app-secret> \
  --bmp /path/to/t_s.bmp
```

## Frida Helpers

`tools/frida_tuya_network_crypto_dump.js` hooks the Android app to log native
sign inputs/results, per-request encryption keys, encrypted request plaintext,
and decrypted response plaintext.

`tools/frida_tuya_sign_key_probe.js` verifies the native request-signing
algorithm in-process. It checks that command `1` equals HMAC-SHA256 with the
initialized native key and does not print the key bytes by default.

## Updating For A New App Version

When Tuya/Smart Life changes app signing material:

1. Pull the APK/splits from a real device.
2. Patch anti-tamper/certificate pinning only in a local test environment.
3. Capture login and device-list calls with mitmproxy.
4. Verify the native signing key and request canonicalization with Frida.
5. Confirm login, home list, and device list using standalone scripts.
6. Update the integration defaults only after plaintext `et=0` login and device
   metadata calls are confirmed.

Keep raw captures and app binaries outside the repository.
