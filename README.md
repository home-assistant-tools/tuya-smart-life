# Tuya Smart Life Local

Custom Home Assistant integration for logging in with a normal Smart Life or
Tuya Smart email/phone account, fetching homes/devices from the mobile API, and
controlling supported devices directly on the local LAN with their local keys.

This integration does not require a Tuya IoT Cloud project. You do not need to
enter an `app_id`, `app_secret`, certificate fingerprint, or native signing key.

## Features

- Log in with your Smart Life/Tuya Smart email address or phone number and
  password.
- Select one or more homes to sync. You can leave the selection empty to avoid
  loading devices for now.
- Fetch devices, local keys, hub/child topology, MAC/UUID, and initial DPS data
  from the Tuya mobile API.
- Control devices locally through TinyTuya over the LAN, without using cloud
  OpenAPI calls for on/off actions.
- Keep persistent local TCP connections to devices/hubs for realtime DPS
  updates. Streams start automatically when UDP broadcast/scan discovers the
  LAN IP, then refresh/sync once and listen for push updates. Commands also
  prefer the same stream socket to avoid devices rejecting a second LAN
  connection; the integration does not poll local state periodically.
- Listen for Tuya UDP broadcasts and run LAN scans to keep IP/protocol version
  data current when devices or hubs change network details.
- Ignore public/WAN IPs returned by the mobile API and use only private LAN IPs
  for local commands.
- Create switch entities for button/gang DPS values from `dataPointInfo.dps`.
  If `dataPointInfo.dpName` contains labels, those labels are used; otherwise
  entities fall back to names such as `Button <dp_id>`.
- Create fan entities for recognized fan devices, for example devices with
  separate power and speed DPS values, and for supported IR fan remotes.
- Create a diagnostic `Online` binary sensor for hubs so hubs still appear in
  Home Assistant even when they do not expose direct control buttons.
- Create button entities for IR remotes when the Tuya mobile API returns usable
  virtual remote action DPS payloads, including fallback keys for DIY and media
  remotes.
- Detect IR AC/climate, fan, light, TV/set-top box, audio, projector, and DVD
  remotes from Tuya's infrared APIs. Climate, fan, light, and media_player
  commands are sent locally through the IR hub; state is optimistic because IR
  appliances do not report their real state back.
- Clean up stale entities/devices when you change the selected home list.

## Requirements

- Home Assistant with HACS installed.
- Home Assistant must be on the same LAN/broadcast domain as the Tuya devices
  or hubs you want to control locally. The integration needs Tuya UDP broadcast
  packets to discover LAN IP/protocol information and open realtime TCP streams.
- A Smart Life/Tuya Smart account that owns the devices.
- Devices must have local keys in the mobile API and must support the Tuya local
  protocol.
- For IR remotes, the real IR hub must be on the same LAN as Home Assistant.
  Virtual remotes such as TV, AC, or IR fan remotes are app-side devices behind
  the hub, and commands are ultimately sent through the hub.

Important network note: if a Smart Life home is on another LAN/subnet/VLAN,
Home Assistant cannot automatically connect to those devices locally yet.
Cross-network discovery is not supported because Tuya UDP broadcast/discovery
does not cross routers by default. Being able to ping or route TCP to a device
is not enough for the current automatic discovery mechanism. Some other
workarounds or integrations may let you point to a manual IP temporarily, but
that is not recommended: when the device changes IP or protocol version,
Home Assistant will not receive the UDP broadcast needed to update itself and
local realtime/control can break. The stable setup is to select only homes
whose devices/hubs are on the same broadcast domain as Home Assistant.

## HACS Installation

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Open the **...** menu in the top-right corner and choose
   **Custom repositories**.
4. Enter this repository URL:

   ```text
   https://github.com/home-assistant-tools/tuya-smart-life
   ```

5. Select **Integration** as the category/type.
6. Click **Add**.
7. Find **Tuya Smart Life Local** in HACS and click **Download**.
8. Restart Home Assistant.

