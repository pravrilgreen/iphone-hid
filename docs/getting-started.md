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

Mở `http://localhost:8000`: có 4 iPhone mô phỏng. (Chạy như vậy thì server không có token, nó in cảnh báo
"anyone ... can control the phones": ai vào được máy này cũng điều khiển được. Thử cả phần xác thực thì thêm
`--token <chuỗi bí mật>`; web console sẽ hỏi token một lần.) Trong đó:
- chọn một máy, chế độ **Precise tap**: click lên hình là chạm đúng chỗ đó (mở app **Targets** để xem độ
  chính xác);
- chế độ **Live control** chuyển chuột và bàn phím của bạn thẳng xuống iPhone mô phỏng;
- khoá máy, popup phụ kiện, mất tín hiệu: gọi `POST /api/devices/{id}/sim/{op}` (xem `/docs`) để thử cách hệ
  thống phản ứng.

Tuỳ chọn:
- `--uncalibrated`: bắt đầu chưa hiệu chỉnh, rồi bấm **Calibrate** để xem quy trình qua Safari mô phỏng;
- `--sim-absolute`: mô phỏng iPhone nhận chuột tuyệt đối;
- `--model <model>`: chọn model mô phỏng (mặc định `iphone-15`; xem [simulator.md](simulator.md)).

Công cụ dòng lệnh cho chip HID giả:

```bash
python tools/hidtest.py --fake        # gõ `help`
```

## 3. Dùng từ Python (dự án automation)

```python
from ihc.client import Farm

farm = Farm.discover(token="...")            # mọi hộp trong mạng LAN (mDNS); hoặc Farm("http://box-01:8000", token="...")
for d in farm.devices():                     # mỗi hộp một iPhone, tên riêng theo serial board: "iphone-b40d9e"
    print(d.host, d.id)
phone = farm.device("iphone-b40d9e")         # hoặc farm.devices()[0]

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

Token: bỏ `token=` thì SDK lấy biến môi trường `IHC_TOKEN`. Mỗi hộp có token riêng thì truyền
`token={"http://box-01:8000": "...", "http://box-02:8000": "..."}`. Gọi REST trực tiếp thì gửi header
`Authorization: Bearer <token>`, và mọi `POST` phải có `Content-Type: application/json` (kể cả khi không có
body), nếu không server trả 415.

Thử lại an toàn: SDK gửi kèm mỗi lệnh một `Idempotency-Key`. Lệnh bị timeout thì gọi lại với cùng key
(`phone.tap(x, y, idempotency_key=e.idempotency_key)`, `e` là `IhcError` vừa bắt được): iPhone không bao giờ
làm hai lần. Lệnh mà client đã bỏ đi trước khi nó kịp chạy (đang xếp hàng sau lệnh khác) thì server bỏ qua.

## 4. Với phần cứng thật: hộp Orange Pi 5 Plus

Mỗi iPhone một hộp: board Orange Pi 5 Plus là bàn phím + chuột của iPhone (Linux USB gadget trên cổng Type-C
USB 3.0/DP), đọc màn hình qua cổng HDMI IN của chính nó, và chạy server. Linh kiện, cách nối và xử lý sự cố:
[gadget.md](gadget.md).

1. Cài đặt iPhone theo [iphone-setup.md](iphone-setup.md).
2. Nối phần cứng theo [gadget.md](gadget.md#wiring): sạc 30 W trở lên vào cổng PD của hub, HDMI của hub vào
   **HDMI IN** của board, cổng USB-A của hub qua **cáp USB-A → USB-C** vào cổng Type-C cạnh các cổng USB 3 (không
   phải cổng nguồn), hub vào iPhone, cấp nguồn board sau cùng.
3. Trên board (image Ubuntu hoặc Debian của Orange Pi), trong thư mục repo đã cài ở mục 1 (venv đang bật), kiểm
   tra cổng Type-C làm được USB device:

   ```bash
   ls /sys/class/udc                  # phải in một tên, ví dụ fc000000.usb
   ```

   Không in gì thì xem mục Troubleshooting của [gadget.md](gadget.md#troubleshooting), hoặc dùng phương án dự
   phòng ở mục 5.
4. Cài hộp: gadget được dựng lúc boot (`ihc-gadget.service`), rồi server chạy `ihc serve --auto` (`ihc.service`).
   Cách nhanh nhất: tải file `ihc-box-<version>-linux-aarch64.run` ở
   [bản release mới nhất](https://github.com/pravrilgreen/iphone-hid/releases/latest), chép vào board rồi chạy.
   File này có sẵn Python và mọi thư viện, không cần pip:

   ```bash
   chmod +x ihc-box-*-linux-aarch64.run
   sudo ./ihc-box-*-linux-aarch64.run
   ```

   Từ mã nguồn thì dùng `sudo sh deploy/install.sh` (cài bằng pip). Cài bằng file release thì các lệnh là
   `ihc`, `ihc-hidtest`, `ihc-capture-check` (thay cho `python3 tools/hidtest.py`, `python3 tools/capture_check.py`).

5. Kiểm tra (mở khoá iPhone, chọn **Allow** nếu iOS hỏi về phụ kiện):

   ```bash
   ihc gadget status                                  # "USB state configured": iPhone đã nhận gadget
   python3 tools/capture_check.py list                # HDMI IN (rk_hdmirx hoặc snps_hdmirx)
   ```

   Kiểm tra bàn phím + chuột bằng `hidtest` thì dừng server trước, vì hai tiến trình không nên cùng gửi lệnh. Sau
   khi cài, node `/dev/hidgN` thuộc nhóm `ihc`: vào nhóm này một lần (`sudo usermod -aG ihc $USER`, rồi đăng xuất
   và đăng nhập lại). Các bài B2–B4 của [phase0-checklist.md](phase0-checklist.md) làm chi tiết:

   ```bash
   sudo systemctl stop ihc
   python3 tools/hidtest.py --gadget "info; abstest"
   sudo systemctl start ihc
   ```

6. Mở web console ở `http://<địa chỉ board>:8000`. Máy tên `iphone-` cộng 6 ký tự cuối số serial của board (đặt
   tên khác: `IHC_PHONE_ID=...` trong `/etc/default/ihc`). Bấm **Calibrate** (iPhone phải cùng mạng với
   board).

