# Tuya Smart Android API Findings

This document summarizes API knowledge extracted from the Android package
`com.tuya.smart` pulled from a Samsung S21 Ultra.

Extraction workspace:

- Package: `com.tuya.smart`
- Pulled APKs:
  - `pulled/com.tuya.smart/base.apk`
  - `pulled/com.tuya.smart/split_config.arm64_v8a.apk`
- Decompiled with:
  - `apktool` into `decompiled/com.tuya.smart/`
  - `jadx` into `jadx_out/com.tuya.smart/`

Important observation: the app does not expose ordinary REST paths such as
`/v1/...` for these flows. It uses Tuya/Thing ATOP-style calls through
`ApiParams(apiName, version, countryCode?)`. Host routing, session handling,
request signing, and transport are handled by the Tuya SDK.

## Required API Map

All calls below are mobile ATOP calls sent as `POST /api.json` form data. The
important envelope fields are `a`, `v`, `sid`, `gid`, `postData`, `time`,
`requestId`, `chKey`, and `sign`. For `et=3`, `postData` and `result` are
encrypted by the mobile SDK.

### 1. Email/Password Login

Observed successful account login sequence:

| Step | API name | Version | Session | Main `postData` fields | Notes |
| --- | --- | --- | --- | --- | --- |
| Login token | `smartlife.m.user.username.token.get` | `2.0` | No | `countryCode`, `username`, `isUid` | First call before password login. `username` is the email for email login. |
| Password login | `smartlife.m.user.email.password.login` | `3.0` | No | `countryCode`, `email`, `passwd`, `token`, `ifencrypt`, `extInfo` | Confirmed live through the Android app. |

Static SDK wrappers also contain older/internal `thing.m.*` names:

| Purpose | API name | Version |
| --- | --- | --- |
| Login token | `thing.m.user.username.token.get` | `2.0` |
| Email password login | `thing.m.user.email.password.login` | likely `2.0` |
| Mobile password login | `thing.m.user.mobile.passwd.login` | `4.0` |

### 2. List Homes

| Purpose | API name | Version | Session | Main `postData` fields | Important response fields |
| --- | --- | --- | --- | --- | --- |
| List homes/spaces | `m.life.home.space.list` | `1.0` | Yes | none observed | `homeId`/`gid`, `name`, `geoName`, `longitude`, `latitude`, role/admin fields |
| Home detail | `m.life.location.get` | `3.4` | Yes | `gid` | Detailed `HomeResponseBean` for one home |

The live account capture loaded home `Kiara` with `gid=92258848`.

### 3. List Devices And Hub/Child Topology

The app usually refreshes home devices through a batch call:

| Purpose | API name | Version | Session | Main `postData` fields |
| --- | --- | --- | --- | --- |
| Batch wrapper | `smartlife.m.api.batch.invoke` or `thing.m.api.batch.invoke` | `1.0` | Yes | `gid`, `apis` |

Useful nested APIs inside the batch:

| Purpose | API name | Version | Main fields |
| --- | --- | --- | --- |
| Devices in home | `m.life.my.group.device.list` | `2.2` | `gid` |
| Device groups | `m.life.my.group.device.group.list` | `4.3` | `gid` |
| Device relation list | `m.life.my.group.device.relation.list` | `3.2` | `gid` |
| Mesh list | `m.life.my.group.mesh.list` | `3.1` | `gid` |
| Sort/order list | `m.life.my.group.device.sort.list` | `2.1` | `gid` |
| Device reference info | `m.life.device.ref.info.my.list` | `7.2` | `gid`, `zigbeeGroup=true` |
| Shared devices | `thing.m.my.shared.device.list` | `3.2` | current user/session |
| Shared groups | `thing.m.my.shared.device.group.list` | likely `2.0` | current user/session |

Direct calls for detail and subdevices:

