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

- Base APK: `build/signed/com.tuya.smart.mitm12.base.apk`
- Arm64 split: `build/signed/split_config.arm64_v8a.mitm11killfunc.apk`
- Signing key fingerprint used for the patched app:
  `AC:F3:3A:AD:A7:F6:1C:85:CC:B4:4A:8C:FA:E8:AF:A3:73:1A:B3:B8:02:9D:B4:97:8C:BA:B2:64:B4:55:D9:54`

Patch summary:

- Added user CA trust to `force_https_config_international.xml`.
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
- The patched app stayed alive for more than 45 seconds after launch with the
  `libthing_security.so` direct function patch. A broader PLT patch of
  `exit`/`kill`/`abort` caused a SIGSEGV because the native self-exit path fell
  through into `JNI_OnLoad`.

Open item:

- The app can reach the welcome/login UI under the patched build. The tested
  guest/login-navigation screens did not yet produce useful Tuya API flows in
  `mitmdump`; more interaction or a logged-in session may be needed to capture
  live signed requests. Static API names and versions above remain the current
  reliable source for login, home list, and device list behavior.
