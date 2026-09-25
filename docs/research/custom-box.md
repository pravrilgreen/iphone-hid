# Box tự thiết kế: tính khả thi, giá thành, thiết kế tổng thể

- **Ngày:** 2026-09-25. **Phạm vi:** thay Orange Pi 5 Plus + hub bằng một box tự làm, nhỏ gọn, cắm là chạy. Box vừa là
  bàn phím/chuột của iPhone, vừa nhận hình từ cổng USB-C của iPhone, vừa sạc iPhone. Box được điều khiển từ xa qua
  Ethernet, Wi-Fi và USB. Tài liệu này cũng trả lời câu hỏi CH9329 có đủ nhanh không.
- **Cách làm:** ba lượt tra cứu song song.
  - Datasheet đọc trực tiếp từ bản lưu trên GitHub: WCH CH32V20x/30x, Rockchip RV1106, Sophgo SG200x.
  - Mã và tài liệu của các sản phẩm KVM mã nguồn mở: JetKVM, NanoKVM, PiKVM, Luckfox PicoKVM, GL.iNet Comet, Aiden.
  - Driver Linux của các chip cầu nối (Rockchip BSP, InES), TinyUSB, Chromium EC.
  - Kết quả tìm kiếm cho giá LCSC và các trang bị proxy chặn: cnx-software, orangepi.org, lcsc.com, wch.cn.
- **Nhãn:** **[Chắc]** = đã đọc nguồn gốc (mã, datasheet, tài liệu hãng). **[Có thể]** = lời hãng, đoạn trích tìm
  kiếm, hoặc nhiều nguồn gián tiếp khớp nhau. **[Chưa biết]** = chưa có dữ liệu, phải tự đo. Giá lấy từ đoạn trích LCSC
  là **[Có thể]**; số nào là ước tính thì ghi rõ "ước tính".
- **Giấy phép:** JetKVM (GPL-2.0), Luckfox PicoKVM (GPL) và Aiden (AGPL-3.0) chỉ dùng để tham khảo kiến trúc. Không
  chép mã của các dự án này; firmware và phần mềm của box tự viết.

---

## 0. Kết luận

1. **Khả thi, và kiến trúc đã được chứng minh ngoài thị trường.**
   - JetKVM, Luckfox PicoKVM và GL.iNet Comet đều là SoC Rockchip nhỏ, có chip cầu nối hình và cổng USB làm bàn
     phím/chuột. [Chắc]
   - Aiden dùng đúng SoC RV1106 để điều khiển **iPhone** qua hub USB-C. [Chắc]
   - Box của mình là mẫu đó, chuyên cho iPhone. Điểm khác: một chip nhận thẳng hình từ cổng USB-C của iPhone, bỏ hub.
2. **CH9329 đủ cho tap thường, nhưng không đạt mức "siêu tối ưu".** Ba giới hạn chính:
   - descriptor cố định, nên không dùng được bố cục chuột tuyệt đối đã chạy trên iPhone của bạn;
   - lệnh ack chỉ có nghĩa là chip đã nhận, không phải iPhone đã lấy báo cáo;
   - UART 115200 baud gây trễ và lệch nhịp.

   Thay bằng **CH32V305RBT6**: MCU RISC-V của WCH, USB 2.0 High-Speed có PHY sẵn cộng một cổng Full-Speed thứ hai,
   vỏ LQFP64 hàn mỏ được, giá khoảng $1,33. **CH9329F** (bản High-Speed mới) là phương án cắm thay thế nhanh, nhưng
   descriptor vẫn cố định. (§3)
3. **Tốc độ thật sự nằm ở đường hình và ở iOS, không nằm ở chip HID.**
   - Chip HID tốt nhất chỉ tiết kiệm được vài mili giây.
   - iOS mất 80–250 ms để con trỏ lướt tới đích trước mỗi cú tap.
   - Đường hình quyết định độ trễ nhìn thấy. H.264 phần cứng chế độ low-delay qua WebRTC cho 35–60 ms từ màn hình tới
     màn hình (glass-to-glass), MJPEG thì chậm hơn nhiều. (§2)
4. **Box đề xuất (phương án B):**
   - iPhone ↔ **LT7911D**: một chip lo cả PD, DP Alt Mode, và chuyển DP sang MIPI CSI;
   - **CH32V305**: bàn phím/chuột High-Speed, chạy chuỗi lệnh có định thời ngay trên chip;
   - **RV1106** trên module hàn được **Luckfox Core1106**: nhận hình, mã hoá H.264 low-delay, chạy API;
   - cổng ngoài: Ethernet, Wi-Fi, và USB-C tới PC (hình qua UVC, mạng qua USB).

   Linh kiện khoảng **$35–55 mỗi box** (ước tính, lô 10–50). Bản **dock cho PC** (phương án A, không có Linux trên
   box) khoảng **$17–27**. (§6, §9)
