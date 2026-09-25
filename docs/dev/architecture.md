# Kiến trúc

## 1. Mục tiêu thiết kế

1. **Dùng được như một thư viện cho automation:** tap, vuốt, gõ, phím tắt bằng toạ độ chuẩn hoá (0..1) trên màn
   hình iPhone. Không phụ thuộc nguồn hình, độ phân giải hay model máy.
2. **Con trỏ ít sai số, không mất lệnh:** mục tiêu 95% lần tap sai lệch không quá 4 pt (khoảng 5 px ở khung
   1080p). Không lệnh nào được mất mà không ai biết.
3. **Điều khiển từ xa mượt:** xem màn hình trực tiếp trên trình duyệt, điều khiển như ngồi trước máy.
4. **Mỗi iPhone một hộp**, thêm hộp để thêm máy; SDK gom nhiều hộp thành một farm. Không tầng nào tốn CPU khi
   không ai cần kết quả.
5. **Tự hồi phục:** rút/cắm cáp, mất tín hiệu, máy khoá, khởi động lại hộp. Trạng thái luôn rõ ràng.
6. **Không có phần cứng vẫn phát triển được:** mọi tầng chạy trên mô phỏng qua đúng interface thật.

Không dùng thị giác máy: hệ thống không nhận diện hình ảnh. Video chỉ để người xem và để lưu ảnh chụp.

Chỉ hỗ trợ iPhone có cổng USB-C: iPhone 15 trở lên, trừ 16e và 17e (hai máy này không xuất hình qua USB-C).

## 2. Phần cứng: hộp all-in-one

Mỗi iPhone đi với **một Orange Pi 5 Plus**. Board làm cả ba việc:

- **là bàn phím và chuột của iPhone:** cổng Type-C USB 3.0/DP của board (cạnh các cổng USB 3, không phải cổng
  Type-C chỉ để cấp nguồn) chạy như một thiết bị USB (Linux USB gadget);
- **thấy màn hình iPhone** qua cổng **HDMI IN** của chính board;
- **chạy server:** API, web console, trang hiệu chỉnh.

```
 sạc ≥ 30 W ──► hub USB-C (cổng PD vào) ◄──── một dây USB-C ────► iPhone 15 trở lên
                   │ HDMI                │ USB-A
                   │                     │ cáp USB-A → USB-C
                   ▼                     ▼
             ┌── Orange Pi 5 Plus ──────────────────────────────────────┐
             │  HDMI IN            cổng Type-C USB 3.0/DP (USB gadget)  │
             │  server: API, web console, hiệu chỉnh                    │
             └── nguồn 5 V / 4 A riêng ─────────────── Ethernet ────────┘
```

Chi tiết linh kiện, cách nối, cài đặt và xử lý sự cố: [orange-pi-box.md](../guide/orange-pi-box.md).

**Chưa kiểm chứng trên đúng board này:** cổng Type-C có chạy chế độ device được không. Device tree mainline khai
báo cổng này là dual-role, và Orange Pi dùng nó để nạp image và cho ADB, nên nhiều khả năng là được; bài B1–B2 của
[phase0-checklist.md](../research/phase0-checklist.md) trả lời câu hỏi này.

**Phương án dự phòng** (image của board không có chế độ device, hoặc một host Linux khác): cáp CH9329 (đầu CH340
cắm vào host, đầu CH9329 cắm vào cổng USB-A của hub) làm bàn phím và chuột; hình lấy từ HDMI IN của board hoặc từ
capture card USB MS2109. Phần mềm hỗ trợ cả hai.

## 3. Các tầng

```
automation (SDK / REST / WS)             web console (xem live, điều khiển trực tiếp, chạm chính xác)
        │                                      │
┌───────▼──────────────────────────────────────▼──────────────────────────────┐
│ api          FastAPI: REST + WebSocket, MJPEG passthrough, trang hiệu chỉnh │
│ registry     cấu hình farm (TOML), hoặc tự tìm rig: gadget + HDMI IN,       │
│              CH9329 ↔ capture card theo hub USB                             │
│ device       IPhoneDevice: khoá mỗi máy, trạng thái, monitor, tự mở lại HID │
│ calibration  hiệu chỉnh con trỏ qua trang web trên Safari của iPhone        │
│ input        PointerModel (tuyệt đối / neo góc + chạy từng trục), Keyboard  │
│ hid          GadgetBackend (/dev/hidgN), CH9329Backend (serial), chip giả   │
│ video        V4L2: JPEG của capture card, hoặc JPEG từ pipeline HDMI IN     │
└─────────────────────────────────────────────────────────────────────────────┘
sim: iPhone mô phỏng (UI, con trỏ, Safari, HDMI + MJPEG) cắm vào đúng interface của hid và video
```

