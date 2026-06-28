# Tuya Smart Life Local

Custom integration cho Home Assistant để đăng nhập Smart Life/Tuya Smart bằng
email hoặc số điện thoại và password, lấy danh sách thiết bị từ mobile API, sau
đó điều khiển thiết bị trực tiếp trong LAN bằng local key.

Integration này không cần tạo Tuya IoT Cloud project, không cần nhập `app_id`,
`app_secret`, certificate fingerprint hay native signing key.

## Tính năng

- Đăng nhập bằng email hoặc số điện thoại và password của Smart Life/Tuya Smart.
- Cho chọn nhà cần đồng bộ; có thể bỏ trống để tạm không load thiết bị nào.
- Lấy danh sách thiết bị, local key, hub/child topology, MAC/UUID và DPS ban đầu
  từ Tuya mobile API.
- Điều khiển local bằng TinyTuya qua IP LAN, không dùng cloud API để bật/tắt.
- Giữ kết nối TCP local tới thiết bị/hub để nhận DPS update realtime. Stream sẽ
  tự khởi động khi UDP broadcast/scan tìm được IP LAN, sau đó refresh/sync một
  lần rồi nghe push DPS. Lệnh điều khiển cũng ưu tiên gửi qua socket stream này
  để tránh thiết bị từ chối kết nối LAN thứ hai; integration không poll trạng
  thái local định kỳ.
- Theo dõi UDP broadcast và quét LAN định kỳ để cập nhật IP/protocol version khi
  thiết bị hoặc hub đổi thông tin LAN.
- Tự loại bỏ IP public/WAN mà mobile API trả về, chỉ dùng IP local/private cho
  lệnh local.
- Tạo switch entity cho các nút/gang điều khiển lấy từ `dataPointInfo.dps`.
  Nếu API trả tên trong `dataPointInfo.dpName` thì dùng tên đó; nếu không sẽ
  fallback thành `Button <dp_id>`.
- Tạo fan entity cho thiết bị quạt đã nhận diện được, ví dụ quạt có DP nguồn
  và DP tốc độ riêng.
- Tạo binary sensor chẩn đoán `Online` cho hub để hub vẫn hiện trong danh sách
  thiết bị của Home Assistant ngay cả khi hub không có nút điều khiển trực tiếp.
- Tạo button entity cho remote hồng ngoại (IR) khi Tuya mobile API trả được
  action DPS của remote ảo.
- Tự nhận diện một số remote IR điều hoà/AC và tạo climate entity thử nghiệm.
  Lệnh climate được gửi local qua IR hub; trạng thái là optimistic vì IR không
  phản hồi trạng thái thật từ điều hoà.
- Tự dọn entity/device cũ khi bạn đổi danh sách nhà được chọn.

## Yêu cầu

- Home Assistant đã cài HACS.
- Home Assistant phải nằm cùng lớp mạng/broadcast domain với thiết bị muốn điều
  khiển local. Integration cần nhận UDP broadcast của Tuya để tự tìm IP/version
  và mở TCP stream realtime.
- Tài khoản Smart Life/Tuya Smart đang quản lý các thiết bị đó.
- Thiết bị cần có local key trong mobile API và mở được local Tuya protocol.
- Với remote IR, IR hub phải cùng LAN với Home Assistant. Remote con như TV,
  điều hoà, quạt IR là thiết bị ảo sau hub nên lệnh thực tế vẫn đi qua hub.

Lưu ý quan trọng: nếu một nhà trong Smart Life nằm ở LAN/subnet/VLAN khác,
Home Assistant chưa thể tự kết nối local các thiết bị của nhà đó. Kết nối
xuyên mạng hiện chưa được hỗ trợ vì UDP broadcast/discovery không đi qua router
theo mặc định; chỉ route TCP/ping được tới thiết bị là chưa đủ cho cơ chế tự
động hiện tại. Chỉ chọn các nhà mà HA nằm chung lớp mạng với thiết bị/hub.

## Cài Đặt Qua HACS

1. Mở HACS trong Home Assistant.
2. Vào **Integrations**.
3. Bấm menu **...** góc phải, chọn **Custom repositories**.
4. Nhập repository URL:

   ```text
   https://github.com/home-assistant-tools/tuya-smart-life
   ```