| Purpose | API name | Version | Main `postData` fields | Return model |
| --- | --- | --- | --- | --- |
| Device detail | `thing.m.device.get` | `4.1` | `devId`, optional request `gid` | `DeviceRespBean` |
| Hub subdevice list | `thing.m.device.sub.list` | `2.1` | `meshId` | `ArrayList<DeviceRespBean>` |
| One subdevice detail | `thing.m.device.sub.get` | `2.1` | `meshId`, `devId` | `DeviceRespBean` |
| Local/direct device list | `m.life.app.smart.local.device.list` | `1.1` | `homeId`, `groupType=homeGroup` | `ThingLocalDeviceListDataBean.deviceList` |

Device fields to preserve from `DeviceRespBean`:

| Field | Meaning |
| --- | --- |
| `devId` | Tuya device id |
| `name` | Display name |
| `productId`, `productVer`, `productInfo.category`, `productInfo.categoryCode` | Product/category metadata |
| `uuid`, `mac`, `ip` | Hardware/network identifiers when present |
| `localKey`, `devKey`, `secKey` | Local protocol credentials when returned |
| `deviceTopo.meshId` | Mesh/gateway context |
| `deviceTopo.nodeId` | Subdevice node id |
| `deviceTopo.parentDevId` | Parent gateway/hub device id |
| `communication.communicationModes` | Transport modes, useful for Zigbee/BLE/Wi-Fi classification |
| `communication.connectionStatus` | Connectivity status |
| `meta` | Extra flags such as Matter bridge gateway/subdevice |

Topology inference used by the app model:

1. Build a map of `devId -> DeviceRespBean` from the home device list.
2. A device is a child/subdevice if `deviceTopo.parentDevId` is non-empty.
   Its parent hub is that `parentDevId`.
3. If `parentDevId` is empty but `deviceTopo.meshId` is non-empty, treat
   `meshId` as the mesh/gateway context. Resolve it to a hub by matching a
   device with that `devId`, or by calling `thing.m.device.sub.list`.
4. A hub/gateway is any device referenced by another device's
   `parentDevId`, any device with a subdevice list, or a Matter bridge gateway
   indicated by `meta` containing `matterBridgeGateway`.
5. Matter bridge children can also be flagged by `meta` containing
   `matterBridgeSub`.

### 4. Local Key, IP, And BLE Address

| Data needed | Preferred source | Fallback/source notes |
| --- | --- | --- |
| Local key | `thing.m.device.key.get` v1.0 | `DeviceRespBean.localKey` may already be present in list/detail/local list responses. |
| IP address | `DeviceRespBean.ip` | Returned by device list/detail/local direct-device APIs when known. |
| BLE address | BLE scan beans: `BLEScanDevBean.address` or `ScanDeviceBean.address` | Bound cloud device records usually expose `mac` and `uuid`, not a dedicated `bleAddress` field. Use `mac` first, then `uuid`, unless a local BLE scan result is available. |

Local-key API:

| Purpose | API name | Version | Main `postData` fields | Return model |
| --- | --- | --- | --- | --- |
| Get local keys | `thing.m.device.key.get` | `1.0` | `gwId`, optional `nodeIds` JSON string | `ArrayList<LocalKeyBean>` with `devId`, `localKey` |

Call patterns:

```json
// Direct/Wi-Fi device
{
  "gwId": "<devId>"
}
```

```json
// Hub child/subdevice
{
  "gwId": "<hubDevId>",
  "nodeIds": "[\"<childNodeId>\"]"
}
```

For subdevices, use `deviceTopo.parentDevId` as `gwId` and
`deviceTopo.nodeId` in `nodeIds`. If the device is mesh-style and has no
`parentDevId`, use the resolved mesh/hub id from `deviceTopo.meshId`.

## Login

Evidence files:

- `jadx_out/com.tuya.smart/sources/com/thingclips/smart/login/skt/business/LoginBusiness.java`
- `jadx_out/com.tuya.smart/sources/com/thingclips/sdk/user/pqdbppq.java`

### Username Token

- API name: `thing.m.user.username.token.get`
- Version: `2.0`
- Session required: no
- Main parameters:
  - `countryCode`
  - `username`
  - `isUid`
- Evidence: `LoginBusiness.java:59-65`

This appears to be the first step before password login. The returned token is
submitted with the later password-login request.

### Mobile Password Login