**Interface ranh giới:**
- **HID:** `GadgetBackend` (gadget của hộp) và `CH9329Backend` (cáp CH9329, và chip giả của mô phỏng) có **cùng
  API**: lưới tuyệt đối 4096×4096 của CH9329, bitmask phím media và phím hệ thống (gadget không có phím hệ thống:
  kernel chỉ cho 4 chức năng HID). Gadget tự đổi toạ độ sang
  0..32767 khi gửi. Nhờ vậy con trỏ, bàn phím, hiệu chỉnh và API dùng chung một đường code.
- **Video:** `FrameSource.latest(newer_than, timeout) -> Frame`. `Frame` giữ nguyên byte JPEG (của capture card,
  hoặc của bộ mã hoá JPEG đọc HDMI IN) và chỉ giải mã khi thật sự cần (chụp ảnh cắt vùng, thumbnail).

## 4. Con trỏ không cần nhìn

### 4.1 Hai chế độ, tự phát hiện

| Chế độ | Khi nào | Cách làm | Trên mô phỏng |
|---|---|---|---|
| **Tuyệt đối** | iPhone theo báo cáo chuột tuyệt đối. Qua USB, trên iOS 26, nhiều dự án đã xác nhận điều này (cần AssistiveTouch). Với gadget của hộp: bài B4; với CH9329: bài D3 | một báo cáo đặt con trỏ đúng chỗ, chờ iOS trượt con trỏ tới (hiệu chỉnh đo thời gian này, mặc định 0,25 s), rồi click | sai số ≤ 0,3 pt, khoảng 0,15 s/tap |
| **Tương đối** | iPhone không theo chuột tuyệt đối (ví dụ iOS không nhận descriptor của CH9329) | neo góc gần nhất rồi chạy từng trục (4.2) | sai số ≤ 1 pt, khoảng 1,2–1,5 s/tap |

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

### 4.2 Chế độ tương đối: neo góc + chạy từng trục

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
| USB-serial (CH340), chỉ với cáp CH9329 | Không thấy được từ host. Bài D1 đo bằng `src/ihc/tools/hid_loopback.py` (khoảng cách thực giữa các báo cáo khi tới host). Gadget không có chặng này: báo cáo được ghi thẳng vào `/dev/hidgN`. |
| Nhịp poll USB (`bInterval`) | Nhịp chạy 20 ms nên là bội số của `bInterval`. Gadget: đọc `bInterval` ở bài B2 (`lsusb -v` trên laptop). CH9329: bài D1. |

Nếu jitter vẫn lớn:
- hạ tốc độ bước lớn (`coarse_target`);
- với cáp CH9329: chuyển sang gadget của hộp (bỏ được chặng USB-serial);
- tốt nhất là chế độ tuyệt đối, vốn không bị ảnh hưởng.

### 4.3 Không mất lệnh

| Lớp | Cơ chế |
|---|---|
| Từng báo cáo | Mọi báo cáo đều được xác nhận, chờ tối đa 500 ms rồi báo lỗi rõ ràng. **Gadget:** báo cáo tính là đã giao khi iPhone đã lấy nó (host đã poll); mỗi interface chỉ có một báo cáo đang chờ. **CH9329:** chip trả **ack** cho mọi báo cáo (timeout 500 ms theo tài liệu WCH). |
| Báo cáo trạng thái (nút, phím, vị trí tuyệt đối) | Mang toàn bộ trạng thái nên gửi lại là an toàn. Được gửi lại khi timeout, hoặc khi CH9329 báo khung hỏng (E1/E2/E4). |
| Báo cáo di chuyển tương đối | Không gửi lại mù (có thể đã được thực thi). Thao tác được **làm lại từ đầu, kể cả bước neo góc**. |
| Đoạn chạy | Gửi liên tục không chờ từng xác nhận, nhưng **mọi xác nhận được thu và kiểm** ở cuối đoạn. Thiếu hay lỗi một xác nhận thì làm lại từ neo. Với gadget, báo cáo sau chỉ được ghi khi iPhone đã lấy báo cáo trước trên cùng interface. |
| Xác nhận đến muộn | CH9329: sau một lần timeout hoặc mất ack, lần trao đổi kế tiếp gửi `GET_INFO` trước và bỏ mọi phản hồi tới trước câu trả lời của nó. Chip trả lời theo thứ tự, nên ack muộn không bao giờ bị tính nhầm cho lệnh sau. Gadget: báo cáo được lấy muộn chỉ được đếm (`late_replies`), không tính cho báo cáo khác. |
| Nhịp | Thời điểm gửi và thời điểm ghi xong từng báo cáo được đo. Lệch quá 1,5 ms thì đoạn đó bị coi là hỏng và làm lại, vì lệch nhịp nghĩa là lệch quãng đường. |
| Phím và nút | Luôn nhả mọi phím và nút khi kết thúc. Thao tác nào lỗi giữa chừng thì nhả hết (bàn phím, phím media, chuột tương đối **và** chuột tuyệt đối, vì hai loại báo cáo giữ trạng thái nút riêng). Nếu không xác nhận được việc nhả, lần neo sau sẽ nhả trước rồi mới di chuyển. |
| Thứ tự | Mỗi thiết bị có một khoá. Thao tác của automation, của người điều khiển trực tiếp và hiệu chỉnh chạy lần lượt, không xen nhau. |