5. **Đi từng bước, rủi ro thấp trước.**
   - Làm board HID CH32V305 trước: 1–2 tuần, dưới $10 linh kiện. Board này dùng ngay được với Orange Pi hiện tại.
   - Sau đó ghép box thử từ module có sẵn.
   - Cuối cùng mới làm PCB riêng. (§12)

---

## 1. Yêu cầu

| Yêu cầu | Cách đáp ứng trong thiết kế |
|---|---|
| Hiệu năng tối đa: ít trễ, không mất lệnh | HID High-Speed 125 µs, chuỗi lệnh chạy bằng timer trên MCU, xác nhận khi iPhone đã lấy báo cáo; H.264 low-delay + WebRTC, cắt bỏ phần viền đen trước khi mã hoá |
| Một cổng USB-C cắm vào iPhone: nhận hình, làm bàn phím/chuột, sạc | LT7911D (PD + Alt Mode sink + sạc pass-through) và CH32V305 trên chân D+/D- |
| Xuất hình qua HDMI hoặc USB | USB: SoC làm webcam UVC cho PC. HDMI: chỉ có ở bản dock (MS2131 có cổng HDMI loop-out), vì RV1106 không có cổng xuất HDMI |
| Điều khiển từ xa qua Wi-Fi, Ethernet, USB | Ethernet 100M (PHY nằm trong RV1106), module Wi-Fi SDIO, USB-C tới PC (mạng qua USB + UVC + điều khiển) |
| Cắm là chạy, nhỏ gọn | DHCP + mDNS, EDID và chế độ con trỏ tự đặt, cập nhật OTA, vỏ nhôm cỡ hub USB-C (§8) |
| Hàn tay được | LQFP cho MCU, module có chân hàn cạnh (castellated) cho SoC. QFN cho chip hình thì cần máy khò, hoặc đặt JLCPCB lắp sẵn (§10) |

---

## 2. Thời gian đi đâu

### 2.1 Một cú tap (chuột tuyệt đối)

| Chặng | CH9329 @115200 | Linux gadget (Orange Pi, hiện tại) | CH32V305 HS (đề xuất) |
|---|---|---|---|
| Server → chip | ~1,7 ms: khung 13 byte + ack 7 byte trên dây [Chắc, tính toán] | vài chục µs (`write()` vào `/dev/hidgN`) | < 0,1 ms qua USB/SPI (ước tính) |
| Chờ iPhone đến lấy (poll) | chưa rõ: chip Full-Speed, bInterval không công bố [Chưa biết] | ≤ 1 ms (HS, bInterval 4); đo trên board của bạn: 2,6 ms từ lúc ghi tới lúc iPhone lấy [Chắc] | ≤ 0,125 ms nếu iOS tôn trọng bInterval=1 [Chưa biết, phải đo] |
| Xác nhận | "chip đã nhận" | "iPhone đã lấy" (POLLOUT) | "iPhone đã lấy" + mốc thời gian µs (ngắt IN-complete) |
| iOS cho con trỏ lướt tới đích | 80–250 ms (Aiden chờ 80 ms, glassbox chứng minh 250 ms an toàn) [Chắc, xem absolute-pointer.md] | như bên trái | như bên trái |
| Nhấn, giữ, nhả (nhả gửi 3 lần) | 60–100 ms + ~30 ms | như bên trái | như bên trái, nhưng định thời chính xác tới µs trên MCU |

Tap mất khoảng 150–350 ms, và gần hết là thời gian của iOS. Chip HID tốt nhất chỉ bớt được 1–10 ms. Muốn nhanh hơn
thật sự thì phải rút ngắn thời gian chờ con trỏ lướt:

- **Đo độ trễ lướt cho từng iPhone** (bước calibration đã đo được `abs_settle`). Thay giá trị cố định 250 ms bằng số
  đo thật.
- **Thử tắt hiệu ứng** (Reduce Motion, và Pointer Animations nếu iOS có mục này cho con trỏ AssistiveTouch). Nếu con
  trỏ nhảy thẳng tới đích thì thời gian chờ có thể xuống vài chục ms. [Chưa biết, phải thử]

Chip HID quan trọng ở bốn chỗ khác:

1. **Chế độ tương đối.** iOS tăng tốc con trỏ theo vận tốc, nên báo cáo gửi lệch nhịp vài ms là con trỏ đi sai quãng.
   Python trên Linux lệch nhịp 0,5–5 ms. Timer phần cứng trên MCU lệch dưới 1 µs, nên chế độ tương đối trở nên tất định.
   Điều này quan trọng khi một bản iOS nào đó (vd iOS 27, xem absolute-pointer.md) bỏ qua chuột tuyệt đối.
