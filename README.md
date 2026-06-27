# Tuya Smart Life API Notes

This repository collects notes from reverse engineering the Android Tuya Smart app
for Home Assistant and smart-home integration research.

Current document:

- [Tuya Smart Android API findings](docs/tuya-smart-android-api-findings.md)

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

The APK and decompiled application sources are intentionally not committed here.
Only the derived API notes are stored in this repository.
