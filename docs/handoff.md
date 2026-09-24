# iPhone HID Control — Tài liệu bàn giao cho Claude Code

> **Mục tiêu:** điều khiển iPhone (xem màn hình + chạm/vuốt/gõ) từ host Linux **chỉ bằng phần cứng ngoại vi**. Không dùng Developer Mode, không jailbreak, không cài app lên iPhone.
> **Trạng thái:** đã nghiên cứu tính khả thi và chốt kiến trúc. Chưa có code. Phần cứng chưa mua đủ.
> **Ngày:** 25/09/2026
> **Bối cảnh:** một kỹ sư nội bộ báo đã điều khiển iPhone thành công bằng IC HID cắm dây. Tài liệu này hệ thống hoá hướng đó để làm thành sản phẩm, theo kiểu các board "phone farm" Trung Quốc.

---

## 0. Hướng dẫn cho Claude Code

- Đọc hết tài liệu trước khi làm. Mục gắn **[XÁC MINH]** là thông tin chưa chắc chắn. Phải kiểm chứng (bằng datasheet, nguồn chính thức, hoặc test phần cứng) trước khi viết code phụ thuộc vào nó.
- Claude Code **không có phần cứng**. Quy trình làm việc:
  1. Viết code, fake device và unit test.
  2. Bàn giao script test kèm hướng dẫn chạy.
  3. Người dùng chạy trên phần cứng thật rồi gửi log.
  4. Sửa theo log.
- Mỗi giai đoạn có tiêu chí nghiệm thu (mục 9). Không chuyển sang giai đoạn sau khi GĐ 0 chưa xác nhận các rủi ro lớn.
- Người dùng giao tiếp bằng **tiếng Việt**. Code, tên biến và commit message viết bằng tiếng Anh.

---

## 1. Mục tiêu và phạm vi

**Mục tiêu**
1. Lấy hình màn hình iPhone realtime về host Linux.
2. Thao tác được: tap, long press, swipe, scroll, gõ phím, Home, App Switcher.
3. Có API (HTTP/WebSocket) và web UI để xem và điều khiển; ghi và phát lại kịch bản.
4. Về sau: nhiều máy trên một host, rồi làm board nhúng gắn theo từng máy.

**Thiết bị đích**
- iPhone cổng USB-C (iPhone 15 trở lên).
- iPhone cổng Lightning: cần iOS 13+ để có con trỏ chuột, tức iPhone 6s trở lên.

**Host:** Linux (Raspberry Pi 4/5 hoặc mini PC x86).

**Ngoài phạm vi (không làm được với hướng này):**
- Vượt passcode hoặc Face ID.
- Cài hoặc ký app.
- Đọc dữ liệu hệ thống.
- Lấy cây UI (accessibility tree).

Hệ thống chỉ là một "ngón tay ảo nhìn màn hình".

---

## 2. Ràng buộc

### 2.1 Được dùng
- Thư viện đa dụng: `pyserial`, `opencv-python`, `numpy`, `fastapi`/`uvicorn`, `websockets`, `Pillow`, OCR đa dụng (Tesseract, PaddleOCR...).
- Thư viện đa dụng cho chip CH9329, ví dụ `pych9329-hid`. Được phép vì không chuyên cho iPhone. Tuy vậy nên tự viết driver mỏng bằng `pyserial` để kiểm soát ack và timeout; thư viện kia chỉ dùng để đối chiếu giao thức.
- SDK của hãng chip: ESP-IDF (NimBLE, TinyUSB), Pico SDK.
- Được đọc tài liệu và mã của dự án khác để hiểu. **Không copy** code có license hạn chế (ví dụ Aiden dùng AGPL-3.0).

### 2.2 Không được dùng (công cụ làm sẵn chuyên cho iPhone)
- `libimobiledevice` và các công cụ dựa trên usbmuxd, `pymobiledevice3`, `go-ios`, `tidevice`, `idb`.
- WebDriverAgent, XCUITest, Appium XCUITest driver, `facebook-wda`.
- `quicktime_video_hack` (qvh) và các bản fork.
- SDK của board phone-farm thương mại (iMouse, SOME 3C/iClick...).
- **Vùng xám, phải hỏi người dùng trước:** UxPlay hoặc thư viện AirPlay receiver. Chỉ cần đến nếu đi hướng AirPlay ở GĐ 5.

---

## 3. Kết quả nghiên cứu (nguồn ở mục 12)