2. **Điều khiển trực tiếp mượt hơn:** 1000–8000 báo cáo/s, so với tối đa khoảng 580/s khi CH9329 phải chờ ack.
3. **Xác nhận đúng nghĩa:** chỉ gadget và MCU biết chắc iPhone đã lấy báo cáo. CH9329 thì không.
4. **Descriptor tự do:** dùng được bố cục đã chạy trên iPhone (mỗi loại báo cáo một interface, chuột tuyệt đối
   0..32767). Bố cục ID 1/2 dùng chung một interface của CH9329 vẫn chưa ai thử trên iOS (absolute-pointer.md, V7).

### 2.2 Đường hình (glass-to-glass)

| Hệ | Độ trễ | Nguồn |
|---|---|---|
| PiKVM V4 (TC358743 → CM4, H.264 qua WebRTC) | 35–50 ms (thu 17 ms + mã hoá 13 ms) | tài liệu PiKVM [Chắc] |
| JetKVM (RV1106 + TC358743, H.264 qua WebRTC) | hãng nói 30–60 ms; Jeff Geerling đo ~40 ms; một đối thủ đo ~98 ms từ click tới hình | [Có thể] |
| NanoKVM (SG2002 + LT6911) | 100–150 ms | README NanoKVM [Chắc] |
| Dongle MS2130 vào PC, 1080p60 | ~66 ms, tính cả màn hình PC | thảo luận HyperHDR [Có thể] |
| Orange Pi 5 Plus + bộ mã hoá JPEG CPU (hiện tại) | chưa đo; dự đoán 80–150 ms | [Chưa biết] |

Những cải tiến dùng được cho mọi phần cứng, kể cả Orange Pi hiện tại:

- **Cắt bỏ viền đen trước khi mã hoá.** Màn hình dọc của iPhone chỉ chiếm khoảng 500×1080 trong khung 1920×1080, tức
  khoảng 26% số điểm ảnh. Cắt ra trước (RGA của Rockchip) thì bộ mã hoá và đường mạng nhẹ khoảng 4 lần.
- **Dùng H.264 phần cứng, chế độ low-delay:** không B-frame, chia slice, gửi IDR khi có yêu cầu. Truyền qua
  WebRTC/WebCodecs; giữ MJPEG làm phương án dự phòng.
- **Băng thông:** MJPEG 1080p60 cần 50–150 Mbit/s nên chỉ chạy được trên LAN. H.264 chỉ cần 2–8 Mbit/s, đủ chạy qua
  Wi-Fi và qua Ethernet 100M.
- **Thử EDID dọc.** Nếu box khai báo một độ phân giải dọc, có thể iPhone xuất hình không viền. [Chưa biết, phải thử]

---

## 3. CH9329 có đủ không, và chọn gì thay

| | CH9329 (đang hỗ trợ) | CH9329F (mới) | Linux gadget trên SoC | **CH32V305RBT6 (đề xuất)** |
|---|---|---|---|---|
| USB tới iPhone | Full-Speed [Có thể] | High-Speed, bInterval chỉnh được [Có thể] | High-Speed | High-Speed, PHY sẵn trên chip [Chắc] |
| Kênh từ host | UART ≤ 115200 (không đạt 115200 khi chạy 3,3 V) [Có thể] | UART tới 15 Mbit/s [Có thể] | ghi thẳng vào file thiết bị | USB Full-Speed thứ hai, hoặc SPI/UART tốc độ Mbit [Chắc] |
| Descriptor | cố định; chỉ chỉnh VID/PID, chuỗi, chế độ [Chắc] | bàn phím/chuột vẫn cố định; có chế độ touchscreen và báo cáo HID tuỳ ý 510 byte [Có thể] | tự do | tự do |
| Ack khi iPhone đã lấy | không | có (tuỳ chọn) [Có thể] | có (POLLOUT) | có, kèm mốc thời gian µs |
| Chuỗi lệnh có định thời trên chip | không | không | không (Linux lo lịch) | có |
| Vỏ, giá | SOP16, rẻ | QFN32, giá chưa rõ | nằm sẵn trong SoC | LQFP64M 10×10, 0,5 mm, ~$1,33 [Có thể] |

**Ghi chú về CH32V305** [Chắc, datasheet V3.9]:

- Vỏ TSSOP20 (V305FBP6) và QFN28 (V305GBU6) chỉ đưa ra chân của cổng High-Speed. Muốn cả hai cổng USB thì chọn một
  trong ba:
  - V305RBT6 (LQFP64M);
  - V305CCT6 (LQFP48);
  - V307RCT6/VCT6 (LQFP64M/100, khoảng $2,2).
- TinyUSB có driver HS/FS cho CH32V30x, nhưng mỗi lúc chỉ chạy một cổng. Cổng thứ hai dùng driver của WCH; repo
  `openwch/ch32v307` có ví dụ HID trên cả hai cổng.
- RP2040/RP2350 chỉ có Full-Speed. NXP LPC55S16 và STM32F723 cũng làm được nhưng đắt hơn hoặc cần thêm linh kiện.
  Không dùng ESP32, theo yêu cầu của dự án.