Giới hạn trung thực: không có thị giác, nên "đã giao" chỉ có nghĩa là:
- **gadget:** iPhone đã lấy báo cáo ở tầng USB;
- **CH9329:** chip đã nhận và thực thi lệnh (yếu hơn gadget).

Không có mức "iOS đã xử lý". Cách kiểm tra rẻ nhất là **vòng Caps Lock** (`hidtest capscheck`): đèn Caps Lock do
iOS gửi về, nên nó đổi theo phím nghĩa là iOS đã xử lý báo cáo bàn phím. Với cáp CH9329, bài D1 dùng
`src/ihc/tools/hid_loopback.py` để đọc lại chính xác những gì một host Linux nhận được (chip có mất hay gộp báo cáo không).

## 5. Điều khiển từ xa và stream

- **MJPEG passthrough:**
  - hình tới server đã là JPEG: capture card nén sẵn, còn với HDMI IN thì bộ mã hoá JPEG của hộp nén. Luồng live
    gửi thẳng các byte đó cho trình duyệt, không giải mã rồi nén lại;
  - mỗi client chỉ có tối đa một khung đang gửi dở; client chậm bị bỏ khung, không xếp hàng;
  - khoảng 15–50 Mbit/s mỗi người xem (ước lượng); trễ với capture card MS2109 dự kiến 150–250 ms trong LAN, trễ
    với HDMI IN của hộp chưa đo (bài B7).
- **HDMI IN của hộp:**
  - khi khởi động, server khai báo với iPhone một màn hình 1080p60 (EDID), nên iPhone mirror ở 1920×1080 thay vì
    4K;
  - khung thô được nén JPEG theo thứ tự ưu tiên: bộ mã hoá phần cứng `mppjpegenc` qua GStreamer nếu image của board
    có; `jpegenc` (GStreamer) hoặc ffmpeg, phần mềm; cuối cùng là bộ đọc riêng của hộp (`ihc.video.v4l2`: đọc thẳng
    driver V4L2, nén bằng OpenCV có sẵn trong gói), nên board không cài gì, không có internet vẫn chạy. Khi bộ mã
    hoá chậm thì khung cũ bị bỏ chứ không bị trễ;
  - EDID 1080p60 được ghi thẳng vào driver (không cần v4l-utils);
  - pipeline tự khởi động lại khi mất tín hiệu hoặc đổi độ phân giải.
- **Điều khiển trực tiếp (kiểu KVM):**
  - chế độ tuyệt đối: vị trí chuột trên khung hình đi thẳng thành báo cáo tuyệt đối;
  - chế độ tương đối: trình duyệt khoá con trỏ (Pointer Lock) và gửi chuyển động tương đối;
  - server gộp chuyển động thành tối đa một báo cáo mỗi khoảng 16–20 ms, còn thay đổi nút gửi ngay nên không
    lỡ click;
  - bàn phím gửi theo mã phím vật lý (`KeyboardEvent.code`), không phụ thuộc layout của máy người điều
    khiển.
- **Chạm chính xác:** click trên khung hình thành tap tại đúng điểm đó (qua hình học vùng màn hình); kéo thành
  vuốt.
- Truy cập ngoài LAN nên chuyển sang H.264. Việc này để sau.

## 6. Độ bền

- **Monitor mỗi 2 s** cho mỗi thiết bị: trạng thái HID và khung hình (có tín hiệu, không đen).
  - Gadget: trạng thái USB của controller (`configured` nghĩa là iPhone đã nhận gadget).
  - CH9329: `GET_INFO` (chip có trả lời, phía USB có được iPhone nhận).