### 3.1 Điều khiển
- iOS/iPadOS 13+ nhận chuột và bàn phím ngoài, qua USB hoặc Bluetooth. Trên iPhone, con trỏ chuột **chỉ hoạt động khi bật AssistiveTouch** (theo AbilityNet và Aiden).
- iOS **chỉ nhận chuột tương đối**. Cộng đồng trên Apple Developer Forums thử đổi HID descriptor sang absolute thì con trỏ không di chuyển nữa. Hệ quả: phải điều khiển con trỏ bằng các bước tương đối, reset về góc, và dùng vòng phản hồi.
- Các nút chuột có thể gán hành động trong AssistiveTouch (Home, App Switcher...).
- **Bug đã biết** (Aiden, 07/2026): khi AssistiveTouch bật và iOS thấy cùng lúc bàn phím và chuột, phím tắt có Cmd/Shift/Option đôi khi bị chuyển sang SpringBoard thay vì app đang mở. Aiden né bằng hai profile USB HID có PID khác nhau:
  - Bình thường: bàn phím + chuột.
  - Khi cần chạy phím tắt: re-enumerate thành profile chỉ có bàn phím, chạy xong thì khôi phục.
- **Bug đã được báo cáo** (Apple Forums, iOS 16.5): khi xoay ngang, con trỏ AssistiveTouch bị đảo trục hoặc không đi qua được giữa màn hình. **[XÁC MINH trên iOS hiện tại]**
- Trong Pointer Control có thể đổi kích thước con trỏ, màu viền, độ tương phản, và tắt tự ẩn. Các tuỳ chọn này giúp nhận diện con trỏ bằng thị giác máy.

### 3.2 Lấy hình
- Mọi iPhone 15 trở lên có DisplayPort Alt Mode qua USB-C, mirror tới 4K/60. Có thể ra HDMI qua adapter hoặc hub.
- iPhone Lightning mirror được 1080p qua Lightning Digital AV Adapter.
- iPhone chỉ mirror, không có chế độ màn hình mở rộng.
- Nội dung có DRM (Netflix...) sẽ ra màn đen trên HDMI do HDCP. Chấp nhận được.
- **Đã loại:** giao thức screen capture kiểu QuickTime qua USB, vì hai lý do:
  - iPhone phải đóng vai USB device, xung đột với việc iPhone làm USB host cho IC HID trên cùng một cổng.
  - Công cụ làm sẵn cho giao thức này (qvh) nằm trong danh sách cấm.

### 3.3 Bảo mật iOS 26+ cần biết
- Settings > Privacy & Security > **Wired Accessories** có 4 lựa chọn:
  - Always Ask
  - Ask for New Accessories
  - Automatically Allow When Unlocked (mặc định)
  - Always Allow
- Khi máy đang khoá, phụ kiện có dây không trao đổi được dữ liệu cho tới khi mở khoá và xác nhận. Vì vậy phải giữ máy luôn mở khoá (Auto-Lock: Never).
- Có báo cáo bug ở iOS 26.0.1: mục này bị khoá cứng ở Always Allow trên một số máy.

### 3.4 Dự án và sản phẩm tham khảo

| Tên | Loại | Cách làm | Ghi chú |
|---|---|---|---|
| **Aiden** (AidenAI-IO) | Mã nguồn mở, AGPL | Luckfox Pico Zero (RV1106): HDMI capture + Linux USB HID gadget, nối qua hub USB-C có HDMI và USB | Mới là dev board. Không cần jailbreak hay Developer Mode; iOS cần AssistiveTouch. **Kiến trúc gần nhất với dự án này** |
| **SOME 3C control board** | Thương mại (~38 USD) | Mỗi board điều khiển 1 máy, có bản Lightning và USB-C; API HTTP/WebSocket/Python, OCR, tìm ảnh | Hỗ trợ iPhone 6s+ với iOS 15+. Không công bố cách lấy hình |
| **iMouse XP** | Thương mại (Taobao) | Phần cứng riêng + thư viện Python | Không jailbreak, không cài app |
| **Wormhole (虫洞)** | App thương mại | Mac: cáp + Bluetooth. Windows: AirPlay + Bluetooth HID, PC đóng vai BLE peripheral | Chứng minh cách BLE HID + mirror dùng được |
| **PiKVM** | KVM mã nguồn mở | Có chế độ chuột tương đối; giả lập được chuột/bàn phím Bluetooth (có ví dụ điều khiển iPad) | Tham khảo phần HID gadget |
| **JetKVM** | KVM mã nguồn mở | 1080p60, H.264, trễ 30–60 ms | Tham khảo pipeline video |
| **esp32-kvm** (KMChris) | Mã nguồn mở | PC → serial → ESP32 → BLE HID; chạy được với iOS | Tham khảo firmware BLE |