5. Chọn category/type là **Integration**.
6. Bấm **Add**.
7. Tìm **Tuya Smart Life Local** trong HACS và bấm **Download**.
8. Restart Home Assistant.

## Thiết Lập Integration

1. Vào **Settings -> Devices & services**.
2. Bấm **Add integration**.
3. Tìm **Tuya Smart Life Local**.
4. Nhập email hoặc số điện thoại và password Smart Life/Tuya Smart.
5. Sau khi login thành công, chọn nhà cần đồng bộ hoặc để trống nếu chưa muốn
   load thiết bị nào.
6. Bấm submit và đợi HA tạo thiết bị/entity.

Nếu đăng nhập bằng số điện thoại, integration dùng country code mặc định `84`
và tự nhận diện số có dạng `+84...` hoặc `0084...`. Với số Việt Nam, nếu bạn
nhập `09...`, integration cũng sẽ thử biến thể `9...` vì mobile API tách country
code riêng.

Sau khi đổi danh sách nhà trong options, integration sẽ reload để registry được
dọn và load lại đúng thiết bị. Nếu đang dùng bản cũ hơn `0.1.33`, hãy reload
hoặc restart Home Assistant sau khi cập nhật.

## Cách Điều Khiển Local Hoạt Động

Integration dùng cloud/mobile API chỉ cho phần metadata:

- login
- danh sách nhà
- danh sách thiết bị
- local key
- hub/child relationship
- DPS ban đầu
- IR remote action metadata

Khi bạn bật/tắt một switch trong Home Assistant, lệnh đi theo đường local:

```text
Home Assistant -> IP LAN của thiết bị/hub -> TinyTuya -> Tuya local protocol
```

Với thiết bị con sau hub, lệnh được gửi qua hub cha bằng `parentDevId` và
`node_id`/`cid` khi Tuya trả topology đầy đủ.

Với thiết bị Zigbee/BLE sau hub, integration cũng đọc UDP broadcast của hub để
cập nhật protocol version cho từng child khi broadcast có `cid`/`nodeId`. Điều
này giúp tránh lỗi local kiểu `Check device key or version` do child dùng
protocol khác hub.

Với remote IR, integration lấy action từ scene/action API của app Tuya rồi gửi
raw DPS trực tiếp xuống IR hub trong LAN. Không dùng Tuya IoT Cloud project và
không gọi OpenAPI cloud để bấm nút.

## Nút Công Tắc Và DPS

Tuya mô tả các nút/gang của công tắc bằng DPS:

- `dataPointInfo.dps`: giá trị hiện tại của từng DP.
- `dataPointInfo.dpName`: tên từng DP nếu app/cloud có trả.

Integration chỉ expose các DP boolean có khả năng là nút/gang điều khiển. Các
DP phụ như indicator, backlight, countdown hoặc trạng thái phụ sẽ bị bỏ qua khi
có thể nhận diện được. Với các thiết bị chưa trả `dpName`, entity sẽ có tên
fallback như `Button 1`, `Button 2`.

Một số thiết bị dùng DP boolean `1` làm nguồn cho domain khác, ví dụ quạt. Khi
thiết bị được nhận diện là quạt, DP nguồn sẽ được expose là `fan` thay vì
`switch`; các DP phụ như đèn của quạt vẫn có thể được expose là switch riêng nếu
Tuya trả về DP boolean tương ứng.

## Thiết Bị IR

Tuya quản lý thiết bị IR theo hai lớp:

- IR hub thật: có local key, IP LAN và nhận lệnh local.
- Remote IR ảo: TV, điều hoà, quạt... nằm sau IR hub và có `remote_id`.

Integration gọi `thing.m.linkage.dev.list` và
`thing.m.linkage.function.list` để lấy danh sách remote/action mà app Tuya dùng
khi tạo automation. Integration cũng thử đọc scene rule qua
`thing.m.linkage.rule.query`/`thing.m.linkage.rule.detail.find` để nhập các
payload IR mà app lưu trong scene. Nếu action có raw DPS hợp lệ, HA sẽ tạo
button tương ứng và publish raw DPS đó trực tiếp xuống IR hub local. `remote_id`
của thiết bị ảo chỉ dùng để đặt tên/entity/report metadata, không được đóng gói
như `cid` trong frame local gửi xuống hub.

