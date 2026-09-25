# Bộ mô phỏng: mô phỏng những gì, dựa trên cơ sở nào

Chưa có phần cứng, nên mọi tầng phần mềm được phát triển và kiểm thử trên iPhone mô phỏng. Nguyên tắc:
**mô phỏng đi qua đúng các đường code thật**, và **mọi hành vi chưa chắc chắn đều bật/tắt được** để thử cả hai
khả năng.

```
 IPhoneDevice ── CH9329Backend (code thật) ──pty──► FakeChip ──sự kiện──► SimPhone (UI, Safari)
      │                                              SimPointer (tương đối / tuyệt đối)  │
      └────────── FrameSource ◄── SimHdmiCapture (letterbox, MJPEG, độ trễ) ◄────────────┘
```

Mô phỏng đi qua đường cáp CH9329 (giao thức serial). Gadget của hộp (`GadgetBackend`) có cùng API với
`CH9329Backend`, nên mọi tầng phía trên HID (con trỏ, bàn phím, hiệu chỉnh, API) chạy y hệt. Bản thân gadget được
kiểm trong `tests/test_gadget.py` với các node `/dev/hidgN` giả (không cần controller USB): dựng và gỡ gadget qua
configfs, descriptor, và cách đếm báo cáo đã giao, bị lấy muộn hay bị mất. Đường đọc HDMI IN (tách luồng JPEG từ
lệnh GStreamer/ffmpeg, lệnh pipeline, luồng capture phía trên) được kiểm trong `tests/test_pipe_video.py`, không
cần board.

Có hai cách chạy:

| Cách | Dùng khi | Đặc điểm |
|---|---|---|
| Rig qua pty (`ihc.sim.rig`, `ihc serve --sim N`) | chạy thử cả hệ thống, web console, API | driver serial thật, thời gian như UART 9600 baud full-duplex; chịu jitter lịch luồng của máy |
| Trực tiếp, đồng hồ ảo (`ihc.sim.direct`) | test độ chính xác và hiệu chỉnh | gọi chip trong tiến trình, thời gian ảo, kết quả tất định và nhanh |

**Model mô phỏng:** iPhone 15, 15 Plus, 15 Pro, 15 Pro Max, 16, 16 Plus, 16 Pro, 16 Pro Max, 17, 17 Pro, 17 Pro Max
và iPhone Air. Chọn bằng `--model` (tên hoặc khoá, ví dụ `--model iphone-15-pro-max`; mặc định `iphone-15`).

## Mô phỏng gì

| Thành phần | Mô phỏng gì | Cơ sở |
|---|---|---|
| Chip HID (`FakeChip`) | từng byte giao thức CH9329: khung, checksum, mã lỗi, địa chỉ, broadcast, cấu hình 50 byte (chỉ có hiệu lực sau khi cấp nguồn lại), work mode | tài liệu WCH V1.0 ([ch9329-protocol.md](ch9329-protocol.md)) |
| Đường serial (`FakeSerialDevice`) | pty thật; chip thấy baud do host đặt (sai baud thì mất liên lạc thật); mỗi khung tới chip khi truyền xong trên dây; ack đi trên đường riêng. Thời điểm khung tới chip tính theo **lúc host ghi** (đường USB-serial có độ trễ cố định), không theo lúc luồng Python của mô phỏng chạy tới, nên độ chính xác trên rig pty không phụ thuộc máy đang bận | UART full-duplex |
| Con trỏ (`SimPointer`) | tương đối có gia tốc theo vận tốc (bộ ước lượng vận tốc "quên" sau 100 ms), kẹp mép màn hình (cách mép phải/dưới 1 pt); tuỳ chọn nhận chuột tuyệt đối, con trỏ trượt tới trong 60 ms | iOS có gia tốc theo vận tốc; Aiden cho thấy iOS nhận chuột tuyệt đối qua USB và phải chờ trượt trước khi click |
| iPhone (`SimPhone`) | Home, Notes, Settings, Spotlight, App Switcher, Safari (mở URL từ Spotlight, trang hiệu chỉnh gửi sự kiện click như trang thật), app Targets để đo độ chính xác; nút phải = Home, nút giữa = App Switcher; gõ phím; vuốt; cuộn; khoá máy; popup phụ kiện; độ trễ animation | AssistiveTouch, bàn phím phần cứng, Safari iOS 15+ (thanh địa chỉ ở dưới) |
| HDMI + nguồn hình (`SimHdmiCapture`) | scale-to-fit + viền đen, black level 16, fps, độ trễ, JPEG, mất tín hiệu | cách mirror của iPhone, và khung JPEG như từ capture card MJPEG hoặc pipeline HDMI IN của hộp |

## Tham số bật/tắt được

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `absolute` | `False` | iPhone có theo chuột tuyệt đối không (trên máy thật: bài B4) |
| `tracking` | 1,0 | hệ số Tracking Speed |
| `pointer_visible` | `True` | con trỏ có hiện trong HDMI không (chỉ ảnh hưởng hình ảnh) |
| `latency`, `fps` | 0,08 s, 30 | nguồn hình |
| `simulate_timing` | `True` | thời gian serial như 9600 baud |
| `open_delay` | 0,35 s | animation mở app |
| `usb_connected`, `powered`, `noise_on_mismatch`, `drop_next`, `fail_next` | | hỏng hóc của chip và đường truyền |
| `mute_next`, `reply_delays` | | lệnh đã chạy nhưng không có phản hồi, hoặc phản hồi tới muộn |

## Kết quả trên mô phỏng (đồng hồ ảo, 150 tap vào mục tiêu ngẫu nhiên)

| Cấu hình | Sai số tap tối đa | Thời gian mỗi tap | Hiệu chỉnh |
|---|---|---|---|
| Tuyệt đối | 0,17 pt | 0,18 s | khoảng 8 s |
| Tương đối, Tracking 0,4–2,5 | 0,5–1,0 pt | median 1,2–1,5 s | 48–52 s |

Tất cả 100% trúng mục tiêu. Hiệu chỉnh không bao giờ click ra ngoài trang, kể cả khi mất sự kiện, sự kiện tới
muộn, 20% sự kiện bị rơi, gia tốc mạnh (tới 10 lần), Tracking 0,25–8, hay bản đồ tuyệt đối sai: trong những
trường hợp đó nó dừng lại và báo lỗi, hoặc chuyển sang chế độ tương đối. Ngoại lệ duy nhất là vài click trên
thanh trạng thái khi đang tìm trang, lúc Safari không báo sự kiện di chuột (`tests/test_calibration.py`).

## Giả định đang dùng, phải kiểm chứng trên phần cứng

Mã bài là các bài trong [phase0-checklist.md](../research/phase0-checklist.md).

| Giả định | Bài test |
|---|---|
| iOS theo chuột tuyệt đối từ gadget của hộp | B4 |
| iOS theo chuột tuyệt đối từ CH9329 (dự phòng) | D3 |
| Gia tốc chỉ phụ thuộc vận tốc và lặp lại được; nghỉ 0,1 s là đủ để "đứng yên" | B5 (chế độ tương đối) |
| Con trỏ bị kẹp ở mép màn hình | B5 (chế độ tương đối) |
| Khoá máy thì phía USB báo "không kết nối" | B6 |
| Spotlight + URL + Return mở Safari | B5 |
| Lệnh HID khi USB chưa được enumerate: CH9329 trả E6 | D3 |
| `RESET` của CH9329 không áp dụng cấu hình mới | D2 |
| Trường 2 byte trong cấu hình CH9329 là big-endian | D1 (`cfg`) |