**iPhone có poll ở 125 µs không?** Chưa có số đo công khai [Chưa biết].

- Tính theo chuẩn: Full-Speed tối thiểu 1 ms. High-Speed với bInterval=1 là 125 µs.
- Dấu hiệu gần nhất từ Apple: TinyUSB issue #1705. Một thiết bị HID Full-Speed đặt bInterval=1 chỉ được poll 500 Hz
  trên Mac Apple Silicon, trong khi Mac Intel và Windows cho 1000 Hz.
- Vì vậy phải đo trên iPhone thật bằng board thử nanoCH32V305 **trước khi** vẽ PCB. Đây là thí nghiệm T1 ở §11.

---

## 4. Đường hình: từ cổng USB-C của iPhone tới khung hình

**Những gì đã biết về iPhone** [Chắc, trừ chỗ ghi khác]:

- DP Alt Mode xuất tối đa 4K60, cắm adapter USB-C sang HDMI thường là chạy, không cần MFi.
- Chỉ phản chiếu màn hình: màn hình chính dọc bị thêm viền hai bên (pillarbox).
- Có DP: 15, 16 và 17, gồm các bản Plus, Pro, Pro Max. **Không có DP: 16e, 17e, Air.**
- Bản thường chạy USB 2.0; bản Pro chạy USB 3 10 Gbit/s. Ở chế độ DP 4 lane, cặp D+/D- (USB 2.0) vẫn trống cho HID.
- App có bảo vệ bản quyền (phim) hiện màn đen trên đường hình không có HDCP. [Có thể]
- Adapter USB-C Digital AV Multiport của Apple chạy cùng lúc hình, USB và sạc. Tức là tổ hợp vai trò mà box cần đã
  được chứng minh là chạy được.

| Chip | Vào → ra | Có sẵn PD/Alt Mode | Vỏ | Giá | Ghi chú |
|---|---|---|---|---|---|
| **LT7911D** | USB-C/DP1.2 (4 lane) → MIPI CSI | có (PD 2.0, CC kép để sạc pass-through) | QFN-64 | ~$4,9 | driver `lt7911d.c` trong Rockchip BSP; 1080p60/4K30 [Có thể/Chắc] |
| LT7911UXC | USB-C/DP1.4a → MIPI 4 lane | có (PD 3.0) | BGA-169 | ~$13, hay hết hàng | chỉ cần khi muốn 4K60 |
| **LT8711HE** | USB-C/DP → HDMI 2.0 | có (CC kép, pass-through, có MCU và flash) | QFN-64 | ~$3,2 | dùng cho bản dock |
| LT6911C / LT6911UXC | HDMI → CSI | không | QFN-64 | ~$3,6 / ~$5,9 | NanoKVM dùng; cần firmware của Lontium |
| TC358743 | HDMI 1.4 → CSI-2 | không | BGA-64 | chưa rõ | driver chính thức trong Linux; JetKVM, PiKVM dùng |
| RK628D | HDMI → CSI | không | chưa rõ | chưa rõ | Aiden dùng |
| **MS2131** / MS2130 | HDMI → USB 3 UVC 1080p60 (MS2131 có thêm HDMI loop-out) | không | QFN-64 | MS2130 ~$3,1 | UVC chuẩn, không cần driver; firmware nằm ở flash ngoài |
| VL102/VL103, CYPD3125 | chỉ PD + Alt Mode (vai UFP_D, nguồn cấp điện) | có | QFN-48/40 | ~$1,4–2,4 | firmware đóng; nếu muốn PD mở thì Chromium EC servo_v4 là tham chiếu mã mở đúng vai này |

Chuỗi đề xuất:

- **Box độc lập (B):** iPhone → LT7911D → CSI 4 lane → RV1106. Một chip lo cả PD, Alt Mode và chuyển hình. Không cần
  hub, không có HDMI ở giữa.
- **Dự phòng rủi ro thấp:** adapter USB-C sang HDMI có PD pass-through → module HDMI-sang-CSI (TC358743 hoặc
  LT6911C) → RV1106. Đây đúng là cách Aiden và JetKVM làm.
- **Dock cho PC (A):** iPhone → LT8711HE → HDMI → MS2131 → USB 3 → PC.

---

## 5. SoC

