# Kiến trúc

## 1. Mục tiêu thiết kế

1. **Dùng được như một thư viện cho automation:** tap, vuốt, gõ, phím tắt bằng toạ độ chuẩn hoá (0..1) trên màn
   hình iPhone. Không phụ thuộc capture card, độ phân giải hay model máy.
2. **Con trỏ ít sai số, không mất lệnh:** mục tiêu 95% lần tap sai lệch không quá 4 pt (khoảng 5 px ở khung
   1080p). Không lệnh nào được mất mà không ai biết.
3. **Điều khiển từ xa mượt:** xem màn hình trực tiếp trên trình duyệt, điều khiển như ngồi trước máy.
4. **Nhiều máy trên một host**, thêm host khi đầy. Không tầng nào tốn CPU khi không ai cần kết quả.
5. **Tự hồi phục:** rút/cắm cáp, mất tín hiệu, máy khoá. Trạng thái luôn rõ ràng.
6. **Không có phần cứng vẫn phát triển được:** mọi tầng chạy trên mô phỏng qua đúng interface thật.

Không dùng thị giác máy: hệ thống không nhận diện hình ảnh. Video chỉ để người xem và để lưu ảnh chụp.

## 2. Các tầng

```
automation (SDK / REST / WS)             web console (xem live, điều khiển trực tiếp, chạm chính xác)
        │                                      │
┌───────▼──────────────────────────────────────▼─────────────────────────────┐
│ api          FastAPI: REST + WebSocket, MJPEG passthrough, trang hiệu chỉnh   │
│ registry     cấu hình farm (TOML), ghép serial↔capture theo cổng USB          │
│ device       IPhoneDevice: khoá mỗi máy, trạng thái, monitor, tự mở lại cổng  │
│ calibration  hiệu chỉnh con trỏ qua trang web trên Safari của iPhone         │
│ input        PointerModel (tuyệt đối / neo góc + chạy từng trục), Keyboard    │
│ hid          CH9329Backend (serial), cấu hình chip, dò baud, chip giả         │
│ video        capture V4L2 giữ nguyên JPEG của card, hình học vùng màn hình    │
└──────────────────────────────────────────────────────────────────────────────┘
sim: iPhone mô phỏng (UI, con trỏ, Safari, HDMI + MJPEG) cắm vào đúng interface của hid và video
```

**Interface ranh giới:**
- **HID:** CH9329 thật, bridge ESP32 và chip giả đều nói **cùng giao thức serial**, nên dùng chung một
  driver.
- **Video:** `FrameSource.latest(newer_than, timeout) -> Frame`. `Frame` giữ nguyên byte JPEG của card và
  chỉ giải mã khi thật sự cần (chụp ảnh cắt vùng, thumbnail).

## 3. Con trỏ không cần nhìn

### 3.1 Hai chế độ, tự phát hiện

| Chế độ | Khi nào | Cách làm | Trên mô phỏng |
|---|---|---|---|
| **Tuyệt đối** | iPhone theo báo cáo chuột tuyệt đối (dự án Aiden cho thấy iOS nhận qua USB; phải kiểm chứng với CH9329, bài T1) | một báo cáo đặt con trỏ đúng chỗ, chờ khoảng 80 ms cho iOS trượt con trỏ tới, rồi click | sai số ≤ 0,3 pt, khoảng 0,15 s/tap |
| **Tương đối** | iPhone chỉ theo chuột tương đối (có thể xảy ra với CH9329; gần như chắc chắn với Bluetooth) | neo góc gần nhất rồi chạy từng trục (3.2) | sai số ≤ 1 pt, khoảng 1,2–1,5 s/tap |

**Hiệu chỉnh** quyết định chế độ:
1. Host mở một trang web trên Safari của iPhone (qua Spotlight, hoặc người dùng gõ địa chỉ).
2. Trang báo lại toạ độ (`clientX/Y`) của từng cú click.
3. Host thử báo cáo tuyệt đối trước. Nếu click rơi đúng chỗ, chỉ cần đo bản đồ lưới 0..4095 sang màn hình
   (vài giây).
4. Nếu không, host đo mô hình tương đối (khoảng 1 phút).

