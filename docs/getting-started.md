# Bắt đầu nhanh

## 1. Cài đặt

Cần Linux (hoặc macOS để thử mô phỏng) và Python 3.10 trở lên.

```bash
git clone <repo> iphone-hid && cd iphone-hid
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,video,api,box]"
pytest -q
```

## 2. Chạy thử không cần phần cứng

```bash
ihc serve --sim 4
```

Mở `http://localhost:8000`: có 4 iPhone mô phỏng. Trong đó:
- chọn một máy, chế độ **Precise tap**: click lên hình là chạm đúng chỗ đó (mở app **Targets** để xem độ
  chính xác);
- chế độ **Live control** chuyển chuột và bàn phím của bạn thẳng xuống iPhone mô phỏng;
- khoá máy, popup phụ kiện, mất tín hiệu: gọi `POST /api/devices/{id}/sim/{op}` (xem `/docs`) để thử cách hệ
  thống phản ứng.

Tuỳ chọn:
- `--uncalibrated`: bắt đầu chưa hiệu chỉnh, rồi bấm **Calibrate** để xem quy trình qua Safari mô phỏng;
- `--sim-absolute`: mô phỏng iPhone nhận chuột tuyệt đối;
- `--sim-bridge`: mô phỏng bridge ESP32 thay cho CH9329 (chip tự tạo nhịp).

Công cụ dòng lệnh cho chip HID giả:

```bash
python tools/hidtest.py --fake        # gõ `help`
```

## 3. Dùng từ Python (dự án automation)

```python
from ihc.client import Farm

farm = Farm.discover()                       # mọi box trong mạng LAN (mDNS); hoặc Farm("http://farm-01:8000")
phone = farm.device("iphone-01")

phone.home()
phone.tap(0.5, 0.93)                         # toạ độ 0..1 trên màn hình iPhone
phone.swipe(0.5, 0.8, 0.5, 0.2)
phone.key("cmd+space")
phone.type("notes\n")
phone.screenshot("shot.png")
print(phone.status()["state"])               # ready / busy / hid_disconnected / no_signal ...
```

Các lệnh này cũng có qua REST (`POST /api/devices/{id}/tap` ...) và WebSocket. Danh sách đầy đủ có ở trang
`http://<host>:8000/docs` khi server đang chạy.

## 4. Với phần cứng thật

1. Cài đặt iPhone theo [iphone-setup.md](iphone-setup.md).
2. Nối phần cứng theo sơ đồ trong README.
3. Kiểm tra chip HID:

   ```bash
   python -m ihc.hid.scan
   python tools/hidtest.py --port <cổng> info
   ```
4. Kiểm tra capture card:

   ```bash
   python tools/capture_check.py list
   ```
5. Chạy server, không cần cấu hình: nó tự tìm từng bộ (cáp HID + capture card trên cùng một hub USB), đặt
   tên theo cổng USB, và quét lại khi cắm/rút:

   ```bash
   ihc serve --auto
   ```

   Muốn cố định cấu hình thì tạo `farm.toml` rồi sửa nếu cần:

   ```bash
   ihc discover > farm.toml
   ihc serve --config farm.toml
   ```
6. Mở web console, bấm **Calibrate** cho từng máy (iPhone phải cùng mạng với host).

Để host thành một "box" tự chạy khi bật máy (systemd, udev, mDNS): `sudo sh deploy/install.sh`. Từ máy khác
trong mạng, `ihc devices` liệt kê mọi box và iPhone tìm được.

Chi tiết từng bài kiểm tra ở [phase0-checklist.md](phase0-checklist.md).