### 3.5 Giả thuyết chưa kiểm chứng về board "phone farm"
- SOME 3C quảng cáo iPhone lên mạng được qua "cáp mạng OTG". Như vậy board có thể đóng vai card mạng USB (ECM/NCM) cho iPhone.
- Khi đó chỉ một cáp đã vừa mang HID vừa tạo mạng nội bộ. Hình có thể được kéo về qua AirPlay mirroring trong mạng đó, chạy được cả với máy Lightning.
- Aiden cũng khai báo ECM networking trong USB gadget của họ.
- Đây là hướng nghiên cứu cho GĐ 5, và phải được người dùng duyệt vì dính tới AirPlay receiver (xem 2.2).

---

## 4. Kiến trúc

### 4.1 Dòng USB-C (iPhone 15+): một dây cho cả hình và điều khiển
```
                 ┌────────── Hub USB-C (DP Alt Mode + USB + PD passthrough) ──────────┐
iPhone ══USB-C══►│ HDMI  ───────► Capture card HDMI→USB ───► Linux host   (hình)      │
                 │ USB-A ◄─────── CH9329 (HID) ◄── UART/USB-serial ◄── host (điều khiển)│
                 │ PD in ◄─────── Sạc ≥ 20W                                            │
                 └──────────────────────────────────────────────────────────────────────┘
```
iPhone làm USB host (cho hub và IC HID) đồng thời xuất DisplayPort. Aiden dùng đúng topology này.

### 4.2 Dòng Lightning: hình qua dây, điều khiển qua Bluetooth
```
iPhone ══Lightning══► Lightning Digital AV Adapter ──HDMI──► Capture card ──► host  (hình)
iPhone ◄──── Bluetooth LE ──── ESP32 (BLE HID) ◄── UART/USB-CDC ◄── host        (điều khiển)
```
- **Lý do phải tách:** iPhone Lightning chỉ có một cổng, và cổng Lightning trên Digital AV Adapter chỉ dùng để sạc. Vì vậy không cắm được HID chung dây với HDMI. **[XÁC MINH bằng test]**
- **Quyết định thiết kế:** firmware ESP32 **nói đúng giao thức khung của CH9329** (mục 7). Nhờ vậy phần mềm host dùng chung 100% cho cả hai dòng máy.
- Phương án phụ, test riêng ở GĐ 0: Lightning to USB 3 Camera Adapter + CH9329 để có HID có dây. Chỉ dùng được nếu hình lấy bằng đường khác (AirPlay).

### 4.3 Kiến trúc phần mềm
```
┌─ web UI: live view, click-to-tap, ghi/phát kịch bản ─────────────────────┐
├─ api: FastAPI + WebSocket, device registry ──────────────────────────────┤
├─ control: closed-loop pointer, gestures, calibration ────────────────────┤
├─ vision: capture (OpenCV/V4L2), screen rect, pointer detect, (OCR sau) ──┤
├─ input: pointer model (pacer, corner reset), keymap ─────────────────────┤
└─ hid backends: CH9329 (serial) │ ESP32-BLE bridge (cùng giao thức) │ Fake ┘
```

### 4.4 Các hệ toạ độ
- **HID units:** dx, dy ∈ [-127, 127] trên mỗi báo cáo. Quan hệ với khoảng cách trên màn hình không tuyến tính vì có gia tốc con trỏ.
- **Frame px:** toạ độ trong frame capture (ví dụ 1920×1080).
- **Screen rect:** vùng ảnh iPhone bên trong frame. Khi máy dựng dọc sẽ có viền đen hai bên. Phải phát hiện tự động và cập nhật khi xoay màn hình.
- **Norm:** (0..1, 0..1) tính trong screen rect. Đây là hệ toạ độ công khai của API.

---

## 5. Phần cứng (BOM)