| SoC | CPU / RAM | H.264 | CSI | Cổng USB làm thiết bị | Ethernet | Vỏ, module |
|---|---|---|---|---|---|---|
| **RV1106G3** | 1× A7, RAM 256 MB nằm trong chip | 5 MP@30, có chế độ "ultra-low delay" | 1×4 hoặc 2×2 lane | 1 | 100M, PHY trong chip | QFN128 0,35 mm; module Luckfox Core1106 chân hàn cạnh, $16–27 |
| SG2002 | C906/A53 1 GHz, 256 MB trong chip | 1080p60 (thực tế) | datasheet và board ghi khác nhau | 1 | 100M, PHY trong chip | LicheeRV Nano $9–14; độ trễ cao hơn (NanoKVM 100–150 ms) |
| RV1126B | 4× A53, RAM ngoài | 4K30, low-delay | 2×4 | 1 (USB3) | GbE | BGA; Luckfox Aura từ $49 |
| RK3576 | 4×A72 + 4×A53 | 4K60 | 2×4 + C/D-PHY | **2** | 2× GbE | BGA; Core3576 từ $105 |
| RK3566/3568 | 4× A55 | 1080p60 | 1×4 | 1 | GbE | BGA, cần module |

[Chắc với số liệu datasheet; giá module là Có thể]

**Chọn RV1106G3 trên Luckfox Core1106.**

- JetKVM (1080p60), Luckfox PicoKVM và Aiden (điều khiển iPhone) đều chạy trên chip này.
- RAM và PHY Ethernet nằm sẵn trong chip, nên board đơn giản.
- Bộ mã hoá có chế độ low-delay. SDK `luckfox-pico` mở trên GitHub.
- Hạn chế duy nhất: chỉ có một cổng USB. Thiết kế dành cổng đó cho PC, còn HID giao cho MCU.

**Khi nào lên RK3576:** khi muốn 4K60, GbE, hoặc **2 iPhone trên một box** (hai CSI, hai cổng USB thiết bị). Module
đắt khoảng 5 lần, nhưng chia cho hai iPhone thì giá mỗi máy gần tương đương.

---

## 6. Hai phương án box

### Phương án B: box độc lập (đề xuất)

![Box độc lập](../images/box-architecture.png)

- **Phía iPhone.** Một cáp USB-C:
  - CC và cặp DP vào LT7911D;
  - D+/D- vào cổng High-Speed của CH32V305;
  - VBUS lấy từ bộ sạc qua mạch pass-through.
- **Trong box.**
  - LT7911D đẩy CSI-2 4 lane vào RV1106. RV1106 cắt vùng màn hình, mã hoá H.264 low-delay, rồi chạy API và web console.
  - CH32V305 nối với RV1106 qua SPI hoặc UART vài Mbit/s. Nó nhận lệnh cấp cao (tap, vuốt, gõ) và tự chạy theo timer.
- **Phía ngoài.**
  - Ethernet 100M và module Wi-Fi SDIO.
  - Cổng USB của RV1106 đưa ra một USB-C tới PC. PC thấy box là card mạng USB (gọi đúng REST API như qua LAN), một
    webcam UVC (màn hình iPhone), và một kênh điều khiển. Cả ba chạy trên một dây, không cần driver.
- **Không có HDMI out**, vì RV1106 không có bộ phát HDMI. Nếu cần HDMI out thì dùng phương án A, hoặc thêm LT8711HE
  với bộ chia (tốn thêm tiền và độ phức tạp).

### Phương án A: dock cho PC, không chạy Linux

![Dock cho PC](../images/box-dock.png)

- Gồm LT8711HE (USB-C sang HDMI, có PD), MS2131 (HDMI sang USB 3 UVC, có HDMI loop-out), CH32V305 (HID) và một chip
  hub USB 3.
- PC thấy dock là một webcam (màn hình iPhone) cộng một thiết bị điều khiển, không cần driver trên Linux, Windows hay
  macOS.
- Server `ihc` chạy trên PC. Nó đã đọc được webcam UVC ở chế độ MJPEG passthrough; chỉ cần thêm một backend HID
  mới cho MCU.
- **Hợp khi:** trại máy có sẵn PC/mini PC, cần HDMI out, hoặc muốn box không có hệ điều hành phải cập nhật.
- **Không hợp khi:** cần điều khiển qua Wi-Fi/Ethernet mà không có PC, hoặc một PC phải gánh nhiều iPhone. Mỗi dongle
  MS2130 truyền YUY2 1080p60 tốn khoảng 250 MB/s trên USB 3, nên phải dùng MJPEG.

| | B: box độc lập | A: dock cho PC | Orange Pi 5 Plus + hub (hiện tại) |
|---|---|---|---|
| Cần máy host | không | có (PC) | không |
| Độ trễ hình (mục tiêu) | 35–60 ms (H.264 WebRTC) | ~50–70 ms (MS213x + PC) | chưa đo; JPEG chạy trên CPU |
| HID | CH32V305 HS, chuỗi lệnh có định thời | CH32V305 HS | Linux gadget HS |
| Wi-Fi / Ethernet / USB | có / có / có | qua PC / qua PC / có | có / có / không |
| HDMI out | không | có | hub có |
| Linh kiện (ước tính) | $35–55 | $17–27 | board đắt hơn nhiều lần, thêm hub |
| Kích thước | cỡ hộp 90×60×20 mm | cỡ hub USB-C | board + hub + dây |