Hiệu chỉnh không bao giờ click "mù":
- trang đánh số (`seq`), đóng dấu thời gian (`t`) mọi sự kiện và gửi `hello` định kỳ (heartbeat). Một sự kiện
  trang tạo ra *sau* cú click mà tới trước click đó chứng minh click đã trượt; thiếu số thứ tự (mất sự kiện)
  hoặc trả lời muộn thì dừng hiệu chỉnh;
- lúc tìm trang, con trỏ không bao giờ xuống quá giới hạn (đầu trang không thấp hơn 20% màn hình), và mỗi
  bước được tính để cả con trỏ nhanh nhất có thể vẫn ở trên giới hạn đó;
- mỗi đoạn chạy khi đo chỉ được gửi nếu trường hợp xấu nhất vẫn dừng cách mép trang 6%;
- kết quả được kiểm tra bằng các tap từ giữa ra ngoài, lệch quá 3 pt thì không nhận.

### 3.2 Chế độ tương đối: neo góc + chạy từng trục

1. **Neo:** gửi các báo cáo (±127, ±127) để dồn con trỏ vào **góc gần mục tiêu nhất**. Con trỏ dừng ở mép
   màn hình nên vị trí sau khi neo là biết chắc.
2. **Chạy từng trục:** trục X trước, Y sau. Mỗi trục gồm một đoạn bước lớn và một đoạn bước nhỏ. Cỡ bước do
   hiệu chỉnh chọn cho từng máy: bước lớn đi khoảng 20 pt, bước nhỏ khoảng 1 pt mỗi báo cáo, bất kể Tracking
   Speed. Mỗi đoạn:
   - bắt đầu **từ trạng thái đứng yên** (nghỉ ít nhất 0,1 s);
   - gửi đều **một báo cáo mỗi 20 ms**, tính từ lúc bắt đầu gửi báo cáo này tới lúc bắt đầu gửi báo cáo
     sau.

   Gia tốc của iOS phụ thuộc vận tốc. Vận tốc cố định thì quãng đường của một đoạn chỉ phụ thuộc số báo cáo:
   d(n) = a·n + b.
3. **Mô hình riêng cho từng hướng:** phải, trái, xuống, lên. Thêm vị trí thật sau khi neo ở mỗi mép (iOS có
   thể kẹp con trỏ cách mép 1 pt) và số báo cáo neo tối thiểu (nhân đôi để có biên an toàn).
4. **Neo lại trước mỗi thao tác**, nên sai số không cộng dồn.

**Giới hạn của chế độ tương đối:** quãng đường phụ thuộc vận tốc, nên **jitter thời gian** ở bất kỳ đâu trên
đường đi đều thành sai số vị trí. Trên mô phỏng, lệch 1 ms ở một báo cáo bước lớn làm lệch khoảng 0,75 pt.
Các nguồn jitter và cách xử lý:

| Nguồn | Xử lý |
|---|---|
| Lịch luồng của host (Python, GIL) | Đặt `sys.setswitchinterval` 1 ms (mặc định 5 ms). Đo thời điểm bắt đầu gửi **và** thời điểm ghi xong từng báo cáo; lệch quá **1,5 ms** thì làm lại cả thao tác từ neo. |
| USB-serial (CH340) | Không thấy được từ host. Bài T0 đo bằng `tools/hid_loopback.py` (khoảng cách thực giữa các báo cáo khi tới host). |
| Chu kỳ link tới iPhone: `bInterval` của USB, connection interval của Bluetooth | Nhịp chạy phải là **bội số** của chu kỳ link. Ở nhịp 20 ms trên link Bluetooth 15 ms, khoảng cách iOS thấy xen kẽ 15/30 ms: trên mô phỏng sai số lên tới 30 pt; ở nhịp 30 ms còn dưới 1 pt. |

**Bridge ESP32 tự tạo nhịp:** lệnh riêng `SEND_MS_REL_RUN` (0x30) giao cả một đoạn chạy cho chip. Chip phát
từng báo cáo theo đồng hồ của nó, nên jitter của host và USB-serial không còn tới iPhone. Bridge báo chu kỳ
link trong `GET_INFO` (byte 6–7), và hiệu chỉnh chọn nhịp là bội số nhỏ nhất của chu kỳ đó mà không dưới
20 ms (Bluetooth 15 ms thành 30 ms). Nếu link Bluetooth đổi chu kỳ sau khi hiệu chỉnh, trạng thái thiết bị
báo cần hiệu chỉnh lại.