| # | Thiết bị | Dùng cho | Ghi chú |
|---|---|---|---|
| 1 | iPhone USB-C (15/16/17) | Dòng USB-C | |
| 2 | iPhone Lightning (6s trở lên, iOS 13+) | Dòng Lightning | |
| 3 | Cáp CH9329 thành phẩm (CH9329 + CH340/CP2102, hai đầu USB-A) | HID có dây | Không cần viết firmware. Baud mặc định 9600 **[XÁC MINH]** |
| 4 | Hub USB-C có HDMI + USB-A + PD passthrough | Dòng USB-C | Nên thử 2–3 mẫu; không phải hub nào cũng chạy với iPhone |
| 5 | Capture card HDMI→USB (ưu tiên chip MS2130, MS2109 rẻ hơn) | Cả hai dòng | Định dạng, độ phân giải và độ trễ của từng chip **[XÁC MINH]** |
| 6 | Apple Lightning Digital AV Adapter (chính hãng) | Dòng Lightning | Hàng nhái hay lỗi |
| 7 | Apple Lightning to USB 3 Camera Adapter | Test GĐ 0 cho Lightning | Tuỳ chọn |
| 8 | ESP32-S3 DevKit | BLE HID cho Lightning; tuỳ chọn thay CH9329 (USB OTG + TinyUSB) | S3 có cả BLE và USB OTG |
| 9 | Raspberry Pi 4/5 hoặc mini PC Linux | Host | Pi bị giới hạn băng thông USB khi cắm nhiều capture card |
| 10 | Sạc PD ≥ 20W | Nuôi iPhone qua hub | |

---

## 6. Cài đặt iPhone (làm tay một lần)

Tên menu có thể khác đôi chút theo phiên bản iOS.

1. **Accessibility > Touch > AssistiveTouch:** bật ON. Tắt "Always Show Menu" nếu nút nổi che nội dung.
2. **AssistiveTouch > Pointer Devices > Devices > [thiết bị] > Customize Additional Buttons:** gán nút phải = Home, nút giữa = App Switcher.
3. **Accessibility > Pointer Control:**
   - Tắt Automatically Hide Pointer.
   - Tăng kích thước con trỏ.
   - Đặt màu viền dễ nhận diện (ví dụ xanh lá).
   - Ghi lại mức Tracking Speed và **không đổi** sau khi đã hiệu chỉnh.
4. **Privacy & Security > Wired Accessories:** chọn Ask for New Accessories hoặc Automatically Allow When Unlocked.
5. **Display & Brightness:** Auto-Lock = Never; Display Zoom = Default.
6. **General > Keyboard > Hardware Keyboard:** layout U.S.; tắt Auto-Capitalization, Auto-Correction và phím tắt dấu ".".
7. **Dòng Lightning:** vào Settings > Bluetooth để ghép với ESP32 (GĐ 1b).

---

## 7. Giao thức CH9329 (tham chiếu)

> **Toàn bộ mục này là [XÁC MINH].** Phải đối chiếu với tài liệu giao thức serial CH9329 của WCH ("CH9329芯片串口通信协议") và với mã của `pych9329-hid`.

### Khung lệnh
```
57 AB | ADDR | CMD | LEN | DATA[LEN] | SUM
SUM = (tổng mọi byte phía trước, gồm cả header) & 0xFF
```
- ADDR mặc định 0x00. UART 8N1, baud mặc định 9600.
- Phản hồi: `CMD | 0x80` là thành công, `CMD | 0xC0` là lỗi. Các lệnh gửi HID phản hồi 1 byte trạng thái.
- Mã trạng thái:

| Mã | Ý nghĩa |
|---|---|
| `00` | OK |
| `E1` | timeout |
| `E2` | sai header |
| `E3` | sai mã lệnh |
| `E4` | sai checksum |
| `E5` | sai tham số |
| `E6` | lỗi thực thi |

### Bảng lệnh

| CMD | Tên | DATA gửi | Phản hồi |
|---|---|---|---|
| 0x01 | GET_INFO | — | 8 byte: version (0x30 = V1.0); trạng thái USB enum (0/1); LED (bit0 Num, bit1 Caps, bit2 Scroll); 5 byte dự trữ |
| 0x02 | SEND_KB_GENERAL_DATA | 8 byte: `modifier 00 k1 k2 k3 k4 k5 k6` | status |
| 0x03 | SEND_KB_MEDIA_DATA | ACPI: `01 xx`; Multimedia: `02 b1 b2 b3` | status |
| 0x04 | SEND_MS_ABS_DATA | 7 byte: `02 btn Xlo Xhi Ylo Yhi wheel`, X/Y trong 0..4095 | status (iOS nhiều khả năng bỏ qua) |
| 0x05 | SEND_MS_REL_DATA | 5 byte: `01 btn dx dy wheel` (int8) | status |
| 0x08 | GET_PARA_CFG | — | 50 byte cấu hình |
| 0x09 | SET_PARA_CFG | 50 byte | status; có hiệu lực sau reset |
| 0x0C | SET_DEFAULT_CFG | — | status |
| 0x0F | RESET | — | status |

