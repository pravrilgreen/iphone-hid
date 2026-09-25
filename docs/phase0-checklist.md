# Giai đoạn 0: checklist kiểm chứng phần cứng

Phần mềm đã chạy đầy đủ trên mô phỏng. Checklist này kiểm tra trên phần cứng thật những gì mô phỏng chỉ giả
định. Cấu hình chính là **hộp all-in-one: một Orange Pi 5 Plus cho mỗi iPhone** ([gadget.md](gadget.md)). Các bài
B1–B8 đi theo thứ tự phụ thuộc: bài trước hỏng thì bài sau chưa chạy được. Mục D là phương án dự phòng với cáp
CH9329. Lý do và rủi ro của từng bài: [feasibility.md](feasibility.md), mục 7.

Mỗi bước ghi rõ: **lệnh chạy**, **kết quả mong đợi**, **cần ghi lại gì**. Công cụ tự ghi log JSON lines vào
`docs/test-logs/`. Cuối cùng gửi lại cả thư mục đó ([mục 9](#9-gửi-lại-những-gì)).
Mỗi bài trên iPhone cần chạy trên cả **iOS 26.x và iOS 27**.

## Các câu hỏi cần trả lời

| # | Câu hỏi | Bài |
|---|---|---|
| Q1 | Cổng Type-C USB 3.0/DP của board có chạy chế độ USB device (có UDC) với image đang dùng không? | B1 |
| Q2 | Một máy tính có nhận gadget là bàn phím + chuột, và gõ, di chuột đúng không? | B2 |
| Q3 | HDMI IN có hình iPhone không? Được bao nhiêu fps, với bộ mã hoá JPEG nào? | B3 |
| Q4 | iPhone có nhận gadget qua hub không? Có theo **chuột tuyệt đối** không? Phím tắt và nút Home/App Switcher có chạy không? | B4 |
| Q5 | Hiệu chỉnh qua server cho sai số tap bao nhiêu? | B5 |
| Q6 | Khoá máy, rút/cắm USB, HDMI, sạc, khởi động lại hộp: có tự hồi phục không? | B6 |
| Q7 | Độ trễ hình tới trình duyệt? Con trỏ có hiện trên HDMI không? | B7 |
| Q8 | Chạy liên tục 72 giờ có ổn không? | B8 |
| Q9 | (Dự phòng) Cáp CH9329 giao báo cáo có đủ không, và iPhone có theo chuột tuyệt đối của nó không? | D1–D3 |

---

## 0. Chuẩn bị (một lần)

**Phần cứng** (chi tiết và cách nối: [gadget.md](gadget.md)):

| Món | Ghi chú |
|---|---|
| Orange Pi 5 Plus (bản v2.x được) | nguồn USB-C 5 V / 4 A riêng |
| iPhone 15 trở lên có USB-C | không dùng 16e/17e: hai máy này không xuất hình |
| Hub USB-C: HDMI + USB-A + cổng USB-C PD vào | xem "Chọn hub" dưới đây |
| Sạc USB-C 30 W trở lên | cắm vào cổng PD của hub: nuôi hub và sạc iPhone |
| Cáp HDMI | hub → cổng **HDMI IN** của board |
| Cáp **USB-A → USB-C** | cổng USB-A của hub → cổng **Type-C USB 3.0/DP** của board (cạnh các cổng USB 3, không phải cổng nguồn) |
| Cáp Ethernet | cho API và web console |
| Một laptop (Linux thì tiện nhất) | cho bài B2 |

**Chọn hub.** Một hub USB-C thương mại là đủ, nếu có đủ 4 điều sau.
1. **HDMI qua DisplayPort Alt Mode.** Đa số hub USB-C có HDMI làm vậy. Tránh hub dùng **DisplayLink** (ghi
   "cần cài driver", hay dock nhiều màn hình), vì iPhone không có driver đó. Tránh hub chỉ dành cho Thunderbolt/USB4.
2. **Cổng USB-A có dữ liệu** (không phải cổng chỉ sạc). USB 2.0 là đủ cho bàn phím + chuột.
3. **Cổng USB-C sạc PD vào** (PD pass-through), để iPhone được sạc. Củ sạc nên 30 W trở lên, vì hub giữ lại một
   phần công suất.
4. **Một đầu USB-C cắm vào máy** (không phải hub cho iPad có kẹp hay đế riêng).

Ví dụ: **UGREEN Revodok 105 (15495)**: HDMI 4K30 qua DP Alt Mode, 1 cổng USB-A 3.0 + 2 cổng USB-A 2.0, cổng USB-C PD
100 W vào (chỉ để cấp nguồn). Apple USB-C Digital AV Multiport Adapter là mẫu tham chiếu (Apple ghi hỗ trợ iPhone).
Thử nhanh ngay khi mua, chưa cần board: cắm hub vào iPhone, HDMI vào TV, một bàn phím USB vào cổng USB-A, sạc vào
cổng PD; thấy hình trên TV, gõ được vào Notes và iPhone báo đang sạc, cả ba cùng lúc, là dùng được.

**Phần mềm trên board** (image Ubuntu hoặc Debian của Orange Pi; image này có driver HDMI IN):

```bash
sudo apt install -y git python3-venv v4l-utils usbutils \
    gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
git clone <repo> iphone-hid && cd iphone-hid
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,video,api,box]"
pytest -q                                            # phải xanh hết
uname -a; head -3 /etc/os-release                    # ghi lại image và kernel
```

`tools/gadget.py` chỉ cần Python chuẩn, nên chạy được bằng `sudo python3` ngay trong thư mục repo. Nó làm đúng
việc của `sudo ihc gadget up/status/down`.

**Tập dượt không cần phần cứng** (trên laptop hoặc board): `python tools/hidtest.py --fake`, gõ `help`;
`ihc serve --sim 2`, mở `http://localhost:8000`.

**Cài đặt iPhone** theo [iphone-setup.md](iphone-setup.md).

---

## B1. Cổng Type-C làm được USB device không (chưa cần iPhone)

```bash
ls /sys/class/udc                      # mong đợi: một tên controller, ví dụ fc000000.usb
python3 tools/gadget.py status
```

Mong đợi:
- `ls` in ra đúng một tên;
- `status` in `USB device controllers: <tên>` và `gadget 'ihc': not set up`.

Nếu `ls` không in gì: device tree của image đang giữ cổng ở chế độ host. Device tree mainline khai báo cổng này là
dual-role, và Orange Pi dùng nó để nạp image và cho ADB, nên phần cứng làm được. Thử image của chính Orange Pi. Nếu
vẫn không được, chuyển sang [mục D](#d-dự-phòng-cáp-ch9329) cho phần bàn phím + chuột, và vẫn làm B3.

Nếu `status` báo controller đang được gadget khác dùng (thường là ADB của image): dừng gadget đó, ví dụ
`echo '' | sudo tee /sys/kernel/config/usb_gadget/<tên>/UDC`, rồi chạy lại `status`.

**Ghi lại:**
- tên và phiên bản image, kernel (`uname -a`);
- tên UDC, hoặc "không có";
- có gadget khác (ADB) chiếm controller không, và đã dừng nó thế nào.

---

## B2. Gadget trên một laptop

Nối **cáp USB-A → USB-C**: đầu USB-A vào laptop, đầu USB-C vào cổng Type-C cạnh các cổng USB 3 của board. Không
dùng cáp C–C: với cáp C–C cả hai bên đều có thể nhận vai trò host.

Trên board:

```bash
sudo python3 tools/gadget.py up                      # profile RA (mặc định)
python3 tools/gadget.py status                       # "USB state configured" = laptop đã nhận
python3 tools/hidtest.py --gadget "info; type hello; move 200 0; move 0 200; abstest; bench 100"
```

Để con trỏ chuột của laptop trong một ô soạn thảo trước khi chạy `type`.

Trên laptop Linux:

```bash
lsusb -d 1d6b:0104                                   # có một dòng (VID/PID của gadget)
sudo lsusb -v -d 1d6b:0104 | grep -E "iProduct|iSerial|bInterfaceProtocol|bInterval|wMaxPacketSize"
```

Mong đợi:
- `lsusb -v` có `iProduct ... ihc keyboard + mouse`, `iSerial ... ihc-RA` và 5 interface;
- `info` báo `USB connected`, `interfaces keyboard, consumer, system, mouse, absolute`;
- `hello` được gõ vào ô đang chọn; con trỏ laptop đi sang phải rồi xuống;
- `abstest`: con trỏ laptop nhảy tới 4 góc rồi vào giữa (trả lời `y`);
- `bench`: dòng `mouse report with ack` là thời gian tới khi laptop poll báo cáo.

Đổi profile rồi kiểm tra lại:

```bash
sudo python3 tools/gadget.py up --replace --profile A
python3 tools/gadget.py status                       # interface: keyboard, consumer, system, absolute
sudo python3 tools/gadget.py up --replace            # về RA
```

Laptop phải thấy serial `ihc-A` khi ở profile A.

Máy Windows cũng dùng được: Device Manager phải có bàn phím và chuột, không có dấu chấm than. Một người dùng từng
thấy gadget HID của board này hiện là "USB Input" kèm lỗi (feasibility A10), nên nếu gặp thì chụp lại nguyên văn.

**Ghi lại:**
- OS của laptop; kết quả `type`, `move`, `abstest`;
- đoạn `lsusb -v` (nhất là `bInterval` của từng interface);
- dòng kết quả `bench`;
- serial thấy được ở profile RA và A.

---

## B3. HDMI IN: hình, fps, bộ mã hoá

Nối iPhone → hub, HDMI của hub → **HDMI IN** của board. Mở khoá iPhone.

```bash
v4l2-ctl --list-devices                              # tìm rk_hdmirx (image Orange Pi) hoặc snps_hdmirx (mainline)
python3 tools/capture_check.py list                  # HDMI IN có thêm dòng "HDMI input; the source sends: ..."
VID=/dev/videoN                                      # N lấy từ `list`
python3 tools/capture_check.py snapshot --device $VID
python3 tools/capture_check.py probe --device $VID --seconds 10
```

`capture_check` nhận ra HDMI IN theo tên driver, khai báo EDID 1080p60 (iPhone thấy màn hình ngắt rồi nối lại) và
đọc hình qua bộ mã hoá JPEG, giống hệt `ihc serve`.

So sánh bộ mã hoá và EDID:

```bash
python3 tools/capture_check.py probe --device $VID --encoder mpp      # JPEG phần cứng (mppjpegenc), nếu image có
python3 tools/capture_check.py probe --device $VID --encoder gst      # jpegenc, phần mềm
python3 tools/capture_check.py probe --device $VID --encoder ffmpeg   # nếu đã cài ffmpeg
python3 tools/capture_check.py probe --device $VID --fps 60
v4l2-ctl -d $VID --query-dv-timings                                   # nguồn đang gửi độ phân giải nào
```

`list` cho biết nguồn gửi gì trước khi đặt EDID; `--query-dv-timings` cho biết sau. Muốn thử EDID khác thì tự đặt
bằng `v4l2-ctl -d $VID --set-edid=...` rồi chạy `probe --keep-edid` (không ghi đè EDID). Chạy `top` ở cửa sổ khác
trong lúc `probe` để xem CPU.

Mong đợi:
- ảnh chụp có màn hình iPhone (dọc, viền đen hai bên);
- sau khi đặt EDID, `--query-dv-timings` báo 1920x1080;
- `fps` gần 30, `passthrough True`, `reopens` là 0.

Nếu hình ở 4K hoặc log báo lỗi EDID: `v4l2-ctl -d $VID --set-edid=type=hdmi`, rồi rút/cắm cáp HDMI.
Nếu báo "no JPEG encoder": cài GStreamer (dòng `apt install` ở mục 0).

**Ghi lại** (cho từng bộ mã hoá):
- tên driver, `--query-dv-timings` trước và sau khi đặt EDID;
- `fps`, `interval p50/p95`, KB/khung, Mbit/s, `dropped`, `reopens`;
- CPU;
- bộ mã hoá nào có trên image (`mpp` có chạy không).

---

## B4. Bàn phím + chuột trên iPhone qua hub

Nối: cổng USB-A của hub → cáp USB-A → USB-C → cổng Type-C của board. Hub vẫn nối iPhone, HDMI và sạc như ở B3.
Gadget ở profile RA (`sudo python3 tools/gadget.py up --replace` nếu cần). Mở khoá iPhone và chọn **Allow** nếu
iOS hỏi về phụ kiện.

**1. Chuột tuyệt đối (câu hỏi quan trọng nhất):**

```bash
python3 tools/gadget.py status                       # USB state configured
python3 tools/hidtest.py --gadget "info; abstest"
```

Mong đợi:
- `info` báo `USB connected`;
- con trỏ là **chấm tròn** (AssistiveTouch). Mũi tên nghĩa là thiết bị bị nhận sai, hoặc AssistiveTouch đang tắt:
  dừng lại và sửa;
- `abstest`: con trỏ tới 4 góc rồi vào giữa (iOS trượt con trỏ tới, không nhảy tức thì). Trả lời `y` hoặc mô tả
  những gì thấy.

Ghi thêm: ngay sau khi cắm, con trỏ nằm ở đâu. Nếu nó nhảy về góc trên trái thì có một báo cáo tuyệt đối (0,0)
ngoài ý muốn: ghi lại.

**2. Chuột tương đối và bàn phím.** Mở Notes, tạo ghi chú mới, rồi:

```bash
python3 tools/hidtest.py --gadget "move 200 0; move 0 200"
python3 tools/hidtest.py --gadget 'type Hello iPhone 123 !@#$%^&*()_+-=[]{};:,./<>?'
```

Mong đợi: con trỏ đi sang phải rồi xuống; chuỗi hiện đúng từng ký tự.

**3. Vòng Caps Lock** (xác nhận iOS đã xử lý phím, không cần nhìn màn hình). Tắt "Caps Lock switches language"
trên iPhone, rồi:

```bash
python3 tools/hidtest.py --gadget "capscheck n=10"
```

Đèn Caps Lock do iOS gửi về gadget; nếu nó đổi theo mỗi lần bấm thì đây là cách kiểm tra "iOS còn nhận lệnh" rất
rẻ cho phần giám sát.

**4. Bug phím tắt:**

```bash
python3 tools/hidtest.py --gadget "trial cmd+space n=20"
```

Trả lời `y` nếu Spotlight mở, `n` nếu không. Công cụ tự đóng bằng Esc. Nếu có lần hỏng, thử lại ở profile chỉ bàn
phím (cách né bug của Aiden), rồi về RA:

```bash
sudo python3 tools/gadget.py up --replace --profile K
python3 tools/hidtest.py --gadget "trial cmd+space n=20"
sudo python3 tools/gadget.py up --replace
```

Tuỳ chọn: tổ hợp Tab+phím của **Full Keyboard Access** (Tab là phím thường nên có thể tránh được bug Cmd/Shift).
Bật Settings > Accessibility > Keyboards > Full Keyboard Access, gán Tab+H = Home trong mục Commands nếu được, rồi
`hidtest --gadget "trial tab+h n=30 close=none"`. Kiểm tra thêm: bật Full Keyboard Access có ảnh hưởng con trỏ
AssistiveTouch không.

**5. Nút Home và App Switcher.** Gán nút trong AssistiveTouch trước ([iphone-setup.md](iphone-setup.md), bước 2),
rồi mở một app và chạy:

```bash
python3 tools/hidtest.py --gadget "home"             # nút phải: phải về màn hình chính
python3 tools/hidtest.py --gadget "switcher"         # nút giữa: phải mở App Switcher
```

Ghi lại AssistiveTouch > Devices hiện mấy mục (chuột tương đối và chuột tuyệt đối là hai interface riêng), và gán
nút ở mục nào thì có tác dụng.

**6. Profile A (chỉ chuột tuyệt đối, giống bố cục của các dự án đã chạy được):**

```bash
sudo python3 tools/gadget.py up --replace --profile A
python3 tools/hidtest.py --gadget "info; abstest"
python3 tools/hidtest.py --gadget "abs 2048 2048 buttons=2; sleep 0.1; abs 2048 2048"   # nút phải qua báo cáo tuyệt đối
sudo python3 tools/gadget.py up --replace            # về RA
```

Mong đợi: `abstest` như ở bước 1; lệnh thứ ba đưa con trỏ vào giữa rồi về màn hình chính (nếu gán nút áp dụng cho
báo cáo tuyệt đối). Profile A có serial riêng (`ihc-A`), nên iOS có thể thấy nó là thiết bị mới: ghi lại iOS có
hỏi Allow lại không, và có phải gán nút lại không. Nếu A đạt mà RA không, lỗi nằm ở interface chuột tương đối cạnh
nó.

**Ghi lại:**
- kết quả `abstest` ở RA và A (y/n + mô tả); con trỏ tròn hay mũi tên; vị trí con trỏ ngay sau khi cắm;
- `type`: đúng/sai ký tự nào;
- `capscheck`: k/10;
- `trial cmd+space`: k/n ở RA (và ở K nếu có chạy); Tab+H nếu có thử;
- Home, App Switcher: được/không, qua báo cáo tương đối và tuyệt đối; số mục trong AssistiveTouch > Devices.

---

## B5. Hiệu chỉnh qua server và độ chính xác tap

Cài hộp như sản phẩm thật: gadget lúc boot và server.

```bash
sudo sh deploy/install.sh
sudo cat /var/lib/ihc/token                          # token cho web console và API
systemctl status ihc-gadget ihc
```

iPhone và board phải cùng mạng LAN (iPhone sẽ mở một trang trên board).

1. Mở `http://<ip-board>:8000`, nhập token. Máy tên `iphone`.
2. Bấm **Calibrate**. Công cụ tự mở Safari qua Spotlight. Nếu không mở được: gõ vào Safari trên iPhone link trang
   hiệu chỉnh mà web console hiện (có khoá `k`), rồi bấm **calibrate the open page**.
3. Chờ kết quả: khoảng 10 s nếu iPhone theo chuột tuyệt đối, khoảng 1 phút nếu không.

Mong đợi, ở bảng trạng thái:
- `mode: absolute` (theo kết quả B4);
- `validation`: sai số trung bình/tối đa (pt) trên các điểm kiểm tra ngẫu nhiên.

Mục tiêu: sai số tối đa ≤ 2 pt với absolute, ≤ 4 pt với relative.

Sau đó, trong web console (chế độ **Precise tap**):
- mở app có danh sách dài, vuốt 20 lần: không lần nào bị "kẹt" ở trạng thái đang kéo (nhả nút bị lỡ);
- tap 50 lần vào cùng một nút, đếm số lần trượt;
- đổi Tracking Speed lên tối đa rồi tối thiểu, tap lại 5 điểm: ở chế độ tuyệt đối, kết quả không được đổi. Xong
  thì đặt lại mức đã chọn.

**Chế độ tương đối** (bắt buộc nếu `mode: relative`; nếu còn thời gian thì chạy để có số liệu dự phòng): hiệu chỉnh
3 lần với chế độ tương đối ép buộc, rồi so `validation` giữa các lần.

```bash
curl -X POST http://<ip-board>:8000/api/devices/iphone/calibrate \
     -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
     -d '{"options": {"try_absolute": false}}'
```

Thử cả hai đầu của Tracking Speed (tối đa và tối thiểu): các hãng Trung Quốc chọn ngược nhau
([research/china-market.md](research/china-market.md) §2.5). Sai số tăng rõ giữa các lần là dấu hiệu gia tốc iOS
không lặp lại. Ghi thêm: vị trí neo ở các góc có lặp lại không (góc màn hình bo tròn). Xong thì đặt Tracking Speed
về mức đã chọn và hiệu chỉnh lại (không ép tương đối).

**Ghi lại:**
- `mode`, `validation` của từng lần;
- `abs_settle` (thời gian iOS trượt con trỏ, trong file hiệu chỉnh);
- file `/var/lib/ihc/iphone.json`, log `/var/log/ihc/ihc.jsonl`;
- số lần kẹt kéo / 20, số lần tap trượt / 50, kết quả ở hai mức Tracking Speed.

---

## B6. Độ bền: khoá máy, rút/cắm, khởi động lại

Theo dõi trạng thái của máy trên web console (`ready`, `hid_disconnected`, `no_signal`...) và log:
`sudo tail -f /var/log/ihc/ihc.jsonl`. Làm lần lượt, ghi lại trạng thái và thời gian hồi phục sau mỗi thao tác:

1. khoá iPhone, đợi 5 phút, mở khoá;
2. khoá iPhone 70 phút (quá 1 giờ của USB Restricted Mode), mở khoá; lặp với từng lựa chọn Wired Accessories;
3. rút/cắm cáp USB giữa hub và board;
4. rút/cắm cáp HDMI (pipeline phải tự chạy lại);
5. rút/cắm sạc ở cổng PD của hub (hub có reset không; hình, HID, sạc cái nào rớt);
6. rút/cắm hub khỏi iPhone;
7. khởi động lại board (`sudo reboot`): `ihc-gadget` và `ihc` phải tự lên, máy `iphone` về `ready` mà không cần làm
   gì, hiệu chỉnh cũ vẫn còn;
8. khởi động lại iPhone: HID có gõ được passcode không (chế độ **Live control** trên console).

Muốn xem trạng thái USB từng lúc bằng `hidtest`: dừng server (`sudo systemctl stop ihc`, hai tiến trình không nên
cùng ghi vào gadget), vào nhóm `ihc` một lần (`sudo usermod -aG ihc $USER`, rồi đăng xuất và đăng nhập lại), rồi:

```bash
python3 tools/hidtest.py --gadget
hid> note B6 bắt đầu
hid> watch
```

Trước mỗi thao tác: Ctrl+C, gõ `note <thao tác>`, rồi `watch` lại. Khi máy khoá, thử `move 100 0` và `type abc`.
Xong thì `sudo systemctl start ihc`.

**Ghi lại** (bảng mẫu):

```markdown
| Thao tác | Trạng thái trong lúc đó | Tự hồi phục? | Sau bao lâu | Ghi chú |
|---|---|---|---|---|
| Khoá 5 phút | | | | |
| Khoá 70 phút (Wired Accessories = ...) | | | | |
| Rút/cắm USB hub ↔ board | | | | |
| Rút/cắm HDMI | | | | |
| Rút/cắm sạc | | | | |
| Rút/cắm hub ↔ iPhone | | | | |
| Khởi động lại board | | | | |
| Khởi động lại iPhone | | | | |
```

---

## B7. Độ trễ hình

1. Mở một trang đồng hồ bấm giờ có mili giây trên iPhone. Mở web console trên laptop, để hai màn hình cạnh nhau và
   chụp ảnh cả hai bằng điện thoại khác. Hiệu hai số là độ trễ hình tới trình duyệt. Lặp 5 lần.
2. Chế độ **Live control**: di chuột. Con trỏ có hiện trong hình không?
3. Tuỳ chọn: thử pipeline khác trong server. Server chọn bộ mã hoá đầu tiên có sẵn theo thứ tự `mpp`, `gst`,
   `ffmpeg`. Muốn chạy lệnh riêng thì viết `farm.toml` theo mẫu trong [gadget.md](gadget.md), thay `input = "hdmi"`
   bằng `command = "..."` (lệnh xuất JPEG ra stdout; `{device}` được thay bằng đường dẫn thiết bị), rồi dừng
   `ihc.service` và chạy `ihc serve --config farm.toml`. Với `command`, server không tự đặt EDID: đặt trước bằng
   `v4l2-ctl -d /dev/videoN --set-edid=type=hdmi`.

Log của server có sự kiện `hdmi_input` ghi lại lệnh pipeline đang dùng.

**Ghi lại:** độ trễ (5 lần, trung bình); bộ mã hoá; con trỏ có/không trong hình; fps và Mbit/s (từ B3).

---

## B8. Chạy bền 72 giờ

Để hộp chạy như sản phẩm (service đã cài ở B5). Chạy một script SDK từ máy khác, ví dụ:

```python
import random, time
from ihc.client import Farm

phone = Farm("http://<ip-board>:8000", token="...").device("iphone")
while True:
    phone.home()
    phone.tap(random.uniform(0.1, 0.9), random.uniform(0.2, 0.8))
    print(time.strftime("%H:%M:%S"), phone.status()["state"])
    time.sleep(5)
```

Có một lần rút điện có chủ đích (cả board và sạc), rồi cắm lại.

**Ghi lại:**
- số lần mất tín hiệu và HID, và có tự hồi phục không;
- nhiệt độ board (`cat /sys/class/thermal/thermal_zone*/temp`) và iPhone (nóng tay hay không);
- % pin iPhone đầu và cuối;
- hub có reset lần nào không.

---

## D. Dự phòng: cáp CH9329

Dùng khi image của board không có chế độ device (B1 thất bại), hoặc trên một host Linux khác. Cáp CH9329: đầu
CH340 (USB-serial) cắm vào host, đầu CH9329 cắm vào cổng USB-A của hub. Hình lấy từ HDMI IN của board (B3) hoặc
từ capture card USB MS2109.

```bash
sudo apt install -y evtest
sudo usermod -aG dialout,video,input $USER           # rồi đăng xuất và đăng nhập lại
python -m ihc.hid.scan --list
PORT=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 # đường dẫn ổn định của cáp, lấy từ --list
```

### D1. Cấu hình, descriptor và giao báo cáo (chưa cần iPhone)

Cắm **cả hai đầu** cáp CH9329 vào host. Đầu HID sẽ hiện như chuột + bàn phím của host. Công cụ tự "giữ độc
quyền" thiết bị khi test, nên chuột trên màn hình host không bị ảnh hưởng.

```bash
lsusb                                            # có dòng 1a86:7523 (CH340) và 1a86:e129 (CH9329)
sudo lsusb -v -d 1a86:e129 | grep -E "bInterval|bInterfaceProtocol|wMaxPacketSize"
python -m ihc.hid.scan --port $PORT              # mong đợi: 9600 baud: OK
python tools/hidtest.py --port $PORT "info; cfg; cfg save docs/test-logs/ch9329-cfg-factory.json; bench 200"
python tools/hid_loopback.py --port $PORT list
python tools/hid_loopback.py --port $PORT all
# descriptor của CH9329 (chưa ai công bố): cần cho D3. usbhid-dump có trong gói usbutils;
# giải mã bằng `pip install hid-tools` rồi `hid-decode`
sudo usbhid-dump -d 1a86:e129 -e descriptor | tee docs/test-logs/ch9329-descriptor-mode0.txt
```

Mong đợi:
- **`cfg`:** `work_mode 0x80`, `vid 0x1a86`, `pid 0xe129`. Nếu có dòng `WARNING`, chụp nguyên văn.
- **`bench`:** báo cáo có ack mất khoảng 20–25 ms ở 9600 baud.
- **`hid_loopback rel`:** mỗi nhịp một dòng `OK`/`LOSS`, gồm số đơn vị nhận/gửi, độ trễ, khoảng cách thực giữa
  các lần gửi (`sent every`) và giữa các lần host nhận được (`arrived every`, kèm min..max). Nhịp mà phần mềm dùng
  (20 ms) phải `OK` ở cả chế độ `ack` lẫn `pipelined`.
- **Khoảng `arrived every` min..max** là jitter mà chế độ tương đối sẽ gặp (host + CH340 + poll USB). Ở nhịp 20 ms,
  mong muốn min..max nằm trong khoảng 18,5..21,5 ms. Nếu các giá trị nhảy theo bậc (ví dụ 10/20/30 ms) thì
  `bInterval` của chip không chia hết nhịp, hoặc báo cáo tới sát ranh giới poll: ghi lại.
- **`hid_loopback abs`:** `ABS_X`/`ABS_Y` đổi theo lưới đã gửi.
- **`hid_loopback keys`:** số phím nhấn bằng số ký tự, `all released: True`.
- **Descriptor:** ghi lại collection tuyệt đối nằm đâu, report ID, logical maximum (4095 hay 32767), có nút không,
  và interface chuột có phải boot (subclass 1 / protocol 2) không.

**Ghi lại:** `bInterval`; khoảng `arrived every` min..max ở từng nhịp; nhịp nhỏ nhất vẫn `OK`; độ trễ p50/max;
kết quả `abs`; file descriptor (mode 0x00, và mode 0x02 nếu làm D3 bước 2).

### D2. Baud và cấu hình

- **115200 baud:**
  1. `hidtest --port $PORT "cfg set baud=115200"`, gõ `YES`, rồi rút/cắm.
  2. `hidtest --port $PORT --baud 115200 "info; bench 200"`.
  3. `hid_loopback --port $PORT --baud 115200 rel`.
  4. Khôi phục bằng `cfg set baud=9600`. Mất liên lạc thì chạy `python -m ihc.hid.scan --port $PORT`.
- **`RESET` có áp dụng cấu hình không:**
  1. `cfg set baud=19200`, **không rút cáp**, chạy `reset`.
  2. Chạy `python -m ihc.hid.scan --port $PORT`.
  3. Khôi phục về 9600.

**Ghi lại:** `bench` ở hai mức baud; `RESET` có áp dụng baud mới không.

### D3. CH9329 trên iPhone

Cắm đầu CH9329 vào cổng USB-A của hub (thay cho cáp của gadget), đầu CH340 vào board.

1. Chạy các bước 1–5 của **B4** với `--port $PORT` thay cho `--gadget` (công cụ dùng chung lệnh), bỏ qua phần đổi
   profile gadget.
2. Nếu `abstest` không chạy: đổi sang chế độ chỉ chuột (`hidtest --port $PORT "cfg set work_mode=2"`, rút/cắm), chạy
   lại `abstest`, dump lại descriptor, rồi khôi phục `cfg set work_mode=0`. Descriptor gộp bàn phím có thể là
   nguyên nhân.
3. Bug phím tắt: chạy lại `trial cmd+space n=20` ở chế độ chỉ bàn phím (`cfg set work_mode=1`, rút/cắm), rồi khôi
   phục `cfg set work_mode=0`. Tuỳ chọn: thử **hai CH9329**, một chỉ bàn phím, một chỉ chuột.
4. Hiệu chỉnh như **B5**. Nếu đã cài service ở B5, dừng server và gỡ gadget trước (`sudo systemctl stop ihc
   ihc-gadget`), rồi chạy `ihc serve --auto` từ `.venv`: không có gadget thì server tự ghép cáp CH9329 với HDMI IN
   của board (hoặc với capture card duy nhất). Xong thì `sudo systemctl start ihc`.
5. Khoá máy: chạy `hidtest --port $PORT` rồi `watch` (như ở B6), thử `move 100 0` khi máy đang khoá. Ghi lại chip
   trả gì khi phía USB không được iPhone nhận (`E6`, `00` hay im lặng).

Chỉ một tiến trình được mở cổng serial tại một thời điểm: tắt `ihc serve` khi dùng `hidtest`.

**Ghi lại:** như B4 và B5, thêm work mode đã dùng và phản hồi của chip khi máy khoá.

---

## X. Thí nghiệm thêm

- **X1. Phím media:** `hidtest --gadget "media volume_up; media mute; acpi sleep"`. **Ghi lại:** phím nào có tác
  dụng.
- **X2. Đối chiếu với Sipeed NanoKVM-Go** (59–89 USD, một dây USB-C, chế độ chuột tuyệt đối cho iPhone 15/16/17):
  đo sai số tap và độ trễ hình trên cùng iPhone, so với hộp.

---

## 9. Gửi lại những gì

1. Toàn bộ `docs/test-logs/`, cộng `/var/lib/ihc/iphone.json` và `/var/log/ihc/ihc.jsonl` của hộp (commit lên
   nhánh hoặc nén gửi).
2. `docs/test-logs/environment.md`:

```markdown
| Mục | Giá trị |
|---|---|
| Model iPhone / iOS | |
| Board (Orange Pi 5 Plus, bản) | |
| Image / kernel (`uname -a`) | |
| UDC (`ls /sys/class/udc`) | |
| Driver HDMI IN / bộ mã hoá JPEG | |
| Hub USB-C (hãng, model) | |
| Sạc (W) | |
| Cáp USB-A → USB-C (hãng, dài) | |
| (Dự phòng) Cáp CH9329 (link mua, `lsusb`) | |
| (Dự phòng) Capture card (`lsusb`, chip) | |
| Tracking Speed / Pointer Size / Color | |
| Wired Accessories | |
```

3. Bảng Q1–Q9: có/không + ghi chú ngắn.

## 10. Sự cố thường gặp

| Hiện tượng | Xử lý |
|---|---|
| `ls /sys/class/udc` không in gì | cổng Type-C đang ở chế độ host với image này: thử image của Orange Pi; không được thì dùng cáp CH9329 (mục D) |
| `gadget.py up` báo controller đang được gadget khác dùng | image chạy gadget riêng (thường là ADB); `status` in tên nó; dừng nó rồi `up` lại |
| `status` đứng ở `not attached` | sai cáp hoặc sai cổng: dùng cáp USB-A → USB-C, và cổng Type-C cạnh các cổng USB 3 |
| `info` báo NOT connected trên iPhone | iPhone đang khoá, hoặc đang hỏi cho phép phụ kiện: mở khoá và chọn Allow |
| `hidtest --gadget` báo permission | chạy `gadget.py up` bằng `sudo` từ chính user đó (nó giao node cho user đã gọi sudo); sau khi cài service thì vào nhóm `ihc` |
| không có thiết bị video `rk_hdmirx` | kernel này thiếu driver HDMI IN: dùng image của Orange Pi |
| trạng thái video "no JPEG encoder" | cài GStreamer: `sudo apt install gstreamer1.0-tools gstreamer1.0-plugins-good` |
| hình 4K, hoặc log báo lỗi EDID | `v4l2-ctl -d /dev/videoN --set-edid=type=hdmi`, rồi rút/cắm cáp HDMI |
| Hiệu chỉnh báo "page did not load" | iPhone chưa mở được trang: kiểm tra cùng mạng, gõ tay địa chỉ trong Safari |
| (CH9329) `permission denied` khi mở cổng serial / input | vào nhóm `dialout`, `video`, `input`, rồi đăng xuất và đăng nhập lại |
| (CH9329) `in use by another process` | đang có `hidtest` hoặc `ihc serve` khác mở cổng; hoặc ModemManager (`sudo systemctl stop ModemManager`) |
| (CH9329) `nothing came back` | đầu HID chưa có nguồn (chưa cắm vào iPhone/hub/host), sai cổng, sai baud (chạy `scan`) |
| (CH9329) `baud rate is probably wrong` | `python -m ihc.hid.scan --port $PORT`, rồi dùng `--baud` theo kết quả |
| (CH9329) lệnh chuột báo `EXEC_ERROR` | iPhone chưa nhận phụ kiện: mở khoá, cho phép phụ kiện, kiểm tra `info` |
| (Capture card) capture đen / không có khung | hub không xuất DisplayPort, hoặc card chưa nhận tín hiệu; thử cáp HDMI khác, thử `--fourcc YUYV` |
