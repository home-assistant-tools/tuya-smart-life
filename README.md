# Tuya Smart Life Local

Custom integration cho Home Assistant để đăng nhập Smart Life/Tuya Smart bằng
email hoặc số điện thoại và password, lấy danh sách thiết bị từ mobile API, sau
đó điều khiển thiết bị trực tiếp trong LAN bằng local key.

Integration này không cần tạo Tuya IoT Cloud project, không cần nhập `app_id`,
`app_secret`, certificate fingerprint hay native signing key.

## Tính năng

- Đăng nhập bằng email hoặc số điện thoại và password của Smart Life/Tuya Smart.
- Cho chọn một hoặc nhiều nhà khi cấu hình.
- Lấy danh sách thiết bị, local key, hub/child topology, MAC/UUID và DPS ban đầu
  từ Tuya mobile API.
- Điều khiển local bằng TinyTuya qua IP LAN, không dùng cloud API để bật/tắt.
- Theo dõi UDP broadcast và quét LAN định kỳ để cập nhật IP khi thiết bị đổi IP.
- Tự loại bỏ IP public/WAN mà mobile API trả về, chỉ dùng IP local/private cho
  lệnh local.
- Tạo switch entity cho các nút/gang điều khiển lấy từ `dataPointInfo.dps`.
  Nếu API trả tên trong `dataPointInfo.dpName` thì dùng tên đó; nếu không sẽ
  fallback thành `Button <dp_id>`.
- Tạo fan entity cho thiết bị quạt đã nhận diện được, ví dụ quạt có DP nguồn
  và DP tốc độ riêng.
- Tạo binary sensor chẩn đoán `Online` cho hub để hub vẫn hiện trong danh sách
  thiết bị của Home Assistant ngay cả khi hub không có nút điều khiển trực tiếp.
- Tự dọn entity/device cũ khi bạn đổi danh sách nhà được chọn.

## Yêu cầu

- Home Assistant đã cài HACS.
- Home Assistant phải nằm cùng LAN với thiết bị muốn điều khiển local.
- Tài khoản Smart Life/Tuya Smart đang quản lý các thiết bị đó.
- Thiết bị cần có local key trong mobile API và mở được local Tuya protocol.

Lưu ý quan trọng: nếu một nhà trong Smart Life nằm ở LAN khác, Home Assistant
không thể điều khiển local các thiết bị của nhà đó. Chỉ chọn các nhà mà HA có
đường mạng trực tiếp tới thiết bị.

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
5. Sau khi login thành công, chọn nhà cần đồng bộ.
6. Bấm submit và đợi HA tạo thiết bị/entity.

Nếu đăng nhập bằng số điện thoại, integration dùng country code mặc định `84`
và tự nhận diện số có dạng `+84...` hoặc `0084...`. Với số Việt Nam, nếu bạn
nhập `09...`, integration cũng sẽ thử biến thể `9...` vì mobile API tách country
code riêng.

Sau khi đổi danh sách nhà trong options, restart hoặc reload integration để
registry được dọn và load lại đúng thiết bị.

## Cách Điều Khiển Local Hoạt Động

Integration dùng cloud/mobile API chỉ cho phần metadata:

- login
- danh sách nhà
- danh sách thiết bị
- local key
- hub/child relationship
- DPS ban đầu

Khi bạn bật/tắt một switch trong Home Assistant, lệnh đi theo đường local:

```text
Home Assistant -> IP LAN của thiết bị/hub -> TinyTuya -> Tuya local protocol
```

Với thiết bị con sau hub, lệnh được gửi qua hub cha bằng `parentDevId` và
`node_id`/`cid` khi Tuya trả topology đầy đủ.

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

- Kiểm tra HA và thiết bị có cùng LAN không.
- Nếu chạy HA trong Docker/TrueNAS, nên dùng network mode có thể nhận broadcast
  LAN. Integration cần nghe UDP `6666`, `6667`, `6699`, `7000` và kết nối TCP
  local tới thiết bị.
- Nếu mobile API trả IP public/WAN, integration sẽ bỏ qua IP đó và chờ broadcast
  hoặc LAN scan tìm IP private.
- Một số thiết bị có thể trả local key/version không khớp; khi đó TinyTuya sẽ
  báo lỗi kiểu `Check device key or version`.

### Chọn nhầm nhà ở LAN khác

Vào options của integration và bỏ nhà đó ra khỏi danh sách chọn. Integration sẽ
dọn các entity/device cũ sau khi reload/restart.

## Tài Liệu Kỹ Thuật

README này dành cho cài đặt và sử dụng integration. Phần reverse engineering,
MITM, API mobile, signing và crypto nằm trong tài liệu riêng:

- [Reverse engineering and MITM notes](docs/reverse-engineering.md)
- [Tuya Smart Android API findings](docs/tuya-smart-android-api-findings.md)

APK và source decompile không được commit vào repository này; chỉ lưu lại notes,
tooling và integration.