## Integration Setup

1. Go to **Settings -> Devices & services**.
2. Click **Add integration**.
3. Search for **Tuya Smart Life Local**.
4. Enter your Smart Life/Tuya Smart email address or phone number and password.
   Keep **API region** on **Auto** unless login fails for your account region.
5. After login succeeds, select the homes you want to sync, or leave the list
   empty if you do not want to load any devices yet.
6. Submit the flow and wait for Home Assistant to create devices/entities.

For phone-number login, the default country code is `84`. Change it to your
phone country code if your account is outside Vietnam. The integration
recognizes numbers formatted as `+<country code>...` or
`00<country code>...`. For phone numbers entered with a leading `0`, it also
tries the variant without the leading `0` because the mobile API expects the
country code separately.

The **API region** setting selects the Tuya mobile API endpoint, not a Tuya IoT
Cloud project region. `Auto` currently tries the known Smart Life/Tuya Smart
mobile endpoints for US, Singapore, EU, China, and India, then follows the
domain/region information returned by Tuya after login when available.

After changing the selected home list in integration options, the integration
reloads so Home Assistant can clean up and recreate the correct registry
entries. If you are upgrading from a version older than `0.1.36`, reload or
restart Home Assistant after updating.

## How Local Control Works

The mobile/cloud API is used only for metadata:

- login
- home list
- device list
- local keys
- hub/child relationships
- initial DPS values
- IR remote action metadata

When you turn a switch on or off in Home Assistant, the command path is local:

```text
Home Assistant -> device/hub LAN IP -> TinyTuya -> Tuya local protocol
```

For child devices behind a hub, commands are sent through the parent hub using
`parentDevId` and `node_id`/`cid` when Tuya returns complete topology metadata.

For Zigbee/BLE devices behind hubs, the integration also reads hub UDP
broadcasts to update the protocol version for each child when the broadcast
contains `cid`/`nodeId`. This helps avoid local errors such as
`Check device key or version` when a child uses a different protocol version
from its hub.

For IR remotes, the integration reads action data from the Tuya app
scene/action APIs and sends raw DPS directly to the IR hub on the LAN. It does
not use a Tuya IoT Cloud project and does not call cloud OpenAPI to press
remote buttons.

## Switch Buttons And DPS

Tuya describes switch buttons/gangs through DPS metadata:

- `dataPointInfo.dps`: current value for each DP.
- `dataPointInfo.dpName`: optional label for each DP.

The integration exposes boolean DPS values that look like controllable
buttons/gangs. Auxiliary fields such as indicator, backlight, countdown, or
secondary status values are skipped when they can be recognized. Devices without
`dpName` labels use fallback entity names such as `Button 1`, `Button 2`.

Some devices use boolean DP `1` as the power control for another domain, such
as a fan. When a device is recognized as a fan, that power DP is exposed as a
`fan` entity instead of a `switch`; auxiliary boolean DPS values such as a fan
light can still be exposed as separate switches when Tuya returns them.

## IR Devices

Tuya manages IR devices in two layers:

- Real IR hub: has the local key, LAN IP, and receives local commands.
- Virtual IR remote: TV, AC, fan, and similar remotes behind the IR hub. These
  have a `remote_id`.

The integration calls `tuya.m.infrared.gateway.get` and
`tuya.m.infrared.keydata.get` to fetch virtual IR remotes and their key data,
then builds local hub DP `201` payloads from the same data used by the Tuya app
IR panel. It also calls `thing.m.linkage.dev.list` and
`thing.m.linkage.function.list` to fetch remotes/actions used by the Tuya app
automation editor, and tries scene-rule APIs such as
`thing.m.linkage.rule.query` and `thing.m.linkage.rule.detail.find` to import
IR payloads saved by the app in scenes. If an action contains valid raw DPS,
Home Assistant creates a matching button and publishes that raw DPS directly to
the local IR hub. The virtual remote `remote_id` is used only for naming,
entity identity, and report metadata; it is not packaged as a local `cid` in
the frame sent to the hub.