- **Trạng thái:**
  - `ready`, `busy`;
  - `hid_disconnected`: máy khoá, popup phụ kiện, hoặc cáp;
  - `hid_offline`: không mở được HID (chưa có gadget, hoặc chip không trả lời); tự mở lại;
  - `no_signal`.
- **Khởi động cùng hộp:** `ihc-gadget.service` dựng gadget lúc boot (khi `/sys/class/udc` có controller), rồi
  `ihc.service` chạy `ihc serve --auto`. Cả hai do `sudo sh packaging/install.sh` cài.
- **Định danh ổn định:**
  - hộp: thiết bị tên `iphone-` cộng 6 ký tự cuối số serial của board (ví dụ `iphone-b40d9e`), nên mỗi hộp
    trong farm có tên riêng; muốn tự đặt thì ghi `IHC_PHONE_ID=...` vào `/etc/default/ihc`;
  - cáp CH9329: cấu hình dùng `/dev/serial/by-path` và `/dev/v4l/by-path`, gắn với cổng USB vật lý.
- **Log:** JSON lines có timestamp cho mọi thao tác, lỗi và (khi bật) từng báo cáo HID.

## 7. Quy mô

- **Mỗi iPhone một hộp.** Thêm máy là thêm hộp. Mỗi hộp tự quảng bá trong mạng LAN (mDNS); SDK
  `Farm.discover()` hoặc `Farm(url1, url2, ...)` gom thiết bị của nhiều hộp.
- Trên hộp, hình không đi qua USB (HDMI IN nằm trên board), nên không vướng giới hạn băng thông USB của capture
  card.

**Phương án dự phòng: nhiều máy trên một host Linux** (cáp CH9329 + capture card MS2109). Khi đó nút thắt là
**video**, không phải HID:

| Host | Ước lượng (chưa đo) |
|---|---|
| Raspberry Pi 4 | 1 máy (các cổng USB 2 dùng chung một bus) |
| Raspberry Pi 5 | 1–2 máy |
| Mini PC x86 + card PCIe USB nhiều controller | mỗi controller USB độc lập khoảng 1 card MS2109 (băng thông isochronous); thêm card PCIe để thêm máy |

- Ưu tiên **MS2109** cho MJPEG passthrough. MS2130 ở cổng USB 3 chỉ xuất YUV thô (theo firmware), mất lợi thế
  passthrough.

## 8. Vì sao chọn hộp này

- **iPhone USB-C (15 trở lên, trừ 16e/17e):**
  - cổng USB-C của iPhone vừa xuất hình (DisplayPort Alt Mode) vừa làm USB host;
  - một hub cho ra HDMI, cổng USB-A và nhận sạc PD cùng lúc;
  - một dây lo cả hình, điều khiển lẫn sạc.
- **Board làm bàn phím + chuột (gadget), thay cho chip CH9329:**
  - không cần chip HID, không có đường serial;
  - "đã giao" chặt hơn: iPhone đã lấy báo cáo, không chỉ là chip đã nhận lệnh;
  - chuột tuyệt đối theo đúng kiểu đã chạy được trên iOS: interface riêng, không report ID, toạ độ 0..32767,
    interface con trỏ đứng cuối. Aiden, dự án đã chạy chuột tuyệt đối trên iPhone, cũng là một Linux USB gadget;
  - đổi profile bằng phần mềm (`sudo ihc gadget up --replace --profile K`), mỗi profile có serial USB riêng.
    CH9329 thì phải cấp nguồn lại mới đổi được chế độ. Đây là cách có thể né bug phím tắt Cmd/Shift/Option khi
    có cả bàn phím lẫn chuột; chưa tự động, cần kiểm ở bài B4.
- **HDMI IN của board, thay cho capture card:** bớt một linh kiện và một cổng USB. Rủi ro: driver HDMI IN của
  board và độ trễ của pipeline mã hoá, phải đo (bài B3, B7; xem [feasibility.md](../research/feasibility.md)).
- **Mỗi iPhone một board:** không phải ghép cặp chip với capture card, không tranh băng thông giữa các máy, một
  board hỏng chỉ ảnh hưởng một máy.
- **Dự phòng:** nếu cổng Type-C của board không chạy được chế độ device, cáp CH9329 thay phần bàn phím + chuột;
  hộp vẫn giữ HDMI IN. Chip CH9329 mua về là dùng được, giao thức ở [ch9329-protocol.md](ch9329-protocol.md).