Với CH9329, `bInterval` của chip chưa biết: bài T0 đo nó. Nếu jitter sau cổng serial vẫn lớn thì:
- hạ tốc độ bước lớn (`coarse_target`);
- dùng bridge ESP32 ở chế độ USB (tự tạo nhịp, `bInterval` 1 ms);
- tốt nhất là chế độ tuyệt đối, vốn không bị ảnh hưởng.

### 3.3 Không mất lệnh

| Lớp | Cơ chế |
|---|---|
| Từng báo cáo | Chip trả **ack** cho mọi báo cáo. Driver chờ ack (500 ms theo tài liệu WCH) và báo lỗi rõ ràng. |
| Báo cáo trạng thái (nút, phím, vị trí tuyệt đối) | Mang toàn bộ trạng thái nên gửi lại là an toàn. Được gửi lại khi timeout hoặc khi chip báo khung hỏng (E1/E2/E4). |
| Báo cáo di chuyển tương đối | Không gửi lại mù (có thể đã được thực thi). Thao tác được **làm lại từ đầu, kể cả bước neo góc**. |
| Đoạn chạy | Gửi liên tục không chờ từng ack (chỉ đường serial giới hạn nhịp), nhưng **mọi ack được thu và kiểm** ở cuối đoạn. Thiếu hay lỗi một ack thì làm lại từ neo. |
| Ack đến muộn | Sau một lần timeout hoặc mất ack, lần trao đổi kế tiếp gửi `GET_INFO` trước và bỏ mọi phản hồi tới trước câu trả lời của nó. Chip trả lời theo thứ tự, nên ack muộn không bao giờ bị tính nhầm cho lệnh sau. |
| Nhịp | Thời điểm gửi và thời điểm ghi xong từng báo cáo được đo. Lệch quá 1,5 ms thì đoạn đó bị coi là hỏng và làm lại, vì lệch nhịp nghĩa là lệch quãng đường. Với bridge ESP32, chip tự tạo nhịp. |
| Phím và nút | Luôn nhả mọi phím và nút khi kết thúc. Thao tác nào lỗi giữa chừng thì nhả hết (bàn phím, phím media, chuột tương đối **và** chuột tuyệt đối, vì hai loại báo cáo giữ trạng thái nút riêng). Nếu không xác nhận được việc nhả, lần neo sau sẽ nhả trước rồi mới di chuyển. |
| Thứ tự | Mỗi thiết bị có một khoá. Thao tác của automation, của người điều khiển trực tiếp và hiệu chỉnh chạy lần lượt, không xen nhau. |

Giới hạn trung thực: không có thị giác, "đã giao" nghĩa là **chip đã nhận và thực thi**. Để kiểm chứng tầng USB
(chip có mất hay gộp báo cáo khi gửi tới host không), bài T0 dùng `tools/hid_loopback.py`, đọc lại chính xác
những gì một host Linux nhận được. Firmware ESP32 (chế độ USB) có thể báo "host USB đã lấy báo cáo", chặt
hơn CH9329.

## 4. Điều khiển từ xa và stream

- **MJPEG passthrough:**
  - capture card đã nén JPEG sẵn, luồng live gửi thẳng các byte đó cho trình duyệt, không giải mã rồi nén
    lại;
  - mỗi client chỉ có tối đa một khung đang gửi dở; client chậm bị bỏ khung, không xếp hàng;
  - trễ dự kiến 150–250 ms trong LAN, khoảng 15–50 Mbit/s mỗi người xem.
- **Điều khiển trực tiếp (kiểu KVM):**
  - chế độ tuyệt đối: vị trí chuột trên khung hình đi thẳng thành báo cáo tuyệt đối;
  - chế độ tương đối: trình duyệt khoá con trỏ (Pointer Lock) và gửi chuyển động tương đối;
  - server gộp chuyển động thành tối đa một báo cáo mỗi khoảng 16–20 ms, còn thay đổi nút gửi ngay nên không
    lỡ click;
  - bàn phím gửi theo mã phím vật lý (`KeyboardEvent.code`), không phụ thuộc layout của máy người điều
    khiển.
