# iphone-hid

**Điều khiển iPhone cho automation chỉ bằng phần cứng ngoài.** Hệ thống nhìn màn hình iPhone qua cổng
HDMI và thao tác bằng chuột/bàn phím HID giả lập, giống một "ngón tay ảo có mắt". Không jailbreak, không
Developer Mode, không cài app lên máy, không dùng công cụ chuyên cho iPhone (libimobiledevice,
WebDriverAgent...). iPhone chỉ thấy một màn hình ngoài và một bộ chuột + bàn phím.

Repo này là **tầng điều khiển iPhone** cho một hệ thống automation. Bên automation gọi API (HTTP/WebSocket
hoặc Python SDK) để xem màn hình, tap, vuốt, gõ phím, tìm chữ/ảnh; repo lo phần cứng, con trỏ và độ chính xác.

> **Trạng thái:** đang phát triển, **chưa có phần cứng**. Mọi tầng đều được viết và test trên **bộ mô phỏng**
> (chip HID giả qua pty, iPhone giả vẽ màn hình + con trỏ AssistiveTouch, tín hiệu HDMI giả). Hành vi mô
> phỏng bám theo tài liệu gốc của hãng chip và hành vi iOS đã được ghi nhận; điểm nào chưa chắc đều được
> đánh dấu và có bài test phần cứng tương ứng ở giai đoạn 0. Xem [Lộ trình](#lộ-trình-và-trạng-thái).

---

## Sản phẩm cuối sẽ trông thế nào

### 1. Web console

Một trang web cho người vận hành: lưới các máy đang chạy, mở một máy để xem live và điều khiển trực tiếp
(click để tap, kéo để vuốt, gõ phím), ghi lại thao tác thành kịch bản rồi phát lại.

```
┌─ iphone-hid console ─────────────────────────────────────── host: farm-01 ─ 4 devices ─┐
│  ● iphone-01  ready     ● iphone-02  ready     ● iphone-03  busy     ○ iphone-04  locked │
├───────────────────────────────┬────────────────────────────────────────────────────────┤
│ iphone-01  iPhone 15 · iOS 26 │  Actions                                               │
│ ┌───────────────────────────┐ │  [ Home ] [ App Switcher ] [ Spotlight ] [ Lock? ]     │
│ │ 09:41                  ▮▮ │ │  Type: [ hello world_______________ ] [Send]           │
│ │                           │ │  Key:  [ cmd+space ] [Send]                            │
│ │  ▢ Mail  ▢ Notes ▢ Maps   │ │                                                        │
│ │  ▢ Photos ▢ Clock ▢ Music │ │  Pointer   (0.512, 0.604)  closed-loop ✓  err 1.8 px   │
│ │          ◯ ← con trỏ      │ │  Last tap  182 ms · 1 correction                       │
│ │                           │ │  Stream    30 fps · 74 ms latency · MJPEG passthrough  │
│ │  ▢ Safari  ▢ Settings     │ │                                                        │
│ └───────────────────────────┘ │  Recorder  ● REC  tap(0.51,0.60) · type("hello") ...   │
│   click = tap · drag = swipe  │  [ Save script ] [ Replay ]                            │
└───────────────────────────────┴────────────────────────────────────────────────────────┘
```

### 2. Python SDK cho dự án automation

```python
from ihc.client import Farm

farm = Farm("http://farm-01:8000")          # một hoặc nhiều host
for d in farm.devices():
    print(d.id, d.model, d.state)           # iphone-01  iPhone 15  ready

phone = farm.device("iphone-01")
phone.home()
phone.tap(0.50, 0.93)                       # toạ độ chuẩn hoá 0..1 trên màn hình iPhone
phone.swipe(0.5, 0.8, 0.5, 0.2, duration=0.4)
phone.key("cmd+space")                      # Spotlight
phone.type("notes\n")
phone.wait_for_change(timeout=3)            # chờ UI đổi sau thao tác
phone.tap_image("templates/new_note.png")   # tìm ảnh mẫu trên màn hình rồi tap
phone.screenshot("shot.png")
```

### 3. API cho mọi ngôn ngữ

| Method | Endpoint | Việc |
|---|---|---|
| GET | `/devices` | danh sách máy, trạng thái (ready / busy / locked / no_signal / offline) |
| GET | `/devices/{id}/screenshot` | PNG/JPEG màn hình hiện tại |
| POST | `/devices/{id}/tap` | `{x, y, space: "norm" \| "frame"}`, tuỳ chọn `long: true` |
| POST | `/devices/{id}/swipe` | `{x1, y1, x2, y2, duration}` |
| POST | `/devices/{id}/scroll` | `{x, y, amount}` |
| POST | `/devices/{id}/type` | `{text}` |
| POST | `/devices/{id}/key` | `{combo: "cmd+space"}` |
| POST | `/devices/{id}/home`, `/app_switcher` | nút AssistiveTouch |
| POST | `/devices/{id}/find` | tìm ảnh mẫu / chữ trên màn hình |
| WS | `/devices/{id}/stream` | luồng JPEG + trạng thái con trỏ |
| WS | `/devices/{id}/input` | sự kiện điều khiển realtime từ web UI |

### 4. Một lệnh để chạy

```bash
ihc serve --config farm.yaml      # phần cứng thật: map cổng serial ↔ capture card theo cổng USB vật lý
ihc serve --sim 4                 # 4 iPhone mô phỏng, không cần phần cứng
```

---

## Nguyên lý hoạt động

### Phần cứng cho mỗi iPhone

**iPhone cổng USB-C (15 trở lên):** một dây mang cả hình lẫn điều khiển.

```
            ┌──── Hub USB-C (DisplayPort Alt Mode + USB-A + sạc PD) ────┐
iPhone ═════│ HDMI  ──► Capture card HDMI→USB ──► host Linux   (hình)   │
   USB-C    │ USB-A ◄── Chip HID (CH9329) ◄── serial ◄── host  (thao tác)│
            │ PD    ◄── Sạc ≥ 20 W                                       │
            └────────────────────────────────────────────────────────────┘
```

**iPhone cổng Lightning (6s trở lên, iOS 13+):** hình qua adapter HDMI, điều khiển qua Bluetooth.

```
iPhone ══► Lightning Digital AV Adapter ──HDMI──► Capture card ──► host      (hình)
iPhone ◄── Bluetooth LE ── ESP32 (HID bàn phím + chuột) ◄── serial ◄── host  (thao tác)
```

Firmware ESP32 nói **đúng giao thức serial của CH9329**, nên phần mềm host dùng chung cho cả hai dòng máy.

### Con trỏ: vì sao cần "mắt"

- iOS nhận chuột ngoài qua **AssistiveTouch** và **chỉ nhận chuột tương đối** (dx, dy). Không có lệnh "đặt
  con trỏ tại (x, y)", và iOS còn có gia tốc con trỏ.
- Cách làm: đẩy con trỏ về góc để biết chắc vị trí, di chuyển bằng các bước đều nhịp (quãng đường gần tuyến
  tính theo số bước), rồi **nhìn con trỏ trên hình capture** (viền màu đặc trưng) để sửa sai số trước khi
  click. Tham số hiệu chỉnh được lưu theo từng máy.
- Nếu con trỏ không hiện trong tín hiệu HDMI: dùng open-loop đã hiệu chỉnh, rồi xác minh kết quả bằng việc UI
  có thay đổi sau thao tác hay không.

### Kiến trúc phần mềm

```
 automation của bạn ──► Python SDK / REST / WebSocket          web console (live view, click-to-tap, recorder)
                              │                                        │
 ┌────────────────────────────▼────────────────────────────────────────▼──────────────┐
 │ api       FastAPI + WebSocket, device registry, job queue theo từng máy            │
 │ device    IPhoneDevice: tap / swipe / type / key / find, trạng thái, tự kết nối lại│
 │ control   closed-loop pointer, hiệu chỉnh tự động, xác minh thao tác               │
 │ vision    capture V4L2 (MJPEG passthrough), vùng màn hình, dò con trỏ, tìm ảnh/chữ │
 │ input     pointer model (pacer, reset góc), gestures, keymap                        │
 │ hid       driver CH9329 (serial) │ ESP32 BLE bridge (cùng giao thức) │ chip giả     │
 └──────────────────────────────────────────────────────────────────────────────────────┘
 sim: iPhone mô phỏng (UI, con trỏ, gia tốc, HDMI letterbox) cắm vào đúng các giao diện trên
```

Toạ độ công khai của API là **chuẩn hoá (0..1, 0..1) trên màn hình iPhone**, không phụ thuộc capture card hay
độ phân giải.

---

## Hiệu năng và quy mô

| Chỉ số | Mục tiêu | Cách đạt |
|---|---|---|
| Độ chính xác tap | 95% lần trong 5 px (frame 1080p) | closed-loop theo vị trí con trỏ trên hình |
| Thời gian một tap | < 1,5 s kể cả sửa sai | pacer 25 ms/bước, tối đa vài vòng sửa |
| Độ trễ hình tới trình duyệt | < 200 ms trong LAN | chuyển thẳng khung MJPEG của capture card, không decode/encode lại |
| Tốc độ gửi HID | ~50 báo cáo/s ở 9600 baud, nhiều hơn khi nâng lên 115200 | driver có chế độ chờ ack và không chờ ack |
| Nhiều máy / host | nhiều máy trên một host, mỗi máy một worker độc lập | vision chỉ decode khi cần; giới hạn thật là băng thông USB của capture card, sẽ đo ở giai đoạn 0 |

Nút thắt khi scale là **băng thông USB và CPU cho video**. Hệ thống được thiết kế để: giảm độ phân giải/fps
khi không cần, chỉ decode khung hình khi thị giác máy cần, và thêm host khi đầy (SDK quản lý nhiều host).

---

## Lộ trình và trạng thái

| Giai đoạn | Nội dung | Trạng thái |
|---|---|---|
| 0. Kiểm chứng phần cứng | script test, công cụ dò baud, checklist cho người có phần cứng | 🟡 công cụ đang hoàn thiện; chờ mua phần cứng để chạy |
| 1. Driver HID + CLI | driver CH9329 (ack/không ack, timeout, lỗi dễ hiểu), chip giả trên pty, `hidtest` | 🟢 chạy được trên chip giả |
| 1b. Firmware ESP32 BLE | bridge Bluetooth HID nói giao thức CH9329 | 🟡 đang viết |
| 2. Vision + closed-loop | capture, vùng màn hình, dò con trỏ, hiệu chỉnh, tap có phản hồi | 🟡 đang viết |
| 3. API + web console | REST/WebSocket, SDK, web UI, ghi/phát kịch bản | ⚪ tiếp theo |
| 4. Ổn định, nhiều máy | tự kết nối lại, phát hiện khoá màn hình/popup, log, registry theo cổng USB | ⚪ tiếp theo |
| 5. Board nhúng theo máy | SoC Linux có USB gadget HID + HDMI-to-CSI | ⚪ nghiên cứu |

🟢 xong trên mô phỏng · 🟡 đang làm · ⚪ chưa làm. Chưa hạng mục nào được kiểm chứng trên phần cứng thật.

---

## Chạy thử ngay, không cần phần cứng

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,vision,api]"
pytest -q