- API name: `thing.m.user.mobile.passwd.login`
- Version: `4.0`
- Session required: no
- Main parameters:
  - `countryCode`
  - `mobile`
  - `passwd`
  - `token`
  - `ifencrypt`
  - `extInfo`, containing MFA metadata such as `group` and `mfaCode`
- Evidence: `LoginBusiness.java:129-138`

### Email Password Login

- API name: `thing.m.user.email.password.login`
- Version: likely `2.0` in this build, referenced through
  `GwBroadcastMonitorService.mVersion`
- Session required: no
- Main parameters:
  - `countryCode`
  - `email`
  - `passwd`
  - `token`
  - `ifencrypt`
  - `extInfo`, containing MFA metadata such as `group` and `mfaCode`
- Evidence: `LoginBusiness.java:141-150`

### Related User APIs

| Purpose | API name | Version | Notes |
| --- | --- | --- | --- |
| User info | `thing.m.user.info.get` | `1.0` | Session required. Evidence: `pqdbppq.java:339-342` |
| Logout | `thing.m.user.loginout` | unknown | Constant evidence: `pqdbppq.java:66` |
| UID password login | `thing.m.user.uid.password.login` | unknown | Constant evidence: `pqdbppq.java:45` |
| Email code login | `thing.m.user.email.code.login` | unknown | Constant evidence: `pqdbppq.java:44` |
| Mobile code login | `thing.m.user.mobile.code.login` | unknown | Constant evidence: `pqdbppq.java:107` |
| Domain query | `thing.m.app.domain.query` | unknown | Constant evidence: `pqdbppq.java:48` |

## Home List

Evidence files:

- `jadx_out/com.tuya.smart/sources/com/thingclips/sdk/home/o00O0O.java`
- `jadx_out/com.tuya.smart/sources/com/thingclips/sdk/home/o0OOO0o.java`
- `decompiled/com.tuya.smart/assets/thing_uni_plugins/TUNIHomeDataManager.json`

### List Homes

- API name: `m.life.home.space.list`
- Version: `1.0`
- Return type in SDK: `ArrayList<HomeResponseBean>`
- Evidence:
  - Constant: `o00O0O.java:33`
  - Request: `o00O0O.java:91-92`
  - SDK wrapper: `o0OOO0o.java:87-90`

The SDK-level wrapper is exposed as `queryHomeList(...)`.

### Get Home Detail

- API name: `m.life.location.get`
- Version: `3.4`
- Main parameters:
  - `gid`
- Return type in SDK: `HomeResponseBean`
- Evidence: `o00O0O.java:85-88`

### Home Plugin Bridge Methods

The app also exposes home operations to its plugin/mini-app bridge:

- `getHomeListInfo`: returns `homeList`
- `getCurrentHomeInfo`: returns `homeName`, `homeId`, `longitude`,
  `latitude`, `address`, `admin`, `mode`, `role`
- `getDeviceList`: accepts `homeId`, returns `deviceIds`

Evidence:

- `decompiled/com.tuya.smart/assets/thing_uni_plugins/TUNIHomeDataManager.json`

## Device List In A Home

Evidence files:

- `jadx_out/com.tuya.smart/sources/com/thingclips/sdk/home/oo000o.java`
- `decompiled/com.tuya.smart/assets/thing_uni_plugins/TUNIHomeDeviceListManager.json`

The app constructs a home device list mostly by calling several APIs through
the Tuya batch API.

### Batch Wrapper

- API name: `thing.m.api.batch.invoke`
- Version: `1.0`
- Session required: yes
- Main parameters:
  - `apis`
  - `gid`
- Evidence: `oo000o.java:2696-2707`

### Owned Device And Group APIs

| Purpose | API name | Version | Main parameters | Evidence |
| --- | --- | --- | --- | --- |
| Devices in home/group | `m.life.my.group.device.list` | `2.2` | `gid` | `oo000o.java:35-36`, `oo000o.java:506-510` |
| Device groups | `m.life.my.group.device.group.list` | `4.3` | `gid` | `oo000o.java:38`, `oo000o.java:417-421` |
| Device relation list | `m.life.my.group.device.relation.list` | `3.2` | likely `gid` | `oo000o.java:40`, `oo000o.java:126` |
| Mesh list | `m.life.my.group.mesh.list` | `3.1` | likely `gid` | `oo000o.java:37`, `oo000o.java:111` |

