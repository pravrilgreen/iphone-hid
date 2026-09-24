# Bắt đầu nhanh

## 1. Cài đặt

Cần Linux (hoặc macOS để thử mô phỏng) và Python 3.10 trở lên.

```bash
git clone <repo> iphone-hid && cd iphone-hid
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,video,api]"
pytest -q
```

## 2. Chạy thử không cần phần cứng

```bash
ihc serve --sim 4
```

Mở `http://localhost:8000`: có 4 iPhone mô phỏng. Trong đó:
- chọn một máy, bật **Chạm chính xác**, mở app **Targets** và click lên các vòng tròn để xem độ chính xác;
- chế độ **Điều khiển trực tiếp** chuyển chuột và bàn phím của bạn thẳng xuống iPhone mô phỏng;
- bảng **Mô phỏng** có khoá máy, popup phụ kiện, mất tín hiệu, để thử cách hệ thống phản ứng.

Tuỳ chọn:
- `--uncalibrated`: bắt đầu chưa hiệu chỉnh, rồi bấm **Hiệu chỉnh** để xem quy trình qua Safari mô phỏng;
- `--sim-absolute`: mô phỏng iPhone nhận chuột tuyệt đối.

Công cụ dòng lệnh cho chip HID giả:

```bash
python tools/hidtest.py --fake        # gõ `help`
```

## 3. Dùng từ Python (dự án automation)

```python
from ihc.client import Farm

farm = Farm("http://farm-01:8000")
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
5. Tạo cấu hình farm, rồi sửa nếu cần:

   ```bash
   ihc discover > farm.toml
   ```
6. Chạy server:

   ```bash
   ihc serve --config farm.toml
   ```

   Mở web console, bấm **Hiệu chỉnh** cho từng máy (iPhone phải cùng mạng với host).

Chi tiết từng bài kiểm tra ở [phase0-checklist.md](phase0-checklist.md).