---

## 7. Firmware và phần mềm

### 7.1 Firmware MCU (CH32V305), viết C bare-metal

| Khối | Việc |
|---|---|
| `usb_phone` (cổng HS) | HID composite: bàn phím, phím media, chuột tương đối, chuột tuyệt đối. Mỗi loại một interface, không report ID, bInterval=1. Hồ sơ RA/AR/A/R/K giống `ihc gadget`, mỗi hồ sơ một số serial. Đổi hồ sơ lúc đang chạy bằng cách enumerate lại mềm. |
| `link` | Kênh với host: USB Full-Speed (vendor bulk, hoặc CDC có descriptor WinUSB/MS OS 2.0 để Windows không cần driver) hoặc SPI slave dùng DMA. Khung COBS + CRC16 + số thứ tự. Mỗi lệnh nhận ack, lệnh lỗi nhận nack. Có luồng sự kiện gửi ngược về host. |
| `sched` | Timer phần cứng 1 µs và hàng đợi báo cáo có hạn giờ. Lệnh cấp cao chạy ngay trên chip, không phụ thuộc Linux hay mạng: `tap(x, y, settle, hold, release×3)`, `swipe(điểm, thời gian, tần số)`, `rel_run(n, nhịp)`, `type(chuỗi, nhịp phím)`, `key`, `media`. |
| `telemetry` | Mốc thời gian µs lúc iPhone lấy từng báo cáo (ngắt IN-complete). Đếm SOF để đo tần số poll thật. Trạng thái USB (configured / suspend / reset). Đèn Caps Lock từ báo cáo OUT. |
| `safety` | Watchdog. Tự nhả mọi phím và nút khi mất kênh host quá 200 ms, khi USB reset hoặc khi iPhone vào suspend. Không bao giờ để nút bị giữ. |
| `update` | Bootloader ISP qua USB của WCH (công cụ mở `wchisp`), gọi được từ phần mềm `ihc`. |

Server `ihc` thêm backend `mcu`. Backend này nói giao thức trên, dùng cho cả Orange Pi, PC và box B. Mô hình con trỏ
(`PointerModel`) giao phần định thời cho MCU thay vì tự `sleep()`.

### 7.2 Phần mềm trên RV1106

- Image Buildroot tối giản (SDK luckfox-pico), rootfs chỉ đọc, cập nhật A/B, khởi động dưới 5 giây.
- **Dịch vụ hình (C/C++):** nhận khung từ LT7911D (V4L2) → RGA cắt vùng màn hình → bộ mã hoá H.264 phần cứng chế độ
  low-delay → WebRTC (thư viện có giấy phép mở dễ dùng, không lấy mã GPL của JetKVM), dự phòng MJPEG, và UVC gadget
  trên cổng USB-C tới PC.
- **Dịch vụ điều khiển:** giữ nguyên hợp đồng REST/WebSocket của `ihc`, để SDK và client hiện có chạy không đổi. Có
  thể viết lại bằng Go hoặc Rust cho gọn RAM; bản đầu có thể giữ Python nếu vừa 256 MB.
- **Mạng:** DHCP, mDNS `_ihc._tcp` (đã có trong `ihc`), phát Wi-Fi AP kèm trang cấu hình khi chưa có mạng, CDC-NCM
  qua cổng USB-C.

---

## 8. Cắm là chạy, kiểu các sản phẩm Trung Quốc

1. **Cắm ba dây:** iPhone, sạc PD, LAN (hoặc Wi-Fi). Đèn trạng thái: đỏ là chưa có iPhone, vàng là có HID nhưng chưa
   có hình, xanh là sẵn sàng.
2. **Tự vào mạng:** DHCP, rồi tự quảng bá mDNS. Máy điều khiển thấy box ngay (`ihc` đã tự tìm box qua mDNS).
3. **Không phải chỉnh gì:**
   - EDID 1080p60 và chuột tuyệt đối được đặt sẵn;
   - hồ sơ HID mặc định là bố cục đã chạy trên iPhone;
   - trên iPhone chỉ phải bật AssistiveTouch và gán nút một lần (nếu phím Home kiểu media chạy được thì bỏ luôn
     bước gán nút).
4. **Cắm vào PC là chạy**, không cần driver: mạng qua USB + UVC + kênh điều khiển.
5. **Cập nhật một chạm:** OTA cho SoC, ISP qua USB cho MCU, cả hai từ web console.
6. **Vỏ:** nhôm, cỡ hub USB-C. Cáp USB-C ngắn hoặc đầu đực vuông góc cắm thẳng vào iPhone; có giá kẹp cho dàn máy.

---

## 9. Giá thành (ước tính, lô 10–50, USD)

**Phương án B:**