Cài hộp xong, lần đầu tiên script tạo một token ngẫu nhiên ở `/var/lib/ihc/token` (chỉ user `ihc` đọc được) và in
ra cách xem nó: `sudo cat /var/lib/ihc/token`. Web console hỏi token này một lần rồi nhớ trong trình duyệt. Muốn
nhiều hộp dùng chung một token: `sudo IHC_TOKEN=<token> sh deploy/install.sh`. Từ máy khác trong mạng,
`IHC_TOKEN=<token> ihc devices` (hoặc `ihc devices --token-file <file>`) liệt kê mọi hộp và iPhone tìm được.

Muốn cố định cấu hình thay cho `--auto`, viết `farm.toml` theo mẫu trong [gadget.md](gadget.md) (`hid = { gadget =
"ihc" }`, `video = { device = "/dev/video0", input = "hdmi", fps = 30 }`) rồi chạy `ihc serve --config farm.toml`.

Server chỉ nhận Host là địa chỉ IP, `localhost`, tên `.local` hoặc tên của chính máy này (chống DNS
rebinding); truy cập qua tên khác (ví dụ reverse proxy) thì thêm `--allowed-host <tên>`, và
`--allow-origin https://<tên>` nếu trình duyệt mở console từ một origin khác.

Hiệu chỉnh khi Spotlight không mở được: web console hiện link trang hiệu chỉnh (có khoá `k`, đổi sau mỗi lần
hiệu chỉnh; qua API là `page_url` của `GET /api/devices/{id}/calibration`). Gõ link đó vào Safari trên iPhone
rồi bấm **calibrate the open page**.

## 5. Phương án dự phòng: cáp CH9329

Dùng khi image của board không có chế độ device, hoặc trên một host Linux khác. Cáp CH9329: đầu CH340 cắm vào
host, đầu CH9329 cắm vào cổng USB-A của hub. Hình lấy từ HDMI IN của board hoặc từ capture card USB MS2109.

1. Kiểm tra chip HID:

   ```bash
   python -m ihc.hid.scan
   python tools/hidtest.py --port <cổng> info
   ```
2. Kiểm tra nguồn hình:

   ```bash
   python tools/capture_check.py list
   ```
3. Chạy server, không cần cấu hình: nó tự tìm từng bộ (cáp HID + capture card trên cùng một hub USB; trên board
   không có gadget thì cáp CH9329 ghép với HDMI IN), đặt tên theo cổng USB, và quét lại khi cắm/rút:

   ```bash
   ihc serve --auto
   ```

   Muốn cố định cấu hình (cáp CH9329 + capture card USB) thì tạo `farm.toml` rồi sửa nếu cần:

   ```bash
   ihc discover > farm.toml
   ihc serve --config farm.toml
   ```

`sudo sh deploy/install.sh` cũng dùng được trên host không có chế độ device: khi đó nó không bật
`ihc-gadget.service`, chỉ chạy server.

Chi tiết từng bài kiểm tra ở [phase0-checklist.md](phase0-checklist.md).