- **Chạm chính xác:** click trên khung hình thành tap tại đúng điểm đó (qua hình học vùng màn hình); kéo thành
  vuốt.
- Truy cập ngoài LAN nên chuyển sang H.264 (VAAPI/QSV trên x86). Việc này để sau.

## 5. Độ bền

- **Monitor mỗi 2 s** cho mỗi thiết bị: `GET_INFO` (chip có trả lời, phía USB có được iPhone nhận) và khung
  hình (có tín hiệu, không đen). Trạng thái:
  - `ready`, `busy`;
  - `hid_disconnected`: máy khoá, popup phụ kiện, hoặc cáp;
  - `hid_offline`: không nói chuyện được với chip; tự mở lại cổng;
  - `no_signal`.
- **Định danh ổn định:** cấu hình dùng `/dev/serial/by-path` và `/dev/v4l/by-path`, gắn với cổng USB vật lý.
- **Log:** JSON lines có timestamp cho mọi thao tác, lỗi và (khi bật) từng khung serial.

## 6. Quy mô

Nút thắt là **video**, không phải HID.

| Host | Ước lượng (cần đo ở T8) |
|---|---|
| Raspberry Pi 4 | 1 máy (các cổng USB 2 dùng chung một bus) |
| Raspberry Pi 5 | 1–2 máy |
| Mini PC x86 + card PCIe USB nhiều controller | mỗi controller USB độc lập khoảng 1 card MS2109 (băng thông isochronous); thêm card PCIe để thêm máy |

- Ưu tiên **MS2109** cho MJPEG passthrough. MS2130 ở cổng USB 3 chỉ xuất YUV thô (theo firmware), mất lợi thế
  passthrough.
- Thêm host khi đầy: SDK `Farm(url1, url2, ...)` gom thiết bị của nhiều host.
- Dài hạn: board nhúng cho từng máy (SoC Linux có USB gadget HID + HDMI-to-CSI), host chỉ còn điều phối.

## 7. Phần cứng HID: vì sao hai dòng máy khác nhau

- **iPhone USB-C (15 trở lên):**
  - cổng USB-C của iPhone vừa xuất hình (DisplayPort Alt Mode) vừa làm USB host;
  - một hub cho ra HDMI và một cổng USB-A cùng lúc;
  - chip CH9329 cắm vào cổng USB-A đó, iPhone thấy một bộ chuột + bàn phím có dây;
  - một dây lo cả hình lẫn điều khiển.
- **iPhone Lightning:**
  - chỉ có một cổng, và cổng đó phải dành cho Lightning Digital AV Adapter để lấy hình HDMI;
  - cổng Lightning phụ trên adapter **chỉ để sạc**, không có dữ liệu USB;
  - chưa có adapter nào cho HDMI và USB host chạy cùng lúc một cách đáng tin cậy;
  - vì vậy điều khiển phải đi đường khác: **Bluetooth**. CH9329 chỉ có USB nên không dùng được; ESP32 có
    Bluetooth LE, đóng vai bàn phím + chuột Bluetooth;
  - firmware ESP32 nói **đúng giao thức serial của CH9329**, nên phần mềm host không phải sửa gì.
- **Vì sao không dùng Bluetooth cho cả USB-C?** Có dây ổn định hơn:
  - không phải ghép đôi;
  - không nhiễu sóng khi nhiều máy đặt cạnh nhau;
  - trễ thấp hơn;
  - iOS nhận chuột tuyệt đối qua USB (theo Aiden), còn qua Bluetooth thì chưa chắc.
- **Lựa chọn tốt nhất về lâu dài:** dùng một nền ESP32-S3 cho cả hai dòng (USB HID cho USB-C, Bluetooth cho
  Lightning, cùng giao thức). Nó cho tín hiệu "host đã nhận" chặt hơn, đổi được profile chỉ bàn phím để né bug
  phím tắt (CH9329 phải rút nguồn mới đổi được), và có thể tự tạo nhịp ngay trên chip. CH9329 vẫn là lựa chọn
  nhanh nhất để bắt đầu vì mua về là dùng được.
