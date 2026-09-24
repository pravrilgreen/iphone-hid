# Bộ mô phỏng: mô phỏng những gì, dựa trên cơ sở nào

Chưa có phần cứng, nên mọi tầng phần mềm được phát triển và kiểm thử trên iPhone mô phỏng. Nguyên tắc:
**mô phỏng đi qua đúng các đường code thật**, và **mọi hành vi chưa chắc chắn đều bật/tắt được** để thử cả hai
khả năng.

```
 IPhoneDevice ── CH9329Backend (code thật) ──pty──► FakeChip ──sự kiện──► SimPhone (UI, Safari)
      │                                              SimPointer (tương đối / tuyệt đối)  │
      └────────── FrameSource ◄── SimHdmiCapture (letterbox, MJPEG, độ trễ) ◄────────────┘
```

Có hai cách chạy:

| Cách | Dùng khi | Đặc điểm |
|---|---|---|
| Rig qua pty (`ihc.sim.rig`, `ihc serve --sim N`) | chạy thử cả hệ thống, web console, API | driver serial thật, thời gian như UART 9600 baud full-duplex; chịu jitter lịch luồng của máy |
| Trực tiếp, đồng hồ ảo (`ihc.sim.direct`) | test độ chính xác và hiệu chỉnh | gọi chip trong tiến trình, thời gian ảo, kết quả tất định và nhanh |

## Mô phỏng gì

| Thành phần | Mô phỏng gì | Cơ sở |
|---|---|---|
| Chip HID (`FakeChip`) | từng byte giao thức CH9329: khung, checksum, mã lỗi, địa chỉ, broadcast, cấu hình 50 byte (chỉ có hiệu lực sau khi cấp nguồn lại), work mode | tài liệu WCH V1.0 ([ch9329-protocol.md](ch9329-protocol.md)) |
| Bridge ESP32 (`FakeChip(bridge=True)`) | cùng giao thức, cộng `GET_INFO` 0x40 (link, collection, tính năng, chu kỳ link), lệnh `SEND_MS_REL_RUN` tự tạo nhịp trên chip; mỗi báo cáo chờ tới sự kiện link kế tiếp (`link_period`, ví dụ connection interval Bluetooth 15 ms) | firmware trong `firmware/esp32_ble_hid` (cùng lõi C được test với driver thật qua pty) |
| Đường serial (`FakeSerialDevice`) | pty thật; chip thấy baud do host đặt (sai baud thì mất liên lạc thật); mỗi khung tới chip khi truyền xong trên dây; ack đi trên đường riêng. Thời điểm khung tới chip tính theo **lúc host ghi** (đường USB-serial có độ trễ cố định), không theo lúc luồng Python của mô phỏng chạy tới, nên độ chính xác trên rig pty không phụ thuộc máy đang bận | UART full-duplex |
| Con trỏ (`SimPointer`) | tương đối có gia tốc theo vận tốc (bộ ước lượng vận tốc "quên" sau 100 ms), kẹp mép màn hình (cách mép phải/dưới 1 pt); tuỳ chọn nhận chuột tuyệt đối, con trỏ trượt tới trong 60 ms | iOS có gia tốc theo vận tốc; Aiden cho thấy iOS nhận chuột tuyệt đối qua USB và phải chờ trượt trước khi click |
| iPhone (`SimPhone`) | Home, Notes, Settings, Spotlight, App Switcher, Safari (mở URL từ Spotlight, trang hiệu chỉnh gửi sự kiện click như trang thật), app Targets để đo độ chính xác; nút phải = Home, nút giữa = App Switcher; gõ phím; vuốt; cuộn; khoá máy; popup phụ kiện; độ trễ animation | AssistiveTouch, bàn phím phần cứng, Safari iOS 15+ (thanh địa chỉ ở dưới) |
| HDMI + capture card (`SimHdmiCapture`) | scale-to-fit + viền đen, black level 16, fps, độ trễ, JPEG, mất tín hiệu | cách mirror của iPhone và card giá rẻ |

## Tham số bật/tắt được

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `absolute` | `False` | iPhone có theo chuột tuyệt đối không (chưa kiểm chứng với CH9329) |
| `tracking` | 1,0 | hệ số Tracking Speed |
| `pointer_visible` | `True` | con trỏ có hiện trong HDMI không (chỉ ảnh hưởng hình ảnh) |
| `latency`, `fps` | 0,08 s, 30 | capture card |
| `simulate_timing` | `True` | thời gian serial như 9600 baud |
| `open_delay` | 0,35 s | animation mở app |
| `bridge`, `link_period`, `bridge_output` | `False`, 0, 0x7F | mô phỏng bridge ESP32 thay CH9329; chu kỳ link tới iPhone; loại link (0x01 Bluetooth, 0x02 USB) |
| `run_late_s`, `run_delays` | 10 ms, `[]` | bridge trả `E7` khi một báo cáo của đoạn chạy ra muộn hơn ngưỡng; `run_delays` giữ link lại để thử |
| `usb_connected`, `powered`, `noise_on_mismatch`, `drop_next`, `fail_next` | | hỏng hóc của chip và đường truyền |
| `mute_next`, `reply_delays` | | lệnh đã chạy nhưng không có phản hồi, hoặc phản hồi tới muộn |

## Kết quả trên mô phỏng (đồng hồ ảo, 150 tap vào mục tiêu ngẫu nhiên)

| Cấu hình | Sai số tap tối đa | Thời gian mỗi tap | Hiệu chỉnh |
|---|---|---|---|
| Tuyệt đối | 0,17 pt | 0,18 s | khoảng 8 s |
| Tương đối, Tracking 0,4–2,5, iPhone SE / 15 / 15 Pro Max | 0,5–1,0 pt | median 1,2–1,5 s | 48–52 s |

Tất cả 100% trúng mục tiêu. Hiệu chỉnh không bao giờ click ra ngoài trang, kể cả khi mất sự kiện, sự kiện tới
muộn, 20% sự kiện bị rơi, gia tốc mạnh (tới 10 lần), Tracking 0,25–8, hay bản đồ tuyệt đối sai: trong những
trường hợp đó nó dừng lại và báo lỗi, hoặc chuyển sang chế độ tương đối. Ngoại lệ duy nhất là vài click trên
thanh trạng thái khi đang tìm trang, lúc Safari không báo sự kiện di chuột (`tests/test_calibration.py`).

## Giả định đang dùng, phải kiểm chứng trên phần cứng

| Giả định | Bài test |
|---|---|
| Lệnh HID khi USB chưa được enumerate trả E6 | T7 |
| `RESET` không áp dụng cấu hình mới | X2 |
| Trường 2 byte của cấu hình là big-endian | T0 (`cfg`) |
| iOS có theo chuột tuyệt đối từ CH9329 | T1 |
| Gia tốc chỉ phụ thuộc vận tốc và lặp lại được; nghỉ 0,1 s là đủ để "đứng yên" | T1/T4 (validation), T4 |
| Con trỏ bị kẹp ở mép màn hình | T4 |
| Khoá máy thì phía USB báo "không kết nối" | T7 |
| Spotlight + URL + Return mở Safari | T1 |
