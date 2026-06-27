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

## Mobile Crypto Helpers

`tools/tuya_mobile_crypto.js` implements the Java-side request signing input
format, the swapped MD5 used for encrypted `postData`, and `et=3` response
decryption when the per-request AES key is known:

```bash
node tools/tuya_mobile_crypto.js post-md5 '{"homeId":92258848}'
node tools/tuya_mobile_crypto.js sign-input '{"a":"m.life.home.space.list","v":"1.0"}'
node tools/tuya_mobile_crypto.js decrypt-response --key-hex <key> --response '<json>'
```

`tools/frida_tuya_network_crypto_dump.js` hooks the Android app to log native
sign inputs/results, per-request encryption keys, encrypted request plaintext,
and decrypted response plaintext.

The APK and decompiled application sources are intentionally not committed here.
Only the derived API notes are stored in this repository.
