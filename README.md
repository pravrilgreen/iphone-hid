# iphone-hid

**Điều khiển iPhone cho automation chỉ bằng phần cứng gắn ngoài.** Hệ thống xem màn hình iPhone qua cổng
HDMI và thao tác bằng một bộ chuột + bàn phím "ảo". Với iPhone, đó chỉ là một màn hình ngoài và một bộ chuột,
bàn phím bình thường:
- không jailbreak, không bật Developer Mode;
- không cài app lên máy;
- không dùng công cụ riêng của Apple hay bên thứ ba dành cho iPhone.

Đây là **phần điều khiển iPhone** cho một hệ thống automation lớn hơn. Bên automation chỉ cần gọi "chạm vào
điểm này", "vuốt từ đây tới đây", "gõ chữ này", "chụp màn hình". Hệ thống lo phần cứng, con trỏ và độ chính
xác. Người vận hành có một trang web để xem màn hình từng máy trực tiếp và điều khiển như đang cầm máy.

> **Trạng thái:** phần mềm đã hoàn chỉnh và chạy được trên **iPhone mô phỏng**. Chưa có phần cứng, nên chưa
> hạng mục nào được kiểm chứng trên máy thật. Mọi hành vi mô phỏng đều dựa trên tài liệu gốc của hãng chip,
> hành vi iOS đã được ghi nhận và các dự án tương tự. Những điểm còn phải kiểm chứng đều có bài test phần cứng
> tương ứng. Xem [Lộ trình](#lộ-trình).

---

## Nó hoạt động thế nào (từ đầu đến cuối)

### 1. Mỗi iPhone được nối như sau

**iPhone cổng USB-C (iPhone 15 trở lên):** một dây mang cả hình lẫn điều khiển.

```
                ┌──────── Hub USB-C (HDMI + USB-A + sạc) ────────┐
   iPhone ══════│ HDMI  ─────► Capture card ─────► Máy chủ Linux │  hình
                │ USB-A ◄───── Chip HID ◄───────── Máy chủ Linux │  thao tác
                │ Sạc   ◄───── Củ sạc 20 W                       │
                └────────────────────────────────────────────────┘
```

**iPhone cổng Lightning:** hình qua adapter HDMI của Apple, thao tác qua Bluetooth.

```
   iPhone ══► Lightning Digital AV Adapter ──HDMI──► Capture card ──► Máy chủ Linux   hình
   iPhone ◄── Bluetooth ── ESP32 (chuột + bàn phím Bluetooth) ◄───── Máy chủ Linux   thao tác
```

**Vì sao iPhone Lightning phải dùng ESP32 và Bluetooth, còn iPhone USB-C thì không?**
- Cổng USB-C của iPhone 15 trở lên làm được hai việc cùng lúc: xuất hình ra HDMI và nhận thiết bị USB. Chỉ
  cần một hub là có cả hai, và chip HID (CH9329, rẻ, mua về dùng ngay) cắm vào hub như một bộ chuột + bàn
  phím có dây.
- iPhone Lightning chỉ có một cổng. Cổng đó phải dùng cho adapter HDMI của Apple thì mới lấy được hình, và cổng
  phụ trên adapter **chỉ để sạc**, không truyền dữ liệu. Không còn chỗ nào để cắm chuột có dây, nên thao tác
  phải đi qua **Bluetooth**.
- Chip CH9329 không có Bluetooth. ESP32 là vi điều khiển giá rẻ có Bluetooth, được nạp firmware để làm bộ
  chuột + bàn phím Bluetooth. Firmware "nói" đúng ngôn ngữ của CH9329, nên phần mềm trên máy chủ dùng chung cho
  cả hai dòng máy, không phải sửa gì.
- Không dùng Bluetooth cho cả iPhone USB-C, vì có dây thì ổn định hơn: không phải ghép đôi, không nhiễu sóng khi
  nhiều máy đặt cạnh nhau, trễ thấp hơn, và iPhone nhận được chế độ chuột chính xác nhất qua dây (xem dưới).
- Về lâu dài, có thể thay CH9329 bằng chính ESP32-S3 ở chế độ USB, để hai dòng máy dùng chung một loại phần
  cứng, và có thêm vài khả năng CH9329 không có.

### 2. Nhìn thấy màn hình

iPhone phản chiếu (mirror) màn hình ra HDMI. Capture card biến tín hiệu đó thành luồng hình nén sẵn. Máy chủ
chuyển thẳng luồng này tới trình duyệt, không giải nén rồi nén lại, nên xem được nhiều máy cùng lúc mà máy chủ
không phải gánh nặng. Trong mạng nội bộ, hình tới trình duyệt chậm khoảng 0,15–0,25 giây.

### 3. Chạm đúng chỗ, không cần "nhìn"

iPhone điều khiển bằng chuột thông qua tính năng trợ năng **AssistiveTouch**. Hệ thống không nhận diện hình ảnh,
nên phải biết chắc con trỏ đang ở đâu. Có hai cách, tự chọn khi hiệu chỉnh:

- **Chuột tuyệt đối:** đặt con trỏ thẳng vào toạ độ cần chạm, như chạm tay. Nhanh (khoảng 0,15 giây mỗi lần
  chạm) và gần như không sai số. Dự án mã nguồn mở gần nhất với sản phẩm này cho thấy iPhone nhận được cách này
  qua dây USB. Đây là điều đầu tiên sẽ kiểm chứng khi có phần cứng.
- **Chuột tương đối** (khi iPhone không nhận chuột tuyệt đối, ví dụ qua Bluetooth):
  1. trước mỗi lần chạm, đẩy con trỏ vào góc màn hình gần nhất, nơi nó chắc chắn dừng lại, để có một điểm xuất
     phát biết chắc;
  2. đi từng trục một, với nhịp đều tuyệt đối, để quãng đường luôn lặp lại được dù iPhone có "tăng tốc" con
     trỏ;
  3. vì luôn xuất phát lại từ góc, sai số không bị cộng dồn qua các lần chạm.

**Hiệu chỉnh mỗi máy một lần:** hệ thống tự mở một trang web trên Safari của iPhone. Trang cho biết chính xác
mỗi cú click rơi vào đâu, và hệ thống dùng đó để đo con trỏ của từng máy (vài giây với chuột tuyệt đối, khoảng
một phút với chuột tương đối).

### 4. Không mất lệnh

- Mỗi lệnh gửi xuống chip đều được chip **xác nhận**. Không có xác nhận thì hệ thống biết ngay và xử lý, không
  bao giờ "gửi rồi mong trúng".
- Lệnh nào gửi lại được an toàn (nhấn/nhả nút, phím, đặt vị trí tuyệt đối) thì được gửi lại.
- Lệnh di chuyển không rõ đã chạy hay chưa thì cả thao tác được làm lại từ đầu, kể cả bước đẩy con trỏ về góc,
  chứ không đoán.
- Nhịp gửi được đo từng lệnh. Nếu máy chủ bị khựng làm lệch nhịp (có thể làm lệch vị trí), thao tác đó cũng
  được làm lại.
- Mọi phím và nút luôn được nhả khi kết thúc, kể cả khi có lỗi giữa chừng hoặc người điều khiển đóng trình
  duyệt.
- Trước khi có iPhone, một công cụ kiểm tra đọc lại chính xác những gì chip gửi ra cổng USB, để xác nhận chip
  không đánh rơi hay gộp lệnh.

### 5. Dùng như thế nào

**Người vận hành** mở trang web của máy chủ:
- thấy lưới các máy đang chạy, kèm trạng thái: sẵn sàng / đang bận / máy khoá / mất hình / mất kết nối;
- mở một máy để xem màn hình trực tiếp, rồi chọn một trong hai chế độ:
  - **Điều khiển trực tiếp:** chuột và bàn phím của người vận hành đi thẳng tới iPhone, như dùng máy tính từ
    xa;
  - **Chạm chính xác:** click lên hình là chạm đúng điểm đó; kéo là vuốt; cuộn chuột là cuộn;
- có sẵn nút Home, App Switcher, Spotlight, âm lượng, ô gõ chữ, phím tắt;
- ghi lại một chuỗi thao tác và phát lại;
- nút **Hiệu chỉnh** cho mỗi máy.

**Dự án automation** gọi qua API (HTTP/WebSocket) hoặc thư viện Python. Toạ độ tính theo tỉ lệ trên màn
hình iPhone (0 đến 1), không phụ thuộc camera, độ phân giải hay đời máy. Các lệnh có sẵn:
- chạm, chạm giữ, vuốt, cuộn;
- gõ chữ, phím tắt, Home, App Switcher, mở URL;
- chụp màn hình, xem trạng thái;
- chạy một kịch bản thao tác.

Một máy chủ quản lý nhiều iPhone. Nhiều máy chủ được gộp lại thành một "farm" từ phía thư viện.

---

## Cần những gì cho mỗi iPhone

| Thiết bị | Dòng USB-C | Dòng Lightning | Ghi chú |
|---|---|---|---|
| Hub USB-C có HDMI + USB-A + sạc PD | ✔ | | nên thử 2–3 mẫu; tham khảo Apple USB-C Digital AV Multiport Adapter |
| Cáp CH9329 (CH9329 + CH340, hai đầu USB-A) | ✔ | | mua về dùng ngay |
| Apple Lightning Digital AV Adapter | | ✔ | hàng chính hãng |
| ESP32-S3 DevKit | | ✔ | nạp firmware có sẵn trong repo |
| Capture card HDMI → USB (chip MS2109) | ✔ | ✔ | loại nén sẵn MJPEG |
| Củ sạc PD ≥ 20 W | ✔ | ✔ | |
| Máy chủ Linux | | | Raspberry Pi 5 cho 1–2 máy; mini PC x86 + card USB mở rộng cho nhiều máy |

Lưu ý:
- **iPhone 16e/17e không xuất được HDMI**, nên không dùng được cách này.
- Mỗi iPhone cần cài đặt tay một lần (bật AssistiveTouch, gán nút chuột, tắt khoá màn hình tự động...). Xem
  [hướng dẫn cài đặt iPhone](docs/iphone-setup.md).

## Hiệu năng mục tiêu

| Chỉ số | Mục tiêu | Trên mô phỏng |
|---|---|---|
| Độ chính xác chạm | 95% trong 4 pt (≈ 5 px ở khung 1080p) | chuột tuyệt đối ≤ 0,3 pt; chuột tương đối ≤ 1 pt |
| Thời gian một lần chạm | < 1,5 s | tuyệt đối ≈ 0,15 s; tương đối 1,2–1,5 s |
| Độ trễ hình tới trình duyệt (LAN) | < 0,25 s | cần đo trên máy thật |
| Lệnh bị mất mà không biết | 0 | mọi lệnh có xác nhận; lệnh lỗi được làm lại hoặc báo lỗi |

Nút thắt khi mở rộng là **băng thông USB cho capture card**, không phải phần điều khiển. Một host chịu được bao
nhiêu máy sẽ được đo cụ thể khi có phần cứng.

## Lộ trình

| Giai đoạn | Nội dung | Trạng thái |
|---|---|---|
| 0. Kiểm chứng phần cứng | công cụ đo chip, capture card, hiệu chỉnh, checklist 11 bài test | 🟢 công cụ sẵn sàng, chờ phần cứng |
| 1. Điều khiển HID | driver chip, xác nhận từng lệnh, dò cấu hình, công cụ test | 🟢 xong trên mô phỏng |
| 1b. Firmware ESP32 | chuột + bàn phím Bluetooth, cùng ngôn ngữ với CH9329 | 🟡 đang hoàn thiện |
| 2. Con trỏ chính xác | chuột tuyệt đối / tương đối, hiệu chỉnh qua Safari | 🟢 xong trên mô phỏng |
| 3. API + trang điều khiển | xem live, điều khiển trực tiếp, chạm chính xác, ghi/phát, thư viện Python | 🟡 đang hoàn thiện |
| 4. Ổn định, nhiều máy | tự kết nối lại, phát hiện máy khoá/mất hình, log, cấu hình farm | 🟢 xong trên mô phỏng |
| 5. Board nhúng cho từng máy | gắn phần điều khiển + lấy hình vào một board nhỏ theo từng iPhone | ⚪ nghiên cứu |

🟢 xong trên mô phỏng · 🟡 đang làm · ⚪ chưa làm.

## Giới hạn

- Không vượt được passcode/Face ID, không cài app, không đọc dữ liệu hệ thống. iPhone phải được mở khoá sẵn.
- Nội dung có bảo vệ bản quyền (Netflix...) sẽ ra màn đen trên HDMI.
- Tự động hoá trên nền tảng của bên thứ ba có thể vi phạm điều khoản sử dụng của nền tảng đó.

## Tài liệu

- [Bắt đầu nhanh](docs/getting-started.md): chạy thử trên mô phỏng, rồi trên phần cứng
- [Kiến trúc](docs/architecture.md)
- [Đánh giá khả thi](docs/feasibility.md): nghiên cứu độc lập, có nguồn, kèm rủi ro
- [Checklist kiểm chứng phần cứng](docs/phase0-checklist.md)
- [Cài đặt iPhone](docs/iphone-setup.md)
- [Giao thức chip CH9329](docs/ch9329-protocol.md)
- [Bộ mô phỏng](docs/simulator.md)
- [Firmware ESP32](firmware/esp32_ble_hid/README.md)