### Shared Device And Group APIs

| Purpose | API name | Version | Evidence |
| --- | --- | --- | --- |
| Shared device list | `thing.m.my.shared.device.list` | `3.2` | `oo000o.java:41`, `oo000o.java:411-414` |
| Shared device group list | `thing.m.my.shared.device.group.list` | likely `2.0` through `GwBroadcastMonitorService.mVersion` | `oo000o.java:42`, `oo000o.java:424-427` |

### Local Device List

- API name: `m.life.app.smart.local.device.list`
- Version: `1.1`
- Main parameters:
  - `homeId`
  - `groupType = homeGroup`
- Evidence: `oo000o.java:57`, `oo000o.java:404-408`

### Device Metadata Enrichment

| Purpose | API name | Version | Main parameters | Evidence |
| --- | --- | --- | --- | --- |
| Product UI batch info | `thing.m.product.ui.info.batch.get` | `1.0` | product/device IDs depending on caller | `oo000o.java:56`, `oo000o.java:2650` |
| Device reference info | `m.life.device.ref.info.my.list` | `7.2` | likely `gid`/device refs | `oo000o.java:33`, `oo000o.java:2681` |
| Product reference list | `thing.m.device.product.ref.list` | `1.0` | `gid`, optional `productIds` | `oo000o.java:2711-2719` |
| Device business properties | `thing.m.device.biz.prop.list` | `1.0` | `gid` | `oo000o.java:2722-2728` |

### Device Plugin Bridge Methods

The app exposes these bridge methods in
`TUNIHomeDeviceListManager.json`:

- `getDeviceIdList`
- `getDeviceIdsInRoom`
- `getDeviceIdListWithDevId`
- `getShareDeviceIdList`
- `getLocalDeviceIdList`
- `getGroupIdList`
- `getGroupIdsInRoom`
- `getShareGroupIdList`
- `switchDeviceRoom`

Common input object fields:

- `ownerId`
- `roomId`
- `devId`

Common return fields:

- `devIds`
- `groupIds`
- `roomDatas`

## Inferred Read Flow

A practical read-only flow inferred from the Android app:

1. Request a login token with `thing.m.user.username.token.get`.
2. Login with either:
   - `thing.m.user.mobile.passwd.login`
   - `thing.m.user.email.password.login`
3. Get homes with `m.life.home.space.list`.
4. For a selected `homeId`/`gid`, optionally get details with
   `m.life.location.get`.
5. For devices in the home, call `thing.m.api.batch.invoke` using `gid` and
   nested API calls such as:
   - `m.life.my.group.device.list`
   - `m.life.my.group.device.group.list`
   - `m.life.my.group.device.relation.list`
   - `thing.m.my.shared.device.list`
   - `thing.m.my.shared.device.group.list`
6. Optionally enrich devices with product/ref/property APIs such as:
   - `thing.m.product.ui.info.batch.get`
   - `m.life.device.ref.info.my.list`
   - `thing.m.device.product.ref.list`
   - `thing.m.device.biz.prop.list`

## Notes For Home Assistant Work

- The names above are internal Tuya/Thing mobile APIs, not the public Tuya
  Cloud OpenAPI paths.
- Request signing, session cookies/tokens, region selection, and domain routing
  are handled by the Android SDK and still need separate implementation work.
- The APK/decompiled source should not be committed to this repository. Keep
  this repository to derived notes, tooling, and clean integration code.

## MITM Patch Status

Test device: Samsung S21 Ultra, package `com.tuya.smart`, Tuya Smart
`7.8.6` / versionCode `840`.

Installed patched build in the local reverse-engineering workspace:

- Base APK: `build/signed/com.tuya.smart.mitm13.base.apk`
- Arm64 split: `build/signed/split_config.arm64_v8a.mitm11killfunc.apk`
- Signing key fingerprint used for the patched app:
  `AC:F3:3A:AD:A7:F6:1C:85:CC:B4:4A:8C:FA:E8:AF:A3:73:1A:B3:B8:02:9D:B4:97:8C:BA:B2:64:B4:55:D9:54`