- **Modifier bits:** 0x01 LCtrl, 0x02 LShift, 0x04 LAlt (Option), 0x08 LGUI (Command); 0x10–0x80 là các phím tương ứng bên phải.
- **Nút chuột:** bit0 trái, bit1 phải, bit2 giữa.

### Cấu hình 50 byte

| Byte | Nội dung |
|---|---|
| 0 | work mode |
| 1 | serial mode (0x00 protocol, 0x01 ASCII, 0x02 transparent) |
| 2 | address |
| 3–6 | baud (big-endian) |
| 7–8 | reserved |
| 9–10 | packet interval |
| 11–12 | VID |
| 13–14 | PID |
| 15–16 | kb upload interval |
| 17–18 | kb release delay |
| 19 | auto-enter flag |
| 20–27 | enter chars |
| 28–35 | filter strings |
| 36 | USB string enable |
| 37 | kb fast upload |
| 38–49 | reserved |

**Work mode [XÁC MINH]:**
- 0x00: bàn phím (thường + media) + chuột (abs + rel) + custom HID
- 0x01: chỉ bàn phím thường
- 0x02: bàn phím + chuột (?)
- 0x03: chỉ custom HID
- 0x80–0x83: như trên nhưng chọn chế độ bằng chân MODE

Nếu 0x01 đúng là "chỉ bàn phím", có thể mô phỏng cách né bug của Aiden: đổi mode rồi reset chip trước khi gửi phím tắt Cmd. Rủi ro: mỗi lần re-enumerate, iOS có thể hỏi lại quyền phụ kiện.

**An toàn khi ghi cấu hình:**
- Luôn read-modify-write: in dump cấu hình trước, yêu cầu xác nhận rồi mới ghi.
- Phải có công cụ dò baud (9600/19200/38400/57600/115200) để cứu khi đổi baud hỏng.

**Thông lượng:** ở 9600 baud, mỗi khung chuột mất khoảng 11,5 ms, cộng khoảng 7 ms chờ ack, tức khoảng 40–50 báo cáo/giây. Cân nhắc nâng lên 115200 khi driver đã ổn định.

---

## 8. Thiết kế chi tiết

### 8.1 Cấu trúc repo đề xuất
```
iphone-hid/
  pyproject.toml
  ihc/
    hid/        base.py (HidBackend)  ch9329.py  fake.py (pty + mô phỏng con trỏ)  scan.py (dò baud)
    input/      keymap.py  pointer.py  gestures.py
    vision/     capture.py  screen_rect.py  pointer_detect.py
    control/    closed_loop.py  calibrate.py
    device.py   # IPhoneDevice: ghép hid + vision + control
    registry.py # map serial ↔ capture qua /dev/serial/by-path và /dev/v4l/by-path
    api/        server.py
  web/          index.html (+ js)
  tools/        hidtest.py (CLI tương tác)  capture_check.py  calib_page/ (mục 8.6)
  firmware/     esp32_ble_hid/ (ESP-IDF)
  tests/
  docs/         hardware-setup.md  test-logs/
```

### 8.2 HidBackend
```python
class HidBackend(Protocol):
    def info(self) -> dict: ...
    def keyboard(self, modifiers: int, keys: Sequence[int]) -> None: ...
    def media(self, code: int) -> None: ...
    def mouse_rel(self, dx: int, dy: int, buttons: int = 0, wheel: int = 0) -> None: ...
    def close(self) -> None: ...
```
- **CH9329Backend:**
  - Dùng pyserial; hỗ trợ cả chế độ chờ ack và fire-and-forget.
  - Khi chờ phản hồi của lệnh mới, bỏ qua các ack cũ còn tồn trong buffer.
  - Timeout rõ ràng; khi lỗi, ném exception với thông điệp dễ hiểu (sai cổng, sai baud, chip chưa có nguồn...).
- **FakeBackend:**
  - Dùng pty để giả lập chip: trả GET_INFO, cấu hình 50 byte và ack; ghi log sự kiện.
  - Có mô phỏng con trỏ (vị trí, gia tốc giả lập, kẹp biên) để test thuật toán điều khiển mà không cần phần cứng.

