# Tuya Smart Life API Notes

This repository collects notes from reverse engineering the Android Tuya Smart app
for Home Assistant and smart-home integration research.

Current document:

- [Tuya Smart Android API findings](docs/tuya-smart-android-api-findings.md)

The findings document now includes the required mobile API map for:

- email/password login
- home listing
- device listing with hub/subdevice topology
- local key, IP, and BLE identifier extraction
- request signing and encrypted response decryption notes

## HACS Custom Integration

This repository includes a Home Assistant custom integration at
`custom_components/tuya_smart_life_local`. It logs in with the Smart Life /
Tuya Smart mobile API, lets you select one or more homes, fetches devices and
local keys for the selected homes, then controls supported devices locally.

### HACS installation

1. In HACS, open **Integrations**.
2. Click **... -> Custom repositories**.
3. Add this repository URL and select category **Integration**:
   ```bash
   https://github.com/home-assistant-tools/tuya-smart-life
   ```
4. Install **Tuya Smart Life Local** and restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add integration** and search for
   **Tuya Smart Life Local**.

### Login and home selection

The config flow asks only for the Tuya account email/username and password. The
mobile app signing profile recovered from Smart Life 7.8.6 is bundled in the
integration, so users do not need to provide app id, app secret, certificate
fingerprint, BMP `secret2`, SDK version, or native signing key material.

After login succeeds, Home Assistant shows a multi-select list of Tuya homes.
Choose one or more homes to sync. The selection can be changed later from the
integration options.

### Local control behavior

- Device metadata, local keys, hub/child relationships, DPS values, MAC
  addresses, BLE identifiers when Tuya exposes them, and cloud-provided IP
  fields are fetched through the mobile API.
- LAN control uses TinyTuya and Tuya protocol 3.3/3.4/3.5. Child devices are
  addressed through the parent hub with the child's `node_id` / `cid` when
  topology metadata is available.
- The integration listens for Tuya UDP broadcasts on ports `6666`, `6667`,
  `6699`, and `7000`, and also runs a periodic TinyTuya LAN scan. Broadcast and
  scan results update the IP cache so local commands use the current LAN IP.
- The first HACS version exposes boolean DPS values as Home Assistant switch
  entities. The runtime keeps the richer metadata needed to add lights, covers,
  sensors, locks, and fans later.

## Capture Replay Tool

The `tools/replay_tuya_capture_request.py` helper can replay a signed Tuya
mobile API request from a local mitmproxy capture:

```bash
python3 tools/replay_tuya_capture_request.py /path/to/capture.mitm --list
python3 tools/replay_tuya_capture_request.py /path/to/capture.mitm --api m.life.home.space.list
python3 tools/replay_tuya_capture_request.py /path/to/capture.mitm --api m.life.app.smart.local.device.list
```

This is a replay/debug tool. It reuses the captured signed envelope, session
fields, and encrypted `postData`; it does not generate fresh Tuya signatures or
decrypt encrypted `result` payloads yet.

## Standalone Login Tool

`tools/tuya_mobile_login.py` performs a fresh email/password login using the
mobile API request signature. Keep credentials and extracted app material in
environment variables rather than committing them:

```bash
export TUYA_EMAIL='user@example.com'
export TUYA_PASSWORD='...'
export TUYA_APP_ID='<client-id>'
export TUYA_APP_SECRET='<app-secret>'
export TUYA_CERT_SHA256='<apk-cert-sha256>'
export TUYA_BMP='/path/to/t_s.bmp'

python3 tools/tuya_mobile_login.py
python3 tools/tuya_mobile_login.py --action homes
python3 tools/tuya_mobile_login.py --action devices --home-id <home-id>
python3 tools/tuya_mobile_login.py --action devices --json
```

The script redacts session secrets by default. It uses plaintext `et=0` for the
login, home-list, and direct device-list calls, while still applying the native
mobile request signature. Device output includes hub/child classification,
parent hub id, local key presence, IP, MAC, UUID, and product id. Use
`--show-secrets` only when you intentionally want to print raw `sid`, `ecode`,
tokens, and local keys.

Some post-login APIs, especially encrypted batch wrappers, still require the SDK
`et=3` AES-GCM request/response layer.

## Mobile Crypto Helpers

`tools/tuya_mobile_crypto.js` implements the Java-side request signing input
format, the swapped MD5 used for encrypted `postData`, and `et=3` response
decryption when the per-request AES key is known:

```bash
node tools/tuya_mobile_crypto.js post-md5 '{"homeId":92258848}'
node tools/tuya_mobile_crypto.js sign-input '{"a":"m.life.home.space.list","v":"1.0"}'
node tools/tuya_mobile_crypto.js request-sign --native-key-hex <key> --input '<canonical-input>'
node tools/tuya_mobile_crypto.js decrypt-response --key-hex <key> --response '<json>'
```

`tools/tuya_mobile_crypto.py` also includes the native signing-key derivation
for current Thing/Tuya SDK builds:

```bash
python3 tools/tuya_mobile_crypto.py extract-bmp-key --app-id <client-id> --bmp /path/to/t_s.bmp
python3 tools/tuya_mobile_crypto.py derive-native-key \
  --package-name <android-package> \
  --cert-sha256 <apk-cert-sha256> \
  --app-id <client-id> \
  --app-secret <app-secret> \
  --bmp /path/to/t_s.bmp
```

`tools/frida_tuya_network_crypto_dump.js` hooks the Android app to log native
sign inputs/results, per-request encryption keys, encrypted request plaintext,
and decrypted response plaintext.

`tools/frida_tuya_sign_key_probe.js` verifies the native request-signing
algorithm in-process. It checks that command `1` equals HMAC-SHA256 with the
initialized native key and does not print the key bytes by default.

The APK and decompiled application sources are intentionally not committed here.
Only the derived API notes are stored in this repository.