Patch summary:

- Added user CA trust to `force_https_config_international.xml`.
- Bundled the local mitmproxy CA as `@raw/mitmproxy_ca` and added it to the
  same trust anchors. This made Tuya app traffic decryptable without installing
  a user CA certificate into Android settings.
- Disabled OkHttp `CertificatePinner.check(...)` methods.
- Patched `SecretToolUtil`, `Dead`, and related Java exit/kill paths that show
  the "not official" signature guard.
- Added the patched signing certificate fingerprint to app config strings.
- Patched `ResignMonitor.b(...)` to report signature check success.
- Patched `JNICLibrary.testSign(...)` to avoid calling the native signature
  check.
- Patched native `libthing_security.so` function at `0x14794` to return early.
  This function delayed and then called `exit(0)`, causing the app to close
  cleanly after launch when re-signed.
- Patched `NetworkErrorTipManager.j(...)` to ignore certificate-error UI type
  `3`, which otherwise shows the "credential setting" certificate/proxy warning.

Runtime notes:

- Screen timeout was set to `600000` ms and `stay_on_while_plugged_in` to `3`
  for longer MITM sessions.
- Android global proxy was set to `192.168.2.2:8080` for HTTP CONNECT MITM.
- A regular HTTP `mitmdump` listener was started on `0.0.0.0:8080`.
- The patched app connected through the proxy to `a1.tuyaus.com:443`, and after
  bundling the mitmproxy CA, `mitmdump` decrypted Tuya HTTPS traffic to
  `https://a1.tuyaus.com/api.json`.
- The patched app stayed alive for more than 45 seconds after launch with the
  `libthing_security.so` direct function patch. A broader PLT patch of
  `exit`/`kill`/`abort` caused a SIGSEGV because the native self-exit path fell
  through into `JNI_OnLoad`.

Live MITM capture:

- Capture file in the reverse-engineering workspace:
  `mitm/tuya-20260627-000323.mitm`.
- Endpoint: `POST https://a1.tuyaus.com/api.json` over HTTP/2.
- Content type: `application/x-www-form-urlencoded`.
- User-Agent: `Thing-UA=APP/Android/7.8.6/SDK/7.8.0`.
- Common request fields observed: `appVersion`, `appRnVersion`, `sign`,
  `channel`, `deviceId`, `chKey`, `osSystem`, `ttid`, `et`, `nd`,
  `sdkVersion`, `platform`, `requestId`, `lang`, `a`, `clientId`, `os`,
  `timeZoneId`, `cp`, `v`, `deviceCoreVersion`, `bizData`, `time`, and
  sometimes encrypted `postData`.
- The patched/re-signed app receives `ILLEGAL_CLIENT_ID` /
  `Invalid client;No access` from Tuya, so the live capture confirms transport
  and request shape but does not yet produce successful business responses.

Live API names observed during launch, privacy acceptance, and guest flow:

| API name | Version |
| --- | --- |
| `smartlife.m.app.ad.list` | `2.0` |
| `smartlife.m.language.update` | `2.0` |
| `smartlife.m.app.version.upgrade` | `4.0` |
| `smartlife.m.pull.config.data.for.app` | `1.0` |
| `smartlife.m.user.mobile.sendcode.whitelist` | `2.0` |
| `smartlife.m.app.smart.privacy.setting` | `1.0` |
| `smartlife.m.country.list.international` | `1.0` |
| `m.life.country.list.international` | `1.0` |
| `smartlife.p.time.get` | `1.0` |
| `m.life.app.dynamic.config.get` | `1.0` |
| `smartlife.m.miniprogram.kit.whitelist.query` | `1.0` |
| `smartlife.m.user.guest.register` | `1.0` |

## Accepted Client Identity Capture