| Hạng mục | Giá |
|---|---|
| Luckfox Core1106 (RV1106G3, 256 MB) | 16–27 [Có thể] |
| LT7911D | ~4,9 [Có thể] (firmware Lontium: chưa rõ) |
| CH32V305RBT6 + thạch anh + ESD | ~1,7 |
| 3× USB-C, ESD cho cổng iPhone và cổng PC | ~1,5 |
| Nguồn: kích PD đầu vào (CH224K), buck 5 V/3 A, các nguồn 3,3/1,8/1,2 V, công tắc tải VBUS | 2–3 (ước tính) |
| RJ45 có biến áp | ~0,8 |
| Module Wi-Fi SDIO | 2–4 (ước tính) |
| PCB 4 lớp có kiểm soát trở kháng | 2–5 (ước tính) |
| Linh kiện thụ động, LED, nút | 1–2 |
| Vỏ nhôm | 3–8 |
| **Cộng** | **~$35–55** |

**Phương án A:** LT8711HE ~3,2 + MS2131 (MS2130 ~3,1) + flash 0,2 + CH32V305RBT6 1,3 + chip hub USB 3 (chưa rõ, ước
tính 1–3) + cổng và ESD ~2 + nguồn ~1,5 + PCB 2–5 + vỏ 3–6 ≈ **$17–27**.

**Board HID (giai đoạn 1):** CH32V305RBT6 + thạch anh + LDO + 2 USB-C + ESD + PCB 2 lớp ≈ **$4–6**.

**Giá tham khảo thị trường** [Có thể]:

- JetKVM: $69–103;
- GL.iNet Comet: $69–89;
- Luckfox PicoKVM: $28/$56;
- NanoKVM: Lite ~$20, Full ~$40.

Box B nằm quanh giá NanoKVM Full/PicoKVM, nhưng làm riêng cho iPhone: nhận hình thẳng từ cổng USB-C, có MCU HID
định thời chính xác, và sạc iPhone.

---

## 10. Hàn tay và sản xuất

| Linh kiện | Vỏ | Cách hàn |
|---|---|---|
| CH32V305RBT6 | LQFP64 0,5 mm | mỏ hàn + flux, kéo chì: được |
| Luckfox Core1106 | module có chân hàn cạnh | mỏ hàn: được |
| LT7911D, LT8711HE, MS2131 | QFN-48/64, có pad nhiệt | cần stencil + máy khò hoặc bếp hàn; hoặc đặt JLCPCB lắp sẵn |
| RV1106 trần, TC358743 | QFN128 0,35 mm, BGA | không hàn tay: dùng module |

- **PCB:** 4 lớp, trở kháng 90 Ω cho USB và 100 Ω cho DP và MIPI. Cặp DP từ cổng USB-C tới LT7911D càng ngắn càng tốt,
  không đi qua via nếu tránh được. Đặt JLCPCB 4 lớp có kiểm soát trở kháng thì rẻ.
- **Bản thử đầu tiên** nên dùng board phát triển và module có sẵn (§12, giai đoạn 2), để tách rủi ro của mạch cao tần
  ra khỏi rủi ro của firmware.

---

## 11. Rủi ro và thí nghiệm làm trước

| ID | Câu hỏi | Cách thử | Nếu không được |
|---|---|---|---|
| T1 | iPhone có poll HID High-Speed ở 125 µs không? | board nanoCH32V305, đếm khoảng cách giữa các IN-complete | vẫn dùng CH32V305 (lợi ích từ định thời và descriptor vẫn còn), chỉ đặt kỳ vọng 1 ms |
| T2 | LT7911D có vào được DP Alt Mode với iPhone trong khi vẫn cấp nguồn (source, DR_Swap)? Lấy firmware Lontium ở đâu? EDID có ép 1080p60 được không? | mua board LT7911D có sẵn (loại USB-C sang MIPI cho màn hình), cắm iPhone + Luckfox Pico | adapter USB-C sang HDMI + TC358743/LT6911C (đường Aiden/JetKVM đã chạy) |
| T3 | RV1106 có nhận được 1080p60 từ LT7911D không? (chuyển driver `lt7911d.c` từ BSP 5.10 sang kernel SDK) | Luckfox Pico + board LT7911D | dùng cầu HDMI như ở T2 |
| T4 | Độ trễ H.264 low-delay trên RV1106 có ≤ 60 ms? | đo bằng đồng hồ trên màn hình iPhone, chụp cả hai màn hình | chỉnh GOP, chia slice, jitter buffer của WebRTC |
| T5 | Nguồn: sạc iPhone trong khi phản chiếu, và dòng pass-through của LT7911D | đo dòng khi iPhone sạc + phản chiếu | mạch nguồn riêng cấp 5 V/3 A cho iPhone |
| T6 | Tắt hiệu ứng (Reduce Motion) có làm con trỏ đến đích nhanh hơn? | `abstest` + đo `abs_settle` | giữ giá trị đo cho từng máy |
| T7 | EDID dọc có bỏ được viền đen không? | đặt EDID dọc trên cổng HDMI IN của Orange Pi | cắt bằng RGA |

