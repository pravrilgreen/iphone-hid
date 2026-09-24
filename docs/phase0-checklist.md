# Giai đoạn 0: checklist kiểm chứng phần cứng

Phần mềm đã chạy đầy đủ trên mô phỏng. Checklist này kiểm tra trên phần cứng thật những gì mô phỏng chỉ giả
định. Thứ tự các bài theo mức rủi ro (xem [feasibility.md](feasibility.md), mục 7). Làm lần lượt; kết quả
một bài có thể khiến bỏ qua bài sau.

Mỗi bước ghi rõ: **lệnh chạy**, **kết quả mong đợi**, **cần ghi lại gì**. Mọi công cụ đều tự ghi log JSON
lines vào `docs/test-logs/`. Cuối cùng gửi lại cả thư mục đó ([mục 9](#9-gửi-lại-những-gì)).
Mỗi bài trên iPhone cần chạy trên cả **iOS 26.x và iOS 27**.

## Các câu hỏi cần trả lời

| # | Câu hỏi | Bài |
|---|---|---|
| Q1 | Chip HID có giao **đủ và đúng** mọi báo cáo tới host USB không (mất / gộp / trễ)? Nhịp an toàn là bao nhiêu? | T0 |
| Q2 | iPhone có theo **chuột tuyệt đối** không? Nếu có, việc đặt con trỏ gần như được giải quyết. | T1 |
| Q3 | Hub nào cho **hình + HID + sạc** cùng lúc, và có tự hồi phục khi mất nguồn không? | T2 |
| Q4 | Báo cáo có tới iOS đầy đủ không (nhả nút có bị lỡ không)? | T3 |
| Q5 | Nếu chỉ có chuột tương đối: gia tốc có lặp lại đủ để tap trong 4 pt không? | T4 |
| Q6 | Bug phím tắt Cmd/Shift/Option xảy ra thường xuyên cỡ nào? | T5 |
| Q7 | Độ trễ hình tới trình duyệt; con trỏ có hiện trên HDMI không? | T6 |
| Q8 | Máy khoá / khởi động lại / popup phụ kiện thì chuyện gì xảy ra? | T7 |
| Q9 | Một host gánh được bao nhiêu capture card? | T8 |
| Q10 | Dòng Lightning có dùng được không? | T9 |
| Q11 | Chạy liên tục 72 giờ có ổn không? | T10 |

---

## 0. Chuẩn bị (một lần)

```bash
sudo apt install -y git python3-venv v4l-utils usbutils evtest
sudo usermod -aG dialout,video,input $USER          # rồi đăng xuất và đăng nhập lại
git clone <repo> iphone-hid && cd iphone-hid
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,video,api]"
pytest -q                                            # phải xanh hết
```

**Tập dượt không cần phần cứng:**
- `python tools/hidtest.py --fake`, gõ `help`;
- `ihc serve --sim 2`, mở `http://localhost:8000`.

**Cài đặt iPhone** theo [iphone-setup.md](iphone-setup.md).

**Đặt biến cổng.** Đặt `PORT` bằng đường dẫn ổn định của cáp CH9329 (xem `python -m ihc.hid.scan --list`), ví dụ:

```bash
PORT=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
```

---

## T0. Chip HID trên chính host Linux (chưa cần iPhone)

Cắm **cả hai đầu** cáp CH9329 vào host. Đầu HID sẽ hiện như chuột + bàn phím của host. Công cụ tự "giữ độc
quyền" thiết bị khi test, nên chuột trên màn hình host không bị ảnh hưởng.

```bash
lsusb                                            # có dòng 1a86:7523 (CH340) và 1a86:e129 (CH9329)
sudo lsusb -v -d 1a86:e129 | grep -E "bInterval|bInterfaceProtocol|wMaxPacketSize"
python -m ihc.hid.scan --port $PORT              # mong đợi: 9600 baud: OK
python tools/hidtest.py --port $PORT "info; cfg; cfg save docs/test-logs/ch9329-cfg-factory.json; bench 200"
python tools/hid_loopback.py --port $PORT list
python tools/hid_loopback.py --port $PORT all
```

Mong đợi:
- **`cfg`:** `work_mode 0x80`, `vid 0x1a86`, `pid 0xe129`. Nếu có dòng `WARNING`, chụp nguyên văn.
- **`bench`:** báo cáo có ack mất khoảng 20–25 ms ở 9600 baud.
- **`hid_loopback rel`:** mỗi nhịp một dòng `OK`/`LOSS`, gồm số đơn vị nhận/gửi, độ trễ, khoảng cách thực
  giữa các lần gửi (`sent every`) và giữa các lần host nhận được (`arrived every`, kèm min..max). Nhịp mà phần
  mềm dùng (20 ms) phải `OK` ở cả chế độ `ack` lẫn `pipelined`.
- **Khoảng `arrived every` min..max** là jitter mà chế độ tương đối sẽ gặp (host + CH340 + poll USB). Ở nhịp
  20 ms, mong muốn min..max nằm trong khoảng 18,5..21,5 ms. Nếu thấy các giá trị nhảy theo bậc (ví dụ 10/20/30
  ms) thì `bInterval` của chip không chia hết nhịp, hoặc báo cáo tới sát ranh giới poll: ghi lại và thử nhịp
  là bội số của `bInterval`.
- **`hid_loopback abs`:** `ABS_X`/`ABS_Y` đổi theo lưới đã gửi.
- **`hid_loopback keys`:** số phím nhấn bằng số ký tự, `all released: True`.

**Ghi lại:**
- `bInterval`;
- khoảng `arrived every` min..max ở từng nhịp;
- nhịp nhỏ nhất vẫn `OK`, và từ nhịp nào bắt đầu `LOSS` hoặc bị gộp;
- độ trễ p50/max;
- kết quả `abs`.

Thử lại ở 115200 baud (xem X1) nếu còn thời gian.

---

## T1. iPhone có theo chuột tuyệt đối không (quan trọng nhất)

Nối dòng USB-C:
- iPhone → hub USB-C;
- HDMI của hub → capture card → host;
- đầu HID của CH9329 → cổng USB-A của hub;
- sạc PD cắm vào hub **trước** khi cắm iPhone.

iPhone và host phải cùng mạng Wi-Fi/LAN (iPhone sẽ mở một trang trên host).

1. Tạo file cấu hình:

   ```bash
   ihc discover > farm.toml
   ```

   Nếu không ghép được cổng tự động, sửa tay theo mẫu trong `ihc/registry.py`: `hid.port` = `$PORT`,
   `video.device` = `/dev/v4l/by-path/...-video-index0`.
2. Chạy server:

   ```bash
   ihc serve --config farm.toml
   ```
3. Mở `http://<ip-host>:8000`, chọn máy, bấm **Calibrate**. Công cụ tự mở Safari qua Spotlight. Nếu không
   mở được, gõ tay trên iPhone địa chỉ `http://<ip-host>:8000/calibrate/<id>`, rồi gọi API hiệu chỉnh với
   `{"open_page": false}` (không gửi phím nào).
4. Chờ kết quả, khoảng 10 s nếu iPhone theo chuột tuyệt đối, khoảng 1 phút nếu không.

Mong đợi, ở bảng trạng thái:
- `mode: absolute` nếu iPhone theo chuột tuyệt đối, ngược lại `mode: relative`;
- `validation`: sai số trung bình/tối đa (pt) trên các điểm kiểm tra ngẫu nhiên.

Mục tiêu: sai số tối đa ≤ 2 pt với absolute, ≤ 4 pt với relative.

**Ghi lại:**
- `mode`;
- `validation`;
- file `calib/<id>.json`;
- log `docs/test-logs/*.jsonl`.

Nếu mode là `relative`: thử lại sau khi đổi work mode sang chỉ chuột (`hidtest ... "cfg set work_mode=2"`, rồi
rút/cắm), vì descriptor gộp có thể là nguyên nhân. Khôi phục bằng `cfg set work_mode=0`.

---

## T2. Hub: hình + HID + sạc cùng lúc

Làm với từng hub (ưu tiên Apple USB-C Digital AV Multiport Adapter, cộng 2 hãng khác):

```bash
python tools/capture_check.py snapshot --device /dev/video0
python tools/hidtest.py --port $PORT "info; move 150 0; move 0 150"
```

Mong đợi:
- ảnh chụp có màn hình iPhone;
- `info` báo `USB connected`;
- con trỏ tròn di chuyển trên iPhone;
- biểu tượng pin báo đang sạc.

Sau đó rút/cắm nguồn PD và ghi lại thứ gì rớt (hình, HID, sạc) và có tự trở lại không.

**Ghi lại:** hãng/model hub, kết quả từng mục.

---

## T3. Báo cáo tới iOS đầy đủ

1. Mở Notes, tạo ghi chú mới, rồi chạy:

   ```bash
   python tools/hidtest.py --port $PORT 'type Hello iPhone 123 !@#$%^&*()_+-=[]{};:,./<>?'
   ```

   Mong đợi: chuỗi hiện đúng từng ký tự.
2. Trong web console (chế độ **Precise tap**), mở app có danh sách dài. Vuốt 20 lần.
   Mong đợi: không lần nào bị "kẹt" ở trạng thái đang kéo (nhả nút bị lỡ).
3. Nếu `mode: absolute`, chạy 50 lần tap tại cùng một nút và đếm số lần trượt.

**Ghi lại:** số lỗi mỗi phần.

---

## T4. Chế độ tương đối (bắt buộc nếu T1 cho `relative`, và cho dòng Lightning)

Lặp lại **Calibrate** 3 lần (ép chế độ tương đối bằng `{"options": {"try_absolute": false}}` nếu cần) và so `validation` giữa các lần. Sau đó đổi Tracking Speed lên/xuống một nấc,
hiệu chỉnh lại, và so tiếp.

**Ghi lại:** các `validation` và file hiệu chỉnh (`calib/*.json`, có tham số từng hướng). Sai số tăng rõ giữa
các lần là dấu hiệu gia tốc iOS không lặp lại. Khi đó tăng `rest` trong file hiệu chỉnh (ví dụ 0,15 s) rồi
thử lại.

---

## T5. Bug phím tắt

1. Chạy:

   ```bash
   python tools/hidtest.py --port $PORT "cmdspace n=50"
   ```

   Trả lời `y` nếu Spotlight mở, `n` nếu không. Công cụ tự đóng bằng Esc.
2. Trong Notes, gõ vài chữ rồi chạy:

   ```bash
   python tools/hidtest.py --port $PORT "trial cmd+a n=30 close=right"
   ```
3. Thử lại cả hai ở chế độ chỉ bàn phím: `cfg set work_mode=1`, rút/cắm, chạy lại, rồi khôi phục
   `cfg set work_mode=0`.

**Ghi lại:** kết quả `k/n worked` cho từng lần.

---

## T6. Stream và độ trễ

1. Mở một trang đồng hồ bấm giờ có mili giây trên iPhone. Mở web console trên laptop, để hai màn hình cạnh
   nhau và chụp ảnh cả hai bằng điện thoại khác. Hiệu hai số là độ trễ hình tới trình duyệt. Lặp 5 lần.
2. Mở web console, chọn chế độ **Live control**, di chuột. Con trỏ có hiện trong hình không?
3. Chạy `python tools/capture_check.py probe --device /dev/video0` với MJPG 1920x1080 và 1280x720.

**Ghi lại:** độ trễ; con trỏ có/không; fps; Mbit/s.

---

## T7. Khoá máy, khởi động lại, popup phụ kiện

Mở shell tương tác (chỉ một tiến trình được mở cổng serial tại một thời điểm, nên tắt `ihc serve` trước):

```bash
python tools/hidtest.py --port $PORT
hid> note T7 bắt đầu
hid> watch
```

Trong lúc `watch` chạy, lần lượt:
1. khoá máy, đợi 5 phút;
2. mở khoá;
3. rút/cắm hub;
4. đổi Wired Accessories sang từng lựa chọn;
5. khởi động lại iPhone và thử gõ passcode bằng `type`.

Trước mỗi thao tác: Ctrl+C, gõ `note <thao tác>`, rồi `watch` lại. Khi máy khoá, thử `move 100 0` và `type abc`.

**Ghi lại:** trạng thái USB theo từng thao tác; HID có tác dụng lúc khoá không.

---

## T8. Nhiều capture card trên một host

```bash
python tools/capture_check.py list
python tools/capture_check.py multi --device /dev/video0 --device /dev/video2 --seconds 20
```

Tăng dần số card (1, 2, 3...), thử cả cổng USB 2 và USB 3.

**Ghi lại:**
- số card chạy đủ fps cùng lúc;
- lỗi `No space left on device` hay rớt khung bắt đầu từ card thứ mấy;
- `lsusb -t`.

---

## T9. Dòng Lightning

- **L1.** Lightning Digital AV Adapter → capture card: `capture_check.py snapshot` và `probe`. Mong đợi có
  hình. **Ghi lại:** độ phân giải, độ trễ (theo cách ở T6).
- **L2.** ESP32 BLE: nạp firmware theo [README firmware](../firmware/esp32_ble_hid/README.md), ghép
  Bluetooth, rồi chạy `hidtest --port <cổng DevKit> "info; move 100 0; run 20 0 10; type abc"`. Dòng
  `bridge:` của `info` phải có `chip-timed runs yes` và `link period` (connection interval iOS cấp, thường 15
  hoặc 30 ms). Làm lại T1 (bấm **Calibrate**) để biết BLE có theo chuột tuyệt đối không; ở chế độ tương đối,
  hiệu chỉnh tự chọn nhịp là bội số của link period.
  **Ghi lại:**
  - kết quả, link period;
  - link period có đổi không sau khi khoá/mở máy, sau vài phút không dùng (trạng thái máy báo "recalibrate" nếu
    đổi);
  - có tự kết nối lại sau khi tắt/bật Bluetooth, khởi động lại iPhone hoặc ESP32 không.
- **L3 (tham khảo).** Lightning to USB 3 Camera Adapter + CH9329: HID có dây có chạy không.

---

## T10. Chạy bền 72 giờ

```bash
ihc serve --config farm.toml --log docs/test-logs/soak.jsonl
```

Để chạy nền với một kịch bản tap định kỳ (một script dùng SDK chạy vòng lặp các thao tác). Có một
lần rút điện có chủ đích.

**Ghi lại:**
- số lần mất tín hiệu và HID, và có tự hồi phục không;
- nhiệt độ máy;
- % pin.

---

## X. Thí nghiệm thêm

- **X1. 115200 baud:**
  1. `hidtest --port $PORT "cfg set baud=115200"`, gõ `YES`, rồi rút/cắm.
  2. `hidtest --port $PORT --baud 115200 "info; bench 200"`.
  3. `hid_loopback --baud 115200 rel`.
  4. Khôi phục bằng `cfg set baud=9600`. Mất liên lạc thì chạy `python -m ihc.hid.scan --port $PORT`.
- **X2. `RESET` có áp dụng cấu hình không:**
  1. `cfg set baud=19200`, **không rút cáp**, chạy `reset`.
  2. Chạy `scan`.
  3. Khôi phục về 9600.
- **X3. Media keys:** `media volume_up`, `media mute`, `acpi sleep`. **Ghi lại:** phím nào có tác dụng.

---

## 9. Gửi lại những gì

1. Toàn bộ `docs/test-logs/` và `calib/` (commit lên nhánh hoặc nén gửi).
2. `docs/test-logs/environment.md`:

```markdown
| Mục | Giá trị |
|---|---|
| Model iPhone / iOS | |
| Hub USB-C (hãng, model) | |
| Capture card (`lsusb`, chip) | |
| Cáp CH9329 (link mua, `lsusb`) | |
| Host (Pi 5 / x86, OS, kernel) | |
| Tracking Speed / Pointer Size / Color | |
| Wired Accessories | |
```

3. Bảng Q1–Q11: có/không + ghi chú ngắn.

## 10. Sự cố thường gặp

| Hiện tượng | Xử lý |
|---|---|
| `permission denied` khi mở cổng serial / video / input | vào nhóm `dialout`, `video`, `input`, rồi đăng xuất và đăng nhập lại |
| `in use by another process` | đang có `hidtest` hoặc `ihc serve` khác mở cổng; hoặc ModemManager (`sudo systemctl stop ModemManager`) |
| `nothing came back` | đầu HID chưa có nguồn (chưa cắm vào iPhone/hub/host), sai cổng, sai baud (chạy `scan`) |
| `baud rate is probably wrong` | `python -m ihc.hid.scan --port $PORT`, rồi dùng `--baud` theo kết quả |
| lệnh chuột báo `EXEC_ERROR` | iPhone chưa nhận phụ kiện: mở khoá, cho phép phụ kiện, kiểm tra `info` |
| Hiệu chỉnh báo "page did not load" | iPhone chưa mở được trang: kiểm tra cùng mạng, gõ tay địa chỉ trong Safari |
| capture đen / không có khung | hub không xuất DisplayPort, hoặc card chưa nhận tín hiệu; thử cáp HDMI khác, thử `--fourcc YUYV` |