### 8.3 Pointer model (open-loop)
- **Pacer:** giữ khoảng cách cố định giữa hai báo cáo (mặc định khoảng 25 ms) và bước cố định (ví dụ 4 đơn vị). Vận tốc không đổi nên quãng đường xấp xỉ tuyến tính theo số bước, bất kể gia tốc con trỏ.
- **Di chuyển từng trục riêng** (X trước, Y sau) để vận tốc trên mỗi trục giống lúc hiệu chỉnh.
- **`reset_corner()`:** gửi N báo cáo (-127, -127) để dồn con trỏ về góc trên-trái. Sau đó vị trí chắc chắn là (0, 0).
- **Tham số hiệu chỉnh** lưu JSON riêng cho từng máy: `step`, `interval`, `px_per_step_x/y`, `reset_reports`. Phải hiệu chỉnh lại khi đổi Tracking Speed.
- **Swipe:** nhấn nút trái, di chuyển, nhả. Tuyệt đối không reset về góc khi đang giữ nút.

### 8.4 Nhận diện con trỏ (vision)
Thứ tự ưu tiên:
1. **Theo màu:** đặt màu viền con trỏ đặc trưng và tăng kích thước, sau đó lọc HSV → tìm blob → lấy tâm.
2. **Frame differencing:** "lắc" con trỏ vài bước đã biết rồi trừ hai frame.
3. **Template matching.**

> **Rủi ro lớn nhất, phải trả lời ở GĐ 0: con trỏ AssistiveTouch có hiện trong tín hiệu HDMI mirror hay không.**
> Nếu **không**: bỏ mục 8.4, chuyển sang dùng trang hiệu chỉnh (8.6) kết hợp open-loop, và xác minh kết quả mỗi lần tap bằng thay đổi của UI.

### 8.5 Closed-loop tap
```
goto_open_loop(target)
lặp tối đa K lần:
    p = detect_pointer()
    e = target - p
    nếu |e| ≤ tol: thoát
    bước = e / gain          # gain ước lượng online bằng EMA
    move(bước)
click()
```
**Mục tiêu:** 95% số lần tap rơi trong bán kính 5 px (frame 1080p); dưới 1,5 giây mỗi lần tap.

### 8.6 Trang hiệu chỉnh qua Safari (ý tưởng, không cần cài app)
- Host chạy một web server trong mạng LAN; mở trang đó bằng Safari trên iPhone.
- Trang bắt sự kiện click/pointer (clientX/Y, kích thước viewport) rồi gửi về host qua WebSocket.
- Nhờ vậy host biết chính xác mỗi click rơi vào đâu, từ đó tự hiệu chỉnh gain và bù sai số.
- **[XÁC MINH]** Safari trên iPhone có phát `mousemove`/`pointermove` khi dùng con trỏ AssistiveTouch không. Nếu có, còn đo được vị trí con trỏ liên tục mà không cần thị giác máy.

### 8.7 Capture
- Dùng OpenCV `VideoCapture(CAP_V4L2)`; thử cả MJPG và YUYV; đặt buffer = 1.
- Một thread đọc liên tục để luôn giữ frame mới nhất.
- Đo độ trễ glass-to-glass: hiển thị đồng hồ trên iPhone rồi so với thời gian của host.
- **Screen rect:** threshold phần viền đen để lấy bounding box; cập nhật khi hình thay đổi (ví dụ khi xoay màn hình).

### 8.8 Bàn phím
- Bảng HID usage cho layout US:

| Phím | Mã |
|---|---|
| a–z | 0x04–0x1D |
| 1–9 | 0x1E–0x26 |
| 0 | 0x27 |
| Enter | 0x28 |
| Esc | 0x29 |
| Backspace | 0x2A |
| Tab | 0x2B |
| Space | 0x2C |
| `- = [ ] \ ; ' , . /` và backtick | 0x2D–0x38 (bỏ qua 0x32) |
| F1–F12 | 0x3A–0x45 |
| Right / Left / Down / Up | 0x4F / 0x50 / 0x51 / 0x52 |

- Gõ mỗi ký tự: gửi nhấn (modifier + key), rồi gửi nhả.
- Phím tắt iOS có ích **[XÁC MINH trên iPhone]**: Cmd+Space (Spotlight), Cmd+C/V/A. Lưu ý bug ở mục 3.1.
- **Tiếng Việt [XÁC MINH]:** thử gõ bằng bàn phím phần cứng với bàn phím Vietnamese (Telex/VNI) của iOS. Nếu không được, phải tìm phương án khác.
- Media keys (consumer control): volume, có thể còn khoá màn hình và phím khác **[XÁC MINH]**.