Rủi ro đã biết và chấp nhận được: app có DRM hiện màn đen trên đường hình; 16e, 17e và Air không xuất hình.

---

## 12. Lộ trình

1. **Giai đoạn 1, board HID (1–2 tuần, dưới $10):**
   - board phát triển nanoCH32V305, firmware v0 gồm `usb_phone`, `link` qua USB FS, `sched` và `telemetry`;
   - backend `mcu` trong `ihc`;
   - làm T1, T6;
   - so với gadget của Orange Pi và với CH9329.

   Kết quả là một con thay CH9329 dùng được ngay với Orange Pi hoặc PC.
2. **Giai đoạn 2, box ghép từ module (2–4 tuần):**
   - Luckfox Pico (RV1106) + module HDMI-sang-CSI + adapter USB-C sang HDMI + board HID;
   - dịch vụ hình H.264 WebRTC và dịch vụ điều khiển;
   - làm T4;
   - song song thử board LT7911D (T2, T3).
3. **Giai đoạn 3, PCB v1 phương án B (4–8 tuần):**
   - Core1106 + LT7911D + CH32V305 + nguồn + RJ45 + Wi-Fi;
   - vỏ, đèn trạng thái, OTA, tính năng cắm là chạy;
   - làm T5.
4. **Giai đoạn 4, tuỳ nhu cầu:**
   - bản dock (A) cho trại máy dùng PC;
   - box RK3576 cho 2 iPhone.

---

## Nguồn

**HID và MCU**
- CH32V20x/30x datasheet V3.9: https://raw.githubusercontent.com/ch32-riscv-ug/CH32V307/main/datasheet_en/CH32V20x_30xDS0.PDF
- TinyUSB: https://github.com/hathach/tinyusb (issue #1705)
- openwch/ch32v307: https://github.com/openwch/ch32v307
- Giao thức CH9329F V1.3 (bản dịch): https://github.com/Socolin/KVM-Switch (`src/legacy/ch9329.md`)

**Đường hình và PD**
- Apple, hình qua USB-C: https://support.apple.com/en-us/105099
- Apple, iPhone không có DP (16e, 17e, Air): https://support.apple.com/en-us/122208
- Apple, HDCP: https://support.apple.com/en-us/108399
- Apple USB-C Digital AV Multiport Adapter: https://www.apple.com/shop/product/mw5m3am/a/usb-c-digital-av-multiport-adapter
- Tốc độ USB trên iPhone 15: https://appleinsider.com/articles/23/09/21/usb-c-on-iphone-15-everything-you-need-to-know
- Lontium LT7911D: https://www.lontiumsemi.com/UploadFiles/2022-10/LT7911D_Brief_R1.3.pdf
- Lontium LT8711HE: https://www.lontiumsemi.com/UploadFiles/pdf/LT8711HE_Product_Brief.pdf
- Lontium LT8711UXD: https://www.lontiumsemi.com/UploadFiles/2021-07/LT8711UXD_U1_Brief_Draft1.pdf
- Driver cầu nối trong Rockchip BSP: https://github.com/rockchip-linux/kernel/tree/develop-5.10/drivers/media/i2c
- Driver LT6911UXC của InES: https://github.com/InES-HPMM/Lontium_lt6911uxc
- Công cụ cho MS2130/MS2131: https://github.com/BertoldVdb/ms-tools
- Độ trễ MS2130: https://github.com/awawa-dev/HyperHDR/discussions/499
- Infineon pdaltmode: https://github.com/Infineon/pdaltmode
- Chromium EC servo_v4: https://github.com/coreboot/chrome-ec/blob/master/board/servo_v4/usb_pd_policy.c

**SoC và sản phẩm tham khảo**
- JetKVM: https://github.com/jetkvm/kvm
- NanoKVM: https://github.com/sipeed/NanoKVM
- NanoKVM-Pro: https://github.com/sipeed/NanoKVM-Pro
- GL.iNet Comet: https://github.com/gl-inet/glkvm
- Luckfox PicoKVM: https://github.com/luckfox-eng29/kvm
- Aiden (chỉ đọc để tham khảo): https://github.com/AidenAI-IO/aiden-firmware
- Độ trễ PiKVM: https://github.com/pikvm/pikvm/blob/master/docs/latency.md
- Tài liệu Rockchip: https://github.com/DeciHD/rockchip_docs
- Phần cứng Sophgo SG200X: https://github.com/sophgo/sophgo-hardware/tree/master/SG200X
- Luckfox Core1106: https://www.waveshare.com/core1106.htm
- Luckfox Pico Zero: https://www.waveshare.com/luckfox-pico-zero.htm
- Bài thử các IP-KVM của Jeff Geerling: https://www.jeffgeerling.com/blog/2026/i-tested-every-ip-kvm/