Nếu remote được nhận diện là điều hoà/AC và action đủ thông tin `power`, `mode`,
`temp` hoặc `wind`, integration sẽ tạo thêm climate entity. Climate IR là điều
khiển một chiều nên trạng thái trong HA là trạng thái lệnh vừa gửi, không phải
trạng thái đọc ngược từ điều hoà.

Để debug dữ liệu IR ngoài Home Assistant:

```bash
python3 tools/tuya_mobile_login.py --action ir --home-id <home-id>
python3 tools/tuya_mobile_login.py --action ir --home-id <home-id> --json
```

Script sẽ redacted session/key/token mặc định và chỉ in remote, category,
hub, function và `actionDps` tìm được.

## Cập Nhật

HACS sẽ thấy các phiên bản GitHub release của repository này. Để cập nhật:

1. Mở HACS.
2. Vào repository **Tuya Smart Life Local**.
3. Bấm **Update information** nếu chưa thấy version mới.
4. Bấm **Download/Redownload** version mới.
5. Restart Home Assistant.

## Troubleshooting

### Login báo `cannot_connect`

- Kiểm tra email/số điện thoại/password.
- Kiểm tra internet của Home Assistant.
- Nếu Smart Life yêu cầu xác thực phụ/MFA, login bằng script có thể chưa xử lý
  được luồng đó.

### Entity unavailable hoặc không điều khiển được

- Kiểm tra HA và thiết bị có cùng lớp mạng/broadcast domain không.
- Nếu chạy HA trong Docker/TrueNAS, nên dùng network mode có thể nhận broadcast
  LAN. Integration cần nghe UDP `6666`, `6667`, `6699`, `7000` và kết nối TCP
  local tới thiết bị.
- Kết nối xuyên subnet/VLAN/WAN chưa được hỗ trợ tự động. UDP broadcast của Tuya
  không đi qua router, nên HA có thể ping/TCP tới IP thiết bị nhưng vẫn không tự
  học được IP/protocol version để duy trì local realtime ổn định.
- Nếu mobile API trả IP public/WAN, integration sẽ bỏ qua IP đó và chờ broadcast
  hoặc LAN scan tìm IP private.
- Một số thiết bị có thể trả local key/version không khớp; khi đó TinyTuya sẽ
  báo lỗi kiểu `Check device key or version`. Integration sẽ ưu tiên version
  học được từ UDP broadcast và thử fallback protocol cho thiết bị con sau hub.

### Remote IR không hiện hoặc climate IR không hiện

- Kiểm tra nhà được chọn có IR hub cùng LAN với Home Assistant không.
- Chạy `tools/tuya_mobile_login.py --action ir --home-id <home-id>` để xem
  mobile API có trả remote/action không.
- Nếu Tuya chỉ trả phím raw rời, integration sẽ tạo button thay vì climate.
- Nếu API và scene đều không trả `actionDps`/`executorProperty`, chưa thể bấm
  local bằng dữ liệu hiện có. Khi đó có thể cần tạo scene trong app Tuya cho
  nút IR cần dùng để app lưu payload tương ứng.

### Chọn nhầm nhà ở LAN khác

Vào options của integration và bỏ nhà đó ra khỏi danh sách chọn. Có thể bỏ chọn
tất cả nhà nếu muốn integration chỉ giữ login/list homes mà không load thiết bị.
Integration sẽ dọn các entity/device cũ sau khi reload/restart.

## Tài Liệu Kỹ Thuật

README này dành cho cài đặt và sử dụng integration. Phần reverse engineering,
MITM, API mobile, signing và crypto nằm trong tài liệu riêng:

- [Reverse engineering and MITM notes](docs/reverse-engineering.md)
- [Tuya Smart Android API findings](docs/tuya-smart-android-api-findings.md)

APK và source decompile không được commit vào repository này; chỉ lưu lại notes,
tooling và integration.