### 8.9 Firmware ESP32 BLE (dòng Lightning)
- ESP-IDF + NimBLE, HID over GATT với các report: bàn phím, chuột tương đối (3 nút, X/Y int8, wheel), consumer control.
- Nhận khung CH9329 từ host qua UART hoặc USB-CDC, dịch sang BLE report, rồi trả ack **đúng định dạng CH9329**.
- Lưu bonding vào NVS; tự kết nối lại sau khi iPhone hoặc ESP32 khởi động lại.
- GET_INFO dùng byte "USB enum status" để báo trạng thái kết nối BLE.
- **Cần nghiên cứu:** cách chuyển sang profile chỉ bàn phím (bug 3.1) trên BLE. Đổi descriptor BLE thường đòi ghép lại.
- **Tuỳ chọn:** dùng ESP32-S3 USB OTG (TinyUSB) làm HID có dây thay CH9329. Lợi ích là tự do định nghĩa descriptor và re-enumerate hai profile giống Aiden.

### 8.10 API (GĐ 3)
**REST:**
- `GET /devices`
- `GET /devices/{id}/screenshot` (PNG)
- `POST /devices/{id}/tap` với body `{x, y, space: "norm" | "frame"}`
- `POST /devices/{id}/long_press`
- `POST /devices/{id}/swipe` với body `{x1, y1, x2, y2, duration}`
- `POST /devices/{id}/scroll`
- `POST /devices/{id}/type` với body `{text}`
- `POST /devices/{id}/key` với body `{combo}`, ví dụ `"cmd+space"`
- `POST /devices/{id}/home`
- `POST /devices/{id}/app_switcher`

**WebSocket:**
- `/devices/{id}/stream`: gửi frame JPEG kèm trạng thái.
- `/devices/{id}/input`: nhận sự kiện từ web UI.

**Device registry:** định danh máy theo đường dẫn USB vật lý, để map đúng cổng serial với capture card khi có nhiều máy.

---

## 9. Lộ trình và tiêu chí nghiệm thu

### GĐ 0 — Kiểm chứng phần cứng
Người dùng thực hiện; Claude Code chuẩn bị script và checklist.

**Dòng USB-C**
- [ ] Qua cùng một hub, iPhone nhận đồng thời HDMI (capture có hình) và HID (GET_INFO báo USB connected; con trỏ tròn xuất hiện và di chuyển được).
- [ ] Gõ chữ vào Notes đúng.
- [ ] Ghi nhận chuột absolute có tác dụng hay không.
- [ ] **Con trỏ có hiện trong hình capture không** (quyết định hướng đi của mục 8.4).
- [ ] Nút phải → Home hoạt động.
- [ ] Chạy Cmd+Space 20 lần, đếm số lần thành công (đo mức độ bug 3.1).
- [ ] Ghi nhận hành vi khi màn hình khoá và khi hiện popup Wired Accessories.

**Dòng Lightning**
- [ ] Digital AV Adapter → capture card có hình.
- [ ] Camera Adapter + CH9329: HID có dây hoạt động (để tham khảo).

**Chung:** ghi lại model iPhone, phiên bản iOS, chip capture card (lệnh `lsusb`), mẫu hub.

### GĐ 1 — Driver HID + CLI + fake device
**Nghiệm thu:**
- Unit test chạy qua trên fake device.
- Trên máy thật, `hidtest` chạy được: move, click, tap, type, key, home, scroll, swipe.
- Có số đo bench (ms/lệnh).

### GĐ 1b — Firmware ESP32 BLE
**Nghiệm thu:**
- Ghép được với iPhone.
- `hidtest` chạy mà không phải sửa code.
- Tự kết nối lại sau khi khởi động lại.

### GĐ 2 — Vision + closed-loop
**Nghiệm thu:** đạt mục tiêu ở 8.5; tự hiệu chỉnh được.

### GĐ 3 — API + web UI
**Nghiệm thu:** điều khiển mượt từ trình duyệt; độ trễ hình dưới 200 ms trong LAN (mục tiêu tham khảo).

### GĐ 4 — Ổn định và nhiều máy
- Tự kết nối lại khi mất kết nối.
- Phát hiện máy bị khoá hoặc có popup.
- Log đầy đủ.
- Chạy N máy trên một host; đánh giá băng thông USB của các capture card.

### GĐ 5 — Board nhúng theo từng máy
- SoC Linux có USB gadget (HID + ECM) và đầu vào HDMI-to-CSI, giống Aiden.
- Nghiên cứu hướng USB-Ethernet + AirPlay (phải được người dùng duyệt, xem 2.2).

---

## 10. Câu hỏi mở cần research và test