python tools/hidtest.py --fake        # CLI điều khiển chip HID giả; gõ `help`
```

Ví dụ phiên `hidtest` trên chip giả:

```
hid> info
chip V1.0 (0x30) | USB connected (status 0x01) | num lock off, caps lock off, scroll lock off
hid> type Hello, iPhone!
typed 14 characters
  [sim] text 'Hello, iPhone!'
hid> bench 30
GET_INFO round trip:       mean 22.6 ms, p50 22.6, p95 22.7, max 22.8 (n=30)
mouse report with ack:     mean 20.6 ms, p50 20.5, p95 20.6, max 20.9 (n=30)  -> 49 reports/s
```

Khi có phần cứng: `python -m ihc.hid.scan` tìm cổng và baud của chip, `python tools/hidtest.py --port ...`
để thử từng thao tác. Mọi phiên đều ghi log JSON lines vào `docs/test-logs/`.

---

## Phần cứng cần có (mỗi iPhone)

| Thiết bị | Dùng cho | Ghi chú |
|---|---|---|
| Cáp CH9329 thành phẩm (CH9329 + CH340, hai đầu USB-A) | HID có dây | không cần viết firmware |
| Hub USB-C có HDMI + USB-A + PD passthrough | dòng USB-C | không phải hub nào cũng chạy với iPhone, nên thử 2–3 mẫu |
| Capture card HDMI→USB (ưu tiên MS2130; MS2109 rẻ hơn) | cả hai dòng | |
| Apple Lightning Digital AV Adapter (chính hãng) | dòng Lightning | |
| ESP32-S3 DevKit | Bluetooth HID cho dòng Lightning | |
| Sạc PD ≥ 20 W | nuôi iPhone qua hub | |
| Host: Raspberry Pi 5 hoặc mini PC x86 chạy Linux | chạy phần mềm | |

iPhone cần cài đặt tay một lần: bật AssistiveTouch, gán nút chuột phải = Home, nút giữa = App Switcher,
tăng kích thước và đặt màu viền con trỏ, Auto-Lock = Never, bàn phím phần cứng layout U.S.

## Giới hạn (theo thiết kế)

Không vượt passcode/Face ID, không cài/ký app, không đọc dữ liệu hệ thống, không lấy cây UI. Máy phải được
mở khoá sẵn. Nội dung có DRM (HDCP) sẽ ra màn đen trên HDMI. Tự động hoá trên nền tảng bên thứ ba có thể vi
phạm điều khoản sử dụng của nền tảng đó.

## Cấu trúc repo

```
ihc/hid        driver CH9329, chip giả (pty), dò baud, cấu hình chip
ihc/input      keymap, pointer model, gestures
ihc/vision     capture, vùng màn hình, dò con trỏ, tìm ảnh/chữ
ihc/control    closed-loop, hiệu chỉnh
ihc/sim        iPhone mô phỏng
ihc/api        server HTTP/WebSocket
web/           web console
tools/         CLI test phần cứng (hidtest, capture_check)
firmware/      ESP32 BLE HID bridge (ESP-IDF)
tests/
docs/          tài liệu, checklist, log test phần cứng
```

## Phát triển

- Code, tên biến, commit message: tiếng Anh. Tài liệu: tiếng Việt.
- Không có phần cứng thì mọi thay đổi phải có test trên bộ mô phỏng; hành vi phần cứng chưa kiểm chứng phải
  được đánh dấu rõ trong code và có bài test tương ứng ở giai đoạn 0.