The `ILLEGAL_CLIENT_ID` blocker was traced to native request signing in
`libthing_security.so`. Static Java-side signature patches were not enough:
the native `ThingNetworkSecurity.doCommandNative(context, 0, appSecret,
appId, ...)` initialization path still read the re-signed APK certificate and
derived the wrong channel key/signature state.

Successful runtime patch:

- Injected Frida Gadget into the patched base APK.
- Hooked native `libthing_security.so` helper at offset `0x16f88`, which builds
  the certificate fingerprint string used by the native signing state.
- Forced that helper to return the original Tuya certificate SHA-256:
  `93:21:9F:C2:73:E2:20:0F:4A:DE:E5:F7:19:1D:C6:56:BA:2A:2D:7B:2F:F5:D2:4C:D5:5C:4B:61:55:00:1E:40`.
- Also hooked Java `PackageManager.getPackageInfo(...)` to return an
  `android.content.pm.Signature` built from the original Tuya X.509 cert bytes.

Observed effect:

- Before native spoof: `chKey=4d2696db`, all business calls returned
  `ILLEGAL_CLIENT_ID`.
- After native spoof: `chKey=3f7060ea`, `smartlife.m.user.guest.register`
  returned encrypted `result` payload instead of `ILLEGAL_CLIENT_ID`.
- After guest registration the app switched region/domain from
  `a1.tuyaus.com` to `a1-sg.iotbing.com`.

Successful capture:

- Capture file in the reverse-engineering workspace:
  `mitm/tuya-20260627-083438-mitm19-native-spoof-fixed3.mitm`.
- Patched base used for this run:
  `build/signed/com.tuya.smart.mitm15.gadget.base.apk`.
- Split used for this run:
  `build/signed/split_config.arm64_v8a.mitm11killfunc.apk`.
- The capture used guest registration only. No user account login was needed.

Additional live API names observed after accepted guest registration:

| API name | Notes |
| --- | --- |
| `m.life.home.space.list` | Home/space list, observed repeatedly after guest session setup. |
| `smartlife.m.api.batch.invoke` | Batch wrapper used for post-login/home data refresh. |
| `m.life.app.smart.local.device.list` | Local device discovery/list request. |
| `m.energy.home.device.list` | Energy/home device list request. |
| `smartlife.m.device.ref.info.list` | Device reference info enrichment. |
| `smartlife.m.device.sig.mesh.list` | SIG mesh list. |
| `smartlife.m.device.sig.mesh.create` | SIG mesh create/init path during guest setup. |
| `m.thing.ble.mesh.create` | BLE mesh create/init path. |
| `smartlife.m.device.allow.maxNum` | Device count/limit metadata. |
| `smartlife.m.device.latest.product.warn.info` | Device/product warning metadata. |
| `m.life.app.home.device.hide.list` | Hidden-device list for home page. |
| `m.life.app.home.page.card.list` | Home page card/device presentation data. |
| `m.life.app.homepage.card.init.sync` | Home page card initialization sync. |
| `m.life.app.smart.smart.get.card.info` | Smart card data for app home. |
| `smartlife.m.location.add_default` | Default location/home setup. |
| `smartlife.m.location.extend.list` | Extended location/home metadata. |
| `smartlife.m.user.info.get` | Current guest user profile. |
| `smartlife.m.user.properties.get` | User property bag. |
| `smartlife.m.token.get` | Session/token refresh path. |

## Successful Account Login Capture

After the native client-identity spoof was applied, account login was also
validated through the patched Android app and MITM proxy.

Capture file in the reverse-engineering workspace:

- `mitm/tuya-20260627-083834-account-login.mitm`

Runtime result:

- Login succeeded for a real account through the app UI.
- The app loaded the Home tab after login.
- Current home shown in the UI: `Kiara`.
- Room tabs visible in the UI included `Favorites`, `Phòng khách`, and
  `Master`.
- Device cards visible in `Phòng khách` included:
  - `backend`, switch type (`Công tắc`), status off (`Tắt`), function `RESET`.
  - `cks`, function `RESET`.

Live account-login API sequence observed:

| API name | Version | Status | Notes |
| --- | --- | --- | --- |
| `smartlife.m.user.username.token.get` | `2.0` | `200` | Token preflight before password login. |
| `smartlife.m.user.email.password.login` | `3.0` | `200` | Email/password login request. |
| `m.life.home.space.list` | `1.0` | `200` | Home/space list after login. |
| `smartlife.m.api.batch.invoke` | `1.0` | `200` | Home page/device batch refresh. Carries encrypted `postData`; includes `gid`. |
| `m.life.app.smart.local.device.list` | `1.1` | `200` | Local/home device list request. Carries encrypted `postData`. |
| `m.energy.home.device.list` | `3.0` | `200` | Energy/home device list request. Carries encrypted `postData`. |
| `m.life.app.home.device.hide.list` | `1.0` | `200` | Hidden-device list for the home page. |
| `smartlife.m.token.get` | `1.0` | `200` | Token/session refresh path after login. |

Common live request envelope fields for the account-login session:

- Endpoint: `POST https://a1.tuyaus.com/api.json`.
- Content type: `application/x-www-form-urlencoded`.
- Common fields: `a`, `v`, `appVersion`, `appRnVersion`, `bizData`,
  `chKey`, `channel`, `clientId`, `cp`, `deviceCoreVersion`, `deviceId`,
  `et`, `lang`, `nd`, `os`, `osSystem`, `platform`, `requestId`,
  `sdkVersion`, `sid`, `sign`, `time`, `timeZoneId`, `ttid`, and optional
  encrypted `postData`.
- Accepted-client `chKey` remained `3f7060ea`.
- The account home identifier observed in batch calls was `gid=92258848`.

No plaintext password, session token, cookie, or decrypted account response
payload is stored in this repository.

## Request Signing And Response Decryption

Request signing is split between Java request assembly and native crypto:

1. `ThingApiParams.getRequestBody()` builds request parameters and encrypted
   body fields.
2. `ThingApiSignManager.generateSignatureSdk(...)` sorts selected keys and
   builds the canonical string.
3. If `postData` is present, the app signs the swapped MD5 of encrypted
   `postData`, not raw `postData`.
4. The canonical string is passed to
   `ThingNetworkSecurity.doCommandNative(context, 1, signInputBytes, null, d)`.
5. Native `libthing_security.so` returns the final request `sign`.

Canonical sign keys observed in `ThingApiSignManager`:

- `a`, `v`, `lat`, `lon`, `lang`, `deviceId`, `appVersion`, `ttid`, `h5`,
  `h5Token`, `os`, `clientId`, `postData`, `time`, `requestId`, `et`, `n4h5`,
  `sid`, `chKey`, `sp`.

Canonical input format:

```text
key=value||key=value||...
```

`postData` hash transform:

```text
swappedMd5 = md5[8:16] + md5[0:8] + md5[24:32] + md5[16:24]
```

Native request-signing result from `libthing_security.so`:

```text
sign = lower_hex(HMAC-SHA256(nativeSigningKey, canonicalSignInputUtf8))
```

Static reverse notes:

- `libthing_security.so` JNI table registers
  `doCommandNative(Context, int, byte[], byte[], boolean)` at `0x14938`.
- `doCommandNative(command=1)` branches at `0x14f00`, reads the canonical
  sign input byte array, loads the native signing key from a libc++ string at
  global offset `0x3ab60`, and converts the 32-byte digest to lowercase hex in
  the loop around `0x1544c`.
- The digest routine called from command `1` is at `0x18458`. Its setup matches
  HMAC: key normalization, `0x36` ipad, `0x5c` opad, inner hash, and outer hash.
- The descriptor selector at `0x181bc(6)` selects the SHA-256 descriptor
  (`0x20` byte output). `0x181bc(5)` selects SHA-224 and is not the final
  request-sign path.
- The native signing key is initialized by `doCommandNative(command=0)`, which
  uses app config bytes plus the certificate/client identity path and calls
  `read_keys_from_content` from `libthing_security_algorithm.so`.

Brute-force/guess check against a captured accepted request:

- Plain `sha256(canonicalInput)`, `md5(canonicalInput)`, and HMAC-SHA256 with
  visible request fields such as `chKey`, `clientId`, `deviceId`, `sid`,
  `ttid`, concatenations of those fields, and the known certificate fingerprint
  did not reproduce the captured `sign`.