1. Topology hub có chạy ổn trên cả iPhone 15, 16 và 17 không? Mẫu hub nào tương thích?
2. iOS có chấp nhận descriptor composite của CH9329 (có cả collection absolute) không? Chuột tương đối có chạy bình thường không?
3. Gia tốc con trỏ trên iOS: có tắt được không, hay chỉ chỉnh được Tracking Speed? Với pacer cố định thì quan hệ có đủ tuyến tính không?
4. Con trỏ có hiện trong tín hiệu HDMI không (mục 8.4)?
5. Bug phím tắt Cmd (3.1): tần suất bao nhiêu? Cách né bằng đổi work mode của CH9329 có dùng được không?
6. Bug xoay ngang (3.1): còn xảy ra trên iOS 26+ không?
7. Hành vi khi máy khoá, popup Wired Accessories, USB Restricted Mode; HID có tác dụng trên màn hình khoá không?
8. Gõ tiếng Việt qua bàn phím phần cứng.
9. BLE HID: độ trễ, việc giữ bonding, tự kết nối lại, giới hạn số kết nối.
10. Capture card: MJPG hay YUYV cho độ trễ tốt hơn; Pi 4/5 gánh được mấy card cùng lúc.
11. Lightning Digital AV Adapter có ổn định với capture card giá rẻ không?
12. Safari trên iPhone có phát `pointermove` khi dùng con trỏ AssistiveTouch không (mục 8.6)?

---

## 11. Quy trình làm việc hardware-in-the-loop

- Mỗi lần bàn giao phải có: lệnh chạy chính xác, output mong đợi, và danh sách output cần gửi lại.
- Log dạng JSON lines có timestamp. Khi có lỗi, lưu frame capture kèm log.
- Không tự động ghi cấu hình chip. Luôn cung cấp lệnh khôi phục.
- Nên hỏi kỹ sư nội bộ (người đã làm thành công với IC HID): dùng IC nào, model iPhone nào, lấy hình bằng cách nào, xử lý độ chính xác con trỏ ra sao.

---

## 12. Nguồn tham khảo

**Aiden**
- Firmware: https://github.com/AidenAI-IO/aiden-firmware
- Demo phần cứng: https://github.com/AidenAI-IO/aiden-hardware-demo
- Bug phím tắt khi có cả bàn phím và chuột: https://huggingface.co/blog/NatalieY/debugging-aiden
- Bài giới thiệu phần cứng: https://huggingface.co/blog/NatalieY/aiden-hardware-ai-agent-device

**Apple Developer Forums**
- iOS chỉ nhận chuột tương đối: https://developer.apple.com/forums/thread/652700 và https://developer.apple.com/forums/thread/712996
- Bug con trỏ khi xoay ngang: https://developer.apple.com/forums/thread/786963

**Cài đặt con trỏ, AssistiveTouch**
- AbilityNet (iOS 26): https://mcmw.abilitynet.org.uk/how-to-make-it-easier-to-use-a-mouse-or-trackpad-with-your-iphone-or-ipad-in-ios-26
- Tuỳ chọn màu và độ tương phản con trỏ: https://forums.appleinsider.com/discussion/215119

**Xuất hình iPhone 15 qua USB-C:** https://appleosophy.com/2023/09/13/all-iphone-15-models-support-displayport-via-usb-c/

**Wired Accessories trên iOS 26**
- https://www.macworld.com/article/2945482/ios-26-juice-jacking-wired-accessories-always-ask.html
- Bug cài đặt bị khoá: https://tidbits.com/2025/10/13/juice-jacking-protection-setting-broken-in-ios-26/

**KVM**
- PiKVM (chuột tương đối và Bluetooth): https://docs.pikvm.org/blog/2020/11/11/kvmd-2-4-relative-mouse-bluetooth/
- JetKVM: https://www.cnx-software.com/2025/03/21/jetkvm-a-69-kvm-over-ip-solution-with-open-source-software/

**Sản phẩm thương mại**
- SOME 3C control board: https://some3c.com/products/iphone-farm-ios-automation-control-board
- iMouse XP: https://pypi.org/project/imouse-py/0.0.1/
- Wormhole (虫洞), bài phân tích: https://sspai.com/post/60970

**Firmware BLE tham khảo:** https://github.com/KMChris/esp32-kvm

**Thư viện CH9329 đa dụng (để đối chiếu giao thức):** https://pypi.org/project/pych9329-hid/

---

## 13. Lưu ý sử dụng

Tự động hoá thao tác trên nền tảng của bên thứ ba (mạng xã hội, thương mại điện tử...) có thể vi phạm điều khoản sử dụng và dẫn tới bị khoá tài khoản. Cần xác định rõ mục đích sử dụng trước khi mở rộng quy mô.