Recognized IR remotes are mapped to Home Assistant platforms:

- AC/air conditioner: `climate`.
- Fan: `fan`.
- TV, set-top box, TV box, audio, projector, DVD: `media_player` plus raw key
  buttons for commands that do not fit Home Assistant's media model.
- Light: `light`.
- DIY/unknown: `button` fallback.

IR control is one-way, so Home Assistant state represents the last command
sent, not a state read back from the appliance.

To debug IR data outside Home Assistant:

```bash
python3 tools/tuya_mobile_login.py --action ir --home-id <home-id>
python3 tools/tuya_mobile_login.py --action ir --home-id <home-id> --json
```

The script redacts session/key/token values by default and prints only the
remote, category, hub, function, and discovered `actionDps` payloads.

## Updating

HACS detects GitHub releases from this repository. To update:

1. Open HACS.
2. Open the **Tuya Smart Life Local** repository page.
3. Click **Update information** if the new version is not visible yet.
4. Click **Download/Redownload** for the new version.
5. Restart Home Assistant.

## Troubleshooting

### Login Shows `cannot_connect`

- Check the email/phone number/password.
- Check Home Assistant internet access.
- If the account was created in another Smart Life/Tuya Smart region, leave
  **API region** on **Auto** or try the matching region manually.
- If Smart Life requires MFA or another secondary verification flow, the login
  script may not handle that flow yet.

### Entity Is Unavailable Or Local Control Fails

- Check that Home Assistant and the device/hub are on the same LAN/broadcast
  domain.
- If Home Assistant runs in Docker/TrueNAS, use a network mode that can receive
  LAN broadcasts. The integration needs to listen on UDP `6666`, `6667`, `6699`,
  and `7000`, and it needs TCP access to the local device/hub.
- Cross-subnet/VLAN/WAN discovery is not supported automatically. Tuya UDP
  broadcast does not cross routers, so Home Assistant may be able to ping/TCP
  the device IP while still being unable to learn IP/protocol changes for
  stable realtime local control.
- If you use another approach to point to a manual IP for a device on another
  LAN, treat it as a temporary workaround. When the device IP/version changes,
  Home Assistant will not receive broadcast updates and the entity may become
  unavailable or local control may fail.
- If the mobile API returns a public/WAN IP, the integration ignores it and
  waits for broadcast or LAN scan to find a private IP.
- Some devices may return mismatched local key/protocol data. TinyTuya reports
  this as errors such as `Check device key or version`. The integration
  prioritizes the version learned from UDP broadcast and tries protocol
  fallbacks for child devices behind hubs.

### IR Remote Or IR Climate Does Not Appear

- Check that the selected home has an IR hub on the same LAN as Home Assistant.
- Run `tools/tuya_mobile_login.py --action ir --home-id <home-id>` to see
  whether the mobile API returns remote/action data.
- If Tuya returns only raw standalone buttons, the integration creates button
  entities instead of a higher-level climate/fan/light/media_player entity.
- If neither API nor scenes return `actionDps`/`executorProperty`, the current
  data is not enough to press the local IR action. In that case, creating a
  scene in the Tuya app for the needed IR button may help the app store the
  corresponding payload.

### Wrong Home Selected

Open the integration options and remove that home from the selected home list.
You can select no homes if you want the integration to keep only login/home-list
data without loading devices. The integration cleans stale entities/devices
after reload/restart.

## Technical Notes

This README is for installation and day-to-day use. Reverse engineering, MITM,
mobile API, signing, and crypto notes live in separate technical documents:

- [Reverse engineering and MITM notes](docs/reverse-engineering.md)
- [Tuya Smart Android API findings](docs/tuya-smart-android-api-findings.md)

APKs and decompiled source are not committed to this repository. Only notes,
tooling, and the Home Assistant integration are kept here.