- This confirms the final request key is not directly one of the visible
  envelope fields. It must be obtained by reproducing command `0` key
  derivation or dumping the initialized native string in-process.
- Runtime Frida verification on the connected Android device confirmed
  `match=true` for command `1`: native output matched HMAC-SHA256 recomputed
  from the initialized native key and the same canonical input. The key bytes
  were not stored in this repository.

Response decryption for normal `et=3` API calls:

1. Parse encrypted response as `BusinessEncryptResponse` containing `result`,
   `t`, and `sign`.
2. Derive the per-request AES key with
   `ThingNetworkSecurity.getEncryptoKey(requestId, ecodeOrNull)`.
3. Verify response signature:

```text
md5("result=" + result + "||t=" + t + "||" + aesKeyAsUtf8)
```

4. Base64-decode `result`.
5. Treat the first 12 bytes as AES-GCM nonce and the final 16 bytes as GCM tag.
6. AES-GCM decrypt with the key from step 2.
7. Gunzip the plaintext if it is gzip-compressed.
8. Decode as UTF-8 JSON.

The remaining native dependencies for a fully standalone implementation are
`getEncryptoKey(...)` and command `0` native signing-key derivation. Command `1`
final request signing is now identified as HMAC-SHA256 once the native signing
key is known.

Tooling added in this repository:

- `tools/frida_tuya_network_crypto_dump.js`: hooks native sign, request
  encryption key derivation, encrypted request plaintext, and decrypted response
  plaintext.
- `tools/frida_tuya_sign_key_probe.js`: reads the initialized native signing
  key string in-process, logs only key length/hash by default, and verifies that
  command `1` output equals HMAC-SHA256 over the canonical input.
- `tools/tuya_mobile_crypto.js`: reproduces Java-side sign input formatting,
  swapped `postData` MD5, final HMAC-SHA256 request signing when the native key
  is available, and AES-GCM response decryption when a request key is available.
- `tools/tuya_mobile_crypto.py`: Python helper for Java-side sign input and
  swapped `postData` MD5, plus final HMAC-SHA256 request signing when the native
  key is available.

Plaintext confirmed with the Frida hook:

- `smartlife.m.api.batch.invoke` returned room data for home `92258848`:
  `Phòng khách`, `Master`, `Liên`, `Đang`, `WC`, and `Bếp`.
- `m.energy.home.device.list` v3.0 returned these device names:
  `cks`, `backend`, `rọi giường master 2`, `rọi giường master 1`,
  `rọi gương master`, `rọi tab master`, and `hành lang`.
- `m.life.app.home.page.card.list` v2.9 returned home card entries for
  `backend` and `cks`.

Current practical read API map:

1. Login/guest setup:
   - `smartlife.m.user.guest.register` v1.0 for guest mode.
   - Real account login was confirmed live with
     `smartlife.m.user.username.token.get` v2.0 followed by
     `smartlife.m.user.email.password.login` v3.0.
   - Static account-login findings above also identify the older/internal
     `thing.m.*` method names used by the SDK wrapper.
2. List homes:
   - `m.life.home.space.list`.
3. Refresh/list home devices:
   - `smartlife.m.api.batch.invoke`, with nested home/device APIs identified
     statically such as `m.life.my.group.device.list`,
     `m.life.my.group.device.group.list`,
     `m.life.my.group.device.relation.list`,
     `thing.m.my.shared.device.list`, and
     `thing.m.my.shared.device.group.list`.
4. Local/enrichment/device metadata:
   - `m.life.app.smart.local.device.list`
   - `m.energy.home.device.list`
   - `smartlife.m.device.ref.info.list`
   - `smartlife.m.device.sig.mesh.list`

Open item:

- `postData` and most accepted responses are still encrypted by the mobile SDK.
  The transport, accepted client identity, endpoint names, versions, and request
  envelope are now captured successfully. The remaining work for a clean
  non-app implementation is reproducing or reusing SDK encryption/decryption and
  command `0` native key derivation outside the patched Android process.
