# Đánh giá khả thi độc lập: lớp điều khiển iPhone chỉ bằng phần cứng ngoài

- **Ngày:** 24/09/2026. iOS 27 đã phát hành ngày 14/09/2026 ([MacRumors](https://www.macrumors.com/2026/09/09/apple-announces-ios-27-release-date/)). Gần như mọi bằng chứng bên dưới là từ iOS 26 trở về trước, nên **mọi test phần cứng phải chạy trên cả iOS 26.x và iOS 27.0**.
- **Phạm vi:** chỉ lớp điều khiển iPhone: lấy hình qua HDMI, đưa lệnh qua HID. Không dùng thị giác máy (đã chốt). Không jailbreak, không Developer Mode, không cài app. Không dùng công cụ chuyên cho iPhone.
- **Cách làm:** đọc tài liệu Apple (Accessory Design Guidelines R31, bản 21/09/2026), mã nguồn các dự án mở (Aiden, PiKVM, TinyUSB, TinyPilot…), diễn đàn và bài viết kỹ thuật. Chưa có test phần cứng nào.

**Nhãn cho từng khẳng định**

| Nhãn | Nghĩa |
|---|---|
| **[Confirmed]** | Nguồn gốc (tài liệu Apple, đặc tả, mã nguồn đã đọc trực tiếp) nói rõ điều đó. |
| **[Likely]** | Có bằng chứng đáng tin (dự án đang chạy thật, báo cáo thực địa, tài liệu hãng thứ ba) nhưng chưa kiểm chứng trên phần cứng của ta, hoặc có chi tiết chưa khớp. |
| **[Unknown]** | Không tìm được nguồn đáng tin. **Cần test phần cứng.** |
| **[Contradicted]** | Bằng chứng mâu thuẫn với giả định thiết kế hiện tại. |

> **Lưu ý về nguồn.** Môi trường nghiên cứu chặn truy cập trực tiếp nhiều trang (support.apple.com,
> discussions.apple.com, huggingface.co, pikvm.github.io, forum của Raspberry Pi…). Với các trang đó, nội
> dung được lấy từ trích đoạn của công cụ tìm kiếm và được ghi **(trích đoạn)**. Nên mở lại các trang này để
> đối chiếu trước khi dựa vào. Các nguồn trên GitHub và developer.apple.com (kể cả PDF Accessory Design
> Guidelines) đã được đọc trực tiếp.

---

## 0. Cập nhật sau nghiên cứu bổ sung (24/09/2026)

Hai báo cáo mới (tiếng Anh, có nguồn và nhãn tin cậy): [chuột tuyệt đối trên iPhone](research/absolute-pointer.md)
và [phần cứng giá rẻ của Trung Quốc](research/china-market.md). Những điểm làm thay đổi đánh giá bên dưới:

| Điểm | Trước | Sau | Nguồn chính |
|---|---|---|---|
| iPhone theo **chuột tuyệt đối qua USB** (cần AssistiveTouch) | [Likely] | **[Confirmed]**: Aiden (mặc định iOS từ 04/2026), glassbox (iPhone 17 Pro Max, iOS 26.5, cùng kiểu HDMI + USB HID như dự án này), NanoKVM-Go (tài liệu cho iPhone 15/16/17), EasyClick (trích đoạn) | absolute-pointer §1, china-market §2.1 |
| Chuột tuyệt đối qua **Bluetooth** | [Unknown] | **[Likely]** trên iOS 17/18–26: mirrordeck (Bluetooth Classic, iPhone 15 Pro, iOS 26.5), firmware BLE của EasyClick và AScript (trích đoạn) | absolute-pointer §2 |
| **Digitizer / màn cảm ứng HID** như chạm thật | chưa xét | **[Contradicted]**: iOS 13.4 đã chặn; thêm collection Touch Screen cạnh chuột còn làm iOS bỏ qua nút chuột | absolute-pointer §3 |
| CH9329 ở chế độ tuyệt đối trên iPhone | [Unknown] | vẫn **[Unknown]**: chưa ai công bố descriptor của nó; bridge ESP32 USB (chỉ chuột tuyệt đối) là cấu hình tham chiếu | absolute-pointer §7 |
| Kiến trúc có "lỗi thời" so với thị trường? | | **Không.** Mọi giải pháp iPhone không jailbreak, không app đều dùng chuột + phím HID qua AssistiveTouch (thêm Full Keyboard Access). Các box "群控" của Trung Quốc lấy hình qua **AirPlay** thay vì HDMI | china-market §0–1 |

Việc đã làm theo đó:
- **Phần mềm:** hiệu chỉnh đo thời gian iOS "trượt" con trỏ tới vị trí tuyệt đối trước khi click (mặc định 0,25 s
  cho tới khi đo). Lệnh nhả nút qua báo cáo tuyệt đối gửi 3 lần. Mọi phím/nút được nhả khi iPhone vừa nhận phụ
  kiện (sửa lỗi "kẹt kéo" mà NanoKVM-Go phải có nút "Repair iPhone drag"). Chưa bao giờ gửi báo cáo tuyệt đối
  (0,0) khi không chủ ý.
- **Firmware ESP32:** bỏ Report ID khi chỉ có một collection trên BLE; báo cáo tuyệt đối khởi tạo ở giữa màn
  hình; tên/serial theo biến thể descriptor (iOS lưu cache descriptor); thứ tự interface USB để con trỏ cuối
  cùng; nhịp chạy theo 0,25 ms; báo lệnh chạy bị trễ.
- **Checklist:** T0 thêm bước lấy descriptor của CH9329; T1 chạy 4 cấu hình (CH9329 chế độ 0 và 2, ESP32 USB
  chỉ tuyệt đối, ESP32 USB tương đối + tuyệt đối) và đo thời gian trượt, nhả nút, báo cáo (0,0); T3 thêm vòng
  Caps Lock (đèn Caps Lock do iOS gửi về chứng minh iOS đã xử lý lệnh phím); T5 thêm tổ hợp Tab+phím của Full
  Keyboard Access; T9 thử chuột tuyệt đối qua BLE trước tiên.

**Lựa chọn cần chủ dự án quyết định:**
- **AirPlay thay cho HDMI, nhất là cho dòng Lightning.** Lightning có thể dùng Lightning to USB 3 Camera Adapter +
  hub để cắm CH9329 có dây (chuột tuyệt đối, không phải ghép Bluetooth), còn hình lấy qua AirPlay (UxPlay, một
  tiến trình mỗi máy, chuyển thẳng H.264). Bỏ được adapter HDMI 49 USD và capture card, nhưng thêm trễ khoảng
  100–200 ms, cần mạng cho từng máy và phải bật Screen Mirroring trên máy (làm bằng HID được). Cũng là cách duy
  nhất lấy hình từ iPhone 16e/17e.
- **Phím tắt iOS Shortcuts làm kênh phụ** (clipboard để gõ tiếng Việt, xác nhận app đã mở). Cần chủ dự án quyết
  định Shortcuts do người dùng tự tạo có tính là "cài app" không.
- **Sipeed NanoKVM-Go** (59–89 USD): một bo nhỏ làm đúng việc của bộ USB-C này (một dây USB-C, khoảng 60 ms ở
  1080p60, chế độ chuột tuyệt đối cho iPhone). Nên mua một cái để đối chiếu độ chính xác và độ trễ, và cân nhắc
  cho giai đoạn 5 (mỗi máy một bo).

---

## 1. Kết luận ngắn

### 1.1 Phán quyết

| Dòng máy | Phán quyết | Lý do chính |
|---|---|---|
| **USB-C (iPhone 15/16/17, trừ 16e và 17e)** | **CONDITIONAL GO, khả năng cao** | Aiden, dự án mở gần nhất với sản phẩm này, đang chạy đúng topology hub USB-C (HDMI + USB-A HID) với iPhone. Họ còn dùng **chuột tuyệt đối (absolute)** trên iOS, trái với giả định "iOS chỉ nhận chuột tương đối". Nếu điều này đúng với CH9329 hoặc với firmware tự làm, bài toán con trỏ "không cần nhìn" gần như đã được giải: ước lượng sai số khoảng 1–2 pt, mỗi lần tap khoảng 0,2 s. Nó còn không cần reset góc hay pacer. |
| **Lightning (iPhone 11–14, SE 2/3 trên iOS 26/27; máy cũ hơn kẹt ở iOS cũ)** | **CONDITIONAL, ưu tiên thấp** | Hình qua Lightning Digital AV Adapter là luồng H.264 được giải nén lại trong adapter, tối đa 1080p, có thêm độ trễ và nhiễu nén. Điều khiển chỉ đi được qua BLE, gần như chắc chắn là chuột tương đối có gia tốc. BLE còn có vấn đề tự kết nối lại trên iOS. Độ chính xác và độ tin cậy sẽ thấp hơn rõ rệt so với dòng USB-C. |
| **iPhone 16e / 17e** | **NO-GO cho đường HDMI** | Hai máy này có cổng USB-C nhưng **không có DisplayPort Alt Mode** (16e: nhiều nguồn xác nhận; 17e: [Likely]). Cách duy nhất để lấy hình là AirPlay, vốn là vùng xám. |
| **Mục tiêu 1: con trỏ chính xác, không mất lệnh, không thị giác** | **GO nếu có absolute; CONDITIONAL nếu chỉ có relative** | Với relative, muốn đạt 95% trong 4 pt (≈ 5 px ở khung 1080p) thì độ lặp lại của hệ số gia tốc phải tốt hơn khoảng 0,5% trên quãng 400 pt. Chưa ai đo được con số này trên iOS. "Không mất lệnh" **không thể chứng minh ở phía iOS** khi không có thị giác máy. Ta chỉ có thể biết lệnh đã tới chip, hoặc tới host USB, hoặc đã được ACK ở tầng link BLE. |
| **Mục tiêu 2: điều khiển từ xa có video trên trình duyệt** | **GO trong LAN** (MJPEG passthrough, trễ khoảng 150–250 ms); **cần H.264 cho WAN** | MJPEG tốn băng thông khoảng 10–50 Mbit/s cho mỗi người xem. Raspberry Pi 5 không có bộ mã hoá H.264 phần cứng. |

### 1.2 Rủi ro hàng đầu và test giải quyết

| # | Rủi ro | Hệ quả nếu xấu | Test (mục 7) |
|---|---|---|---|
| R1 | iOS không nhận **absolute** từ CH9329 (descriptor gộp abs + rel) | Quay về relative + reset góc, chậm và kém chính xác hơn nhiều | **T1** (và T0) |
| R2 | Relative + gia tốc không lặp lại đủ (độ lệch hệ số > 0,5%) | Không đạt mục tiêu 4 pt nếu không dùng thị giác | T4 |
| R3 | Hub không cho HDMI + HID + PD chạy cùng lúc, hoặc reset khi nguồn chập chờn | Dòng USB-C đứng máy | T2, T10 |
| R4 | Bug phím tắt Cmd/Shift/Option khi có cả bàn phím lẫn chuột. CH9329 không re-enumerate được lúc đang chạy | Phím tắt (Cmd+Space, Cmd+V…) không tin cậy | T5 |
| R5 | CH9329 ack xong nhưng báo cáo vẫn rơi hoặc bị gộp (ack ≠ đã giao) | Mất lệnh mà không biết | T0, T3 |
| R6 | Khoá máy, khởi động lại, cập nhật iOS, popup Wired Accessories | Máy "chết" khi không có người trông | T7, T10 |
| R7 | Băng thông isochronous: chỉ 1 capture card trên mỗi bus USB 2.0 | Số máy trên mỗi host thấp | T8 |
| R8 | Con trỏ AssistiveTouch không hiện trên HDMI | Người vận hành "mò" nếu điều khiển kiểu KVM relative | T6 |
| R9 | iOS 27 thay đổi hành vi (HID, AssistiveTouch, phụ kiện) | Mọi kết luận bên trên phải test lại | Chạy T1–T7 trên iOS 27 |

---

## 2. Bảng giả định

| # | Giả định (trong thiết kế ban đầu) | Trạng thái | Nguồn | Hệ quả |
|---|---|---|---|---|
| A1 | iOS chỉ nhận chuột **tương đối**. Descriptor absolute thì con trỏ đứng yên | **[Contradicted]** với USB; **[Likely]** đúng với BLE (bằng chứng cũ, 2020) | Aiden dùng absolute cho iOS: [aiden-usb-gadget](https://github.com/AidenAI-IO/aiden-firmware/blob/main/overlay-debian/usr/lib/aiden/aiden-usb-gadget), [tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go). Báo cáo BLE trên iPad iOS 13: [forum 652700](https://developer.apple.com/forums/thread/652700) | Test absolute **trước tiên**. Nếu được, bỏ reset góc và pacer trên USB-C. |
| A2 | Trên iPhone, con trỏ chỉ có khi bật AssistiveTouch | **[Confirmed]** | [Apple 111775](https://support.apple.com/en-us/111775) (trích đoạn); [README Aiden](https://github.com/AidenAI-IO/aiden-firmware) | Giữ bước cài đặt. |
| A3 | Không tắt được gia tốc, chỉ chỉnh được Tracking Speed | **[Likely]** | [Apple Community, iPadOS 26](https://discussions.apple.com/thread/256143470) (trích đoạn); [Apple Community 253828185](https://discussions.apple.com/thread/253828185) (trích đoạn) | Relative phải có mô hình gia tốc hoặc pacer. |
| A4 | Con trỏ bị kẹp ở mép màn hình, nên reset góc cho ra vị trí đã biết | **[Unknown]** | Không có nguồn cho iPhone | T4. |
| A5 | Dồn con trỏ vào góc không gây tác dụng phụ | **[Likely]** với mặc định; **[Unknown]** chi tiết | Hot Corners chỉ chạy khi bật cả AssistiveTouch lẫn Dwell ([macmost](https://macmost.com/hot-corners-on-the-iphone-and-ipad.html), trích đoạn). Về Home hoặc mở Control Center cần nhấn giữ rồi kéo ([iDownloadBlog](https://www.idownloadblog.com/2023/10/10/how-to-use-mouse-with-iphone/), trích đoạn) | Giữ Dwell, Zoom và Hot Corners tắt. Chỉ reset góc khi không giữ nút. |
| A6 | Mỗi báo cáo có ack; ack nghĩa là lệnh đã tới iOS | **[Contradicted]** (ack chỉ là chip đã nhận khung) | Tài liệu CH9329 (xem `docs/ch9329-protocol.md`). Aiden: "a successful `/dev/hidg0` write only proves that the gadget accepted the HID report" ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)) | Phải có lệnh idempotent. Xem mục 5. |
| A7 | iOS không bỏ hay gộp báo cáo | **[Contradicted]** (thực địa) | Aiden gửi nhả nút 3 lần vì "a single final release report can be missed or coalesced", và giữ tap 60 ms vì "iOS drops faster events" ([tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go)) | Lặp báo cáo nhả, đặt thời gian giữ tối thiểu. |
| A8 | Hub USB-C cho HDMI + USB-A HID + PD cùng lúc | **[Likely]** | Aiden dùng hub "Type-C hub (with HDMI and USB)" ([hardware.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/01-getting-started/hardware.md)). Apple USB-C Digital AV Multiport Adapter hỗ trợ iPhone ([mô tả sản phẩm](https://www.amazon.com/Apple-USB-C-Digital-Multiport-Adapter/dp/B0D9MMFQNL), trích đoạn) | T2. |
| A9 | Mọi iPhone USB-C đều xuất được hình | **[Contradicted]** | 16e không có DP Alt Mode ([macReports](https://macreports.com/does-iphone-16e-support-hdmi-output-what-you-need-to-know/), trích đoạn). 17e không nằm trong danh sách xuất hình qua USB-C (trích đoạn, [spec 17e](https://support.apple.com/en-us/126470)) | Loại 16e/17e khỏi dòng USB-C. |
| A10 | Cổng Lightning trên Digital AV Adapter chỉ để sạc | **[Likely]** | [Apple Community 255670035](https://discussions.apple.com/thread/255670035) (trích đoạn): HDMI và USB "only on their own". Adapter là SoC giải mã H.264 ([Panic](https://blog.panic.com/the-lightning-digital-av-adapter-surprise/)) | Lightning phải dùng Bluetooth, trừ khi T9 tìm ra adapter combo chạy được. |
| A11 | MS2130 (USB 3) tốt hơn MS2109 cho MJPEG passthrough | **[Contradicted]** (tuỳ firmware) | Trên USB 3, MS2130 chỉ có YUV; trên USB 2 mới có MJPEG ([HyperHDR #499](https://github.com/awawa-dev/HyperHDR/discussions/499), [TinyPilot #1593](https://github.com/tiny-pilot/tinypilot/discussions/1593)) | Với passthrough, dùng MS2109 hoặc cắm MS2130 vào cổng USB 2. |
| A12 | Nhiều capture card trên một host là được | **[Unknown]**, dễ bị giới hạn | "No space left on device" khi có 2 card ([ustreamer #53](https://github.com/pikvm/ustreamer/issues/53); [The Good Penguin](https://www.thegoodpenguin.co.uk/blog/multiple-uvc-cameras-on-linux/), trích đoạn) | Tính theo số bus USB 2 độc lập. T8. |
| A13 | Trang Safari trả `clientX/Y` đúng khi click bằng con trỏ AssistiveTouch | **[Unknown]** (khá có khả năng) | Con trỏ iPhone là tính năng trợ năng, không phải hệ pointer của iPadOS ([HIG](https://developer.apple.com/design/human-interface-guidelines/pointing-devices) chỉ nói Mac/iPad/Vision Pro) | T1/T3 dùng chính trang hiệu chỉnh. |
| A14 | Safari phát `pointermove` khi rê chuột trên iPhone | **[Unknown]** | Trên iPadOS: `any-hover` đổi khi cắm chuột ([WebKit 209292](https://bugs.webkit.org/show_bug.cgi?id=209292), trích đoạn); không có nguồn cho iPhone | Không thiết kế dựa vào hover. |
| A15 | 1 CSS px = 1 pt khi đặt `width=device-width` | **[Likely]** | [W3C Mobile A11y TF](https://lists.w3.org/Archives/Public/public-mobile-a11y-tf/2016Jan/0003.html) (trích đoạn) | Giữ Display Zoom = Default, `initial-scale=1`. |
| A16 | Máy phải luôn mở khoá thì phụ kiện có dây mới chạy | **[Confirmed]** (mặc định); có ngoại lệ "Always Allow" | [Apple Platform Security](https://support.apple.com/guide/security/activating-data-connections-securely-ios-sec5044aad1b/web), [Apple 111806](https://support.apple.com/en-us/111806), [Macworld](https://www.macworld.com/article/2945482/ios-26-juice-jacking-wired-accessories-always-ask.html) (trích đoạn) | Đặt Auto-Lock = Never. Cân nhắc "Always Allow". |
| A17 | BLE HID có trễ thấp và ổn định | **[Likely]** trễ khoảng 15–30 ms. Kết nối lại **[Contradicted]** một phần | ADG R31 §58.6 ([PDF](https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf)); [forum 796428](https://developer.apple.com/forums/thread/796428); [forum 695730](https://developer.apple.com/forums/thread/695730) | Cần watchdog kết nối lại. Pacer phải khớp với connection interval. |
| A18 | Có thể mô phỏng cách né bug của Aiden bằng cách đổi work mode CH9329 | **[Contradicted]** | Cấu hình CH9329 chỉ có hiệu lực ở lần cấp nguồn sau (`docs/ch9329-protocol.md`). Aiden cần re-enumerate bằng phần mềm với PID khác ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)) | Cần MCU tự làm, hoặc hai chip riêng (T5). |

---

## 3. Kết quả nghiên cứu theo chủ đề

### 3.1 Con trỏ iOS với chuột USB và Bluetooth trên iPhone

**Điều kiện và phiên bản**
- **[Confirmed]** iPhone dùng được chuột USB/Bluetooth với con trỏ tròn khi bật AssistiveTouch. Tính năng có từ iOS 13. Các nút phụ gán hành động được qua *AssistiveTouch > Devices > [thiết bị] > nút*.
  Nguồn: [Apple 111775](https://support.apple.com/en-us/111775) (trích đoạn). Aiden: "iOS control also requires AssistiveTouch to be enabled" ([README](https://github.com/AidenAI-IO/aiden-firmware)).
- **[Confirmed]** Tài liệu HIG về pointing devices chỉ nói Mac, iPad và Vision Pro. Các hiệu ứng "magnetic", highlight, lift là của iPadOS.
  Nguồn: [HIG Pointing devices](https://developer.apple.com/design/human-interface-guidelines/pointing-devices).
  **[Likely]** Suy ra: con trỏ iPhone không hút về phần tử, nên không làm lệch điểm click. Đây là suy luận từ việc tài liệu không nhắc tới iPhone.
- **[Confirmed]** iOS 26 và iOS 27 hỗ trợ iPhone 11 trở lên. iOS 18 là bản cuối cho XS/XR.
  Nguồn: [MacRumors iOS 26](https://www.macrumors.com/2025/06/09/ios-26-supports-iphone-11-and-newer/), [MacRumors iOS 27](https://www.macrumors.com/2026/09/14/ios-27-compatible-iphones/) (trích đoạn).
  Hệ quả: dòng Lightning có các máy 6s–XS chạy iOS 15–18. Ma trận test sẽ phình ra nếu muốn hỗ trợ chúng.

**Relative hay absolute: phát hiện quan trọng nhất**
- **[Confirmed]** (mã nguồn) Aiden, bản đang phát hành, dùng **descriptor chuột tuyệt đối** cho iOS. Cấu trúc: Usage Page Generic Desktop, Usage Mouse, Collection Application, Usage Pointer, Collection Physical, 8 nút, X/Y 16 bit tuyệt đối 0..32767, wheel tương đối. Interface HID không phải boot (protocol 0, subclass 0). Mặc định `device_type = "iOS"` suy ra `pointer_mode = "absolute"`.
  Nguồn: [aiden-usb-gadget](https://github.com/AidenAI-IO/aiden-firmware/blob/main/overlay-debian/usr/lib/aiden/aiden-usb-gadget), [config.go/SKILL.md qua tìm kiếm mã](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/04-agent/configuration.md).
- **[Confirmed]** (lịch sử git) Lịch sử đổi hướng của Aiden:
  - Ngày 29/04/2026, commit `651f68d` "switch to relative mouse for iOS compatibility" với lý do "iOS only supports relative".
  - Ngay hôm sau, commit `664be64` quay lại absolute với ghi chú "Testing iOS absolute positioning support".
  - Từ đó đến nay họ giữ absolute cho iOS.
  Nguồn: [lịch sử repo](https://github.com/AidenAI-IO/aiden-firmware/commits/main).
- **[Likely]** Vậy absolute qua USB chạy được trên iPhone đời hiện tại. Hai chi tiết thực địa trong mã Aiden ủng hộ điều này:
  - "iOS HID cursor mode smoothly animates the cursor toward the target". Họ đợi 80 ms trước khi nhấn, nếu không cú nhấn thành thao tác kéo.
  - Toạ độ 0..32767 phủ đúng vùng màn hình điện thoại (phần ảnh đã bỏ viền đen).
  Nguồn: [tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go).
  Chưa rõ model iPhone và bản iOS họ dùng.
- **[Likely]** Trên iPad, PiKVM mặc định dùng absolute. Một issue năm 2024 báo di chuyển chuột chạy được nhưng click trái không nhận (đã được đánh dấu fixed).
  Nguồn: [PiKVM mouse.md](https://github.com/pikvm/pikvm/blob/master/docs/mouse.md), [PiKVM #1202](https://github.com/pikvm/pikvm/issues/1202).
- **[Likely]** Absolute qua **BLE** thì chưa được chứng minh.
  - Năm 2020, trên iPad iOS 13 với nRF52: "the mouse no longer moves" ([forum 652700](https://developer.apple.com/forums/thread/652700)).
  - Năm 2022 có câu hỏi tương tự, không ai trả lời ([forum 712996](https://developer.apple.com/forums/thread/712996)).
  - Thư viện ESP32-BLE-Abs-Mouse không đánh dấu iOS là tương thích ([README](https://github.com/sobrinho/ESP32-BLE-Abs-Mouse)).
  - Chưa ai thử lại với descriptor kiểu PiKVM/Aiden trên iOS mới. **[Unknown]** T9.
- **[Likely]** Digitizer (touchscreen) không được iOS chuyển thành con trỏ.
  Nguồn: commit Aiden `d65e498` "macOS/iOS do not automatically convert Digitizer input to cursor movement".
- **[Unknown]** CH9329 gộp chuột relative (report ID 1) và absolute (report ID 2, lưới 4096) trong cùng chức năng chuột, còn Aiden dùng một interface chỉ có absolute. iOS có nhận lệnh `0x04` của CH9329 hay không là **câu hỏi số 1** (T1).

**Gia tốc, Tracking Speed và các tuỳ chọn**
- **[Likely]** Không có công tắc tắt gia tốc trên iPadOS/iOS, kể cả iPadOS 26. Chỉ có Tracking Speed và Trackpad Inertia (dành cho trackpad).
  Nguồn: [Apple Community 256143470](https://discussions.apple.com/thread/256143470) (trích đoạn), [AbilityNet iOS 26](https://mcmw.abilitynet.org.uk/how-to-use-a-mouse-or-trackpad-with-your-iphone-or-ipad-in-ios-26) (trích đoạn).
- **[Likely]** Tracking Speed nằm ở *Settings > General > Trackpad & Mouse*, và có cả một thanh trượt trong *Accessibility > Touch > AssistiveTouch*. Chưa rõ hai thanh này có liên kết với nhau không.
  Nguồn: [Apple: Use AssistiveTouch](https://support.apple.com/guide/iphone/use-assistivetouch-iph96b21954/ios) (trích đoạn). Hệ quả: khoá cả hai và ghi lại giá trị.
- **[Confirmed]** Pointer Control trên iPhone có: Increase Contrast, Automatically Hide Pointer, Color, Pointer Size, Scrolling Speed. Pointer Animations có trong tài liệu nhưng nhiều khả năng chỉ tác động trên iPad.
  Nguồn: [Apple iPhone guide](https://support.apple.com/guide/iphone/adjust-pointer-settings-iphec6e1e60b/ios) (trích đoạn).
- **[Likely]** Khi xoay ngang với chuột Bluetooth relative, trục X/Y bị đảo và con trỏ không qua được giữa màn hình. Lỗi được báo từ iOS 16.5 và vẫn còn ở iOS 18.5 (FB17806167). Chưa rõ tình trạng trên iOS 26/27 và với absolute.
  Nguồn: [forum 786963](https://developer.apple.com/forums/thread/786963).
  Hệ quả: tạm thời chỉ hỗ trợ màn hình dọc.

**Mép và góc màn hình**
- **[Unknown]** Không có nguồn nào nói con trỏ iPhone bị kẹp ở mép. Hành vi này là bắt buộc để reset góc chạy được. Cũng chưa rõ màn hình bo góc có làm "góc" thực tế lệch vào trong không (T4).
- **[Likely]** Về Home bằng chuột: đưa con trỏ xuống đáy, nhấn giữ và kéo lên. Mở Control Center hoặc Notification Center: nhấn giữ ở mép trên rồi kéo xuống. Chỉ di chuyển (không nhấn) thì không kích hoạt gì.
  Nguồn: [iDownloadBlog](https://www.idownloadblog.com/2023/10/10/how-to-use-mouse-with-iphone/) (trích đoạn).
- **[Likely]** Hot Corners chỉ chạy khi bật cả AssistiveTouch và Dwell. Dwell mặc định tắt (suy luận, chưa có nguồn nói rõ; kiểm tra khi cài máy).
  Nguồn: [macmost](https://macmost.com/hot-corners-on-the-iphone-and-ipad.html), [Apple AssistiveTouch](https://support.apple.com/guide/iphone/use-assistivetouch-iph96b21954/ios) (trích đoạn).
- **[Likely]** Nếu bật Zoom với Zoom Pan "Edges", màn hình sẽ trượt khi con trỏ chạm mép. Phải tắt Zoom.
  Nguồn: cùng trích đoạn tìm kiếm AssistiveTouch ở trên.
- **[Likely]** Trên iPadOS, đẩy con trỏ quá mép dưới (không cần nhấn) sẽ mở Dock/App Switcher ([Apple Universal Control](https://support.apple.com/en-us/102459), trích đoạn). Chưa rõ iPhone có làm vậy không, nên **ưu tiên dùng hai góc trên để neo**.

**Nút phải và nút giữa**
- **[Confirmed]** Nút phụ gán được hành động Home, App Switcher… ([Apple 111775](https://support.apple.com/en-us/111775), trích đoạn).
- **[Likely]** Phím tắt Cmd+H cũng về Home với bàn phím ngoài ([Gadget Hacks](https://ios.gadgethacks.com/how-to/tired-tapping-use-external-keyboard-your-iphone-and-unlock-tons-keyboard-shortcuts-0385569/), trích đoạn). Nhưng nó dính bug modifier ở mục 3.4, nên **nút chuột gán Home đáng tin hơn**.

### 3.2 Việc giao báo cáo HID tới iOS

**USB (CH9329 hoặc MCU)**
- **[Confirmed]** Với thiết bị USB full-speed, endpoint interrupt khai `bInterval` từ 1 đến 255 ms. Host poll không thưa hơn giá trị đó. Mỗi lần poll lấy tối đa một báo cáo cho mỗi endpoint.
  Nguồn: đặc tả USB 2.0 §9.6.6 ([usb.org](https://www.usb.org/document-library/usb-20-specification)). Chuột thường khai 8 ms, chuột game 1 ms ([ghi chú](https://github.com/ventixy/notebook/blob/master/src/python/Project/hid.md)).
- **[Unknown]** `bInterval` của CH9329, và chip xử lý thế nào khi nhận khung serial nhanh hơn nhịp poll: xếp hàng, ghi đè hay trả lỗi.
  - Với relative, ghi đè nghĩa là **mất quãng đường**.
  - Không tìm thấy tài liệu công khai. Tool của cùng hãng cho CH9350L phải chèn mặc định 8 ms giữa hai báo cáo để chip "does not drop characters" ([wch-usb-hid-serial-keycode-tools](https://github.com/sunasaji/wch-usb-hid-serial-keycode-tools)). Đây là dấu hiệu gián tiếp, không phải cùng chip.
  - **T0 đo được trên một PC Linux, chưa cần iPhone.**
- **[Likely]** iOS có lúc ngừng poll HID. Khi đó hàng đợi phía thiết bị đầy và lệnh ghi bị chặn.
  Nguồn: Aiden đặt timeout 750 ms cho mỗi lần ghi vì "Linux hidg writes can otherwise block forever when the USB host stops polling" ([tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go)).
  Với CH9329, chưa rõ lúc đó chip trả `00`, `E6` hay im lặng (T0, T7).
- **[Likely]** iOS có thể bỏ lỡ hoặc gộp báo cáo. Aiden phải làm mấy việc sau:
  - Lặp báo cáo nhả nút 3 lần, cách nhau 15 ms.
  - Giữ tap 60 ms.
  - Giữ bộ phím kèm modifier 120 ms.
  - Chờ con trỏ ổn định 80 ms trước khi nhấn.
  Nguồn: [tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go).
- **[Confirmed]** Ở 9600 baud, một khung chuột relative kèm ack mất 18 byte trên dây, tức 18,75 ms. Cộng 3 ms ngưỡng "packet interval" của chip, tổng khoảng 22–24 ms. **Pacer 25 ms gần như không còn biên**: mỗi lần trễ hay retry đều làm lệch nhịp, và nhịp lệch thì gia tốc lệch. Ở 115200 baud chỉ còn khoảng 1,6 ms + 3 ms.
  Nguồn: tính từ khung trong `docs/ch9329-protocol.md`.

**Bluetooth LE (ESP32)**
- **[Confirmed]** Theo Apple ADG R31 (21/09/2026) §58.6, trang 352:
  - Interval Min ≥ 15 ms và là bội của 15 ms.
  - Peripheral Latency ≤ 30.
  - Supervision Timeout từ 6 đến 18 s.
  - "If Bluetooth Low Energy HID is one of the connected services… a connection interval down to 11.25 ms may be accepted by some devices."
  - Có thiết bị sẽ đề xuất 30 ms.
  Nguồn: [Accessory Design Guidelines PDF](https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf).
- **[Confirmed]** Kỹ sư Apple (DTS) xác nhận chữ "may": iOS chấp nhận hay đổi connection interval tuỳ tải và trạng thái máy.
  Nguồn: [forum 796428](https://developer.apple.com/forums/thread/796428).
- **[Likely]** Hệ quả:
  - Báo cáo tới iOS theo lô, mỗi lô một connection event (11,25, 15 hoặc 30 ms).
  - Trễ trung bình khoảng nửa interval cộng xử lý.
  - Pacer phải gửi **đúng một báo cáo mỗi connection event** và dùng interval là bội của connection interval. Nếu không, các báo cáo dồn vào một event sẽ có timestamp sát nhau, làm lệch ước lượng vận tốc và do đó lệch gia tốc.
  - Suy luận này cần T9 xác nhận.
- **[Likely]** iOS không tự kết nối lại thiết bị BLE HID đã bond khi thiết bị quay lại vùng phủ sóng. Người dùng phải bấm tên thiết bị trong Settings, và vấn đề chưa được giải quyết.
  Nguồn: [forum 695730](https://developer.apple.com/forums/thread/695730).
  Đây là rủi ro lớn cho vận hành không người.
- **[Likely]** Tín hiệu "đã giao" tốt nhất của BLE là ACK ở tầng link. NimBLE báo sự kiện notify đã gửi, nghĩa là controller của iPhone đã nhận, chứ không phải iOS đã xử lý. Suy luận từ cơ chế BLE; cần xác nhận khi viết firmware.

### 3.3 Safari trên iPhone làm đích hiệu chỉnh

- **[Unknown]** Click từ con trỏ AssistiveTouch có sinh `pointerdown`/`click` với `clientX/Y` đúng hay không.
  - **[Likely]** Nhiều khả năng là có, và dưới dạng *touch* (`pointerType = "touch"`), vì AssistiveTouch giả lập ngón tay. Aiden thấy thao tác kéo bằng chuột giữ "fling velocity" giống vuốt tay ([hid_provider.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/mnk/hid_provider.go)).
  - Trang hiệu chỉnh phải ghi **mọi** sự kiện (`pointer*`, `touch*`, `mouse*`, `click`) kèm `pointerType` để T1 trả lời.
- **[Unknown]** Có `pointermove`/`mousemove` khi chỉ rê chuột (hover) trên iPhone không.
  - Trên iPadOS, WebKit có hỗ trợ hover với trackpad: `any-hover` đổi khi cắm chuột ([WebKit 209292](https://bugs.webkit.org/show_bug.cgi?id=209292), trích đoạn).
  - Có một bug iOS phát `pointerenter` kiểu mouse kèm touch ([WebKit 214609](https://bugs.webkit.org/show_bug.cgi?id=214609), trích đoạn).
  - iOS 26 có "Hover Text" cho con trỏ ở cấp hệ thống ([AbilityNet](https://mcmw.abilitynet.org.uk/how-to-display-a-larger-version-of-text-when-you-hover-it-with-the-pointer-in-ios-26-on-your-iphone-or-ipad), trích đoạn), nhưng điều đó không bảo đảm sự kiện web. **Không thiết kế dựa vào hover.**
- **[Likely]** Với `<meta name="viewport" content="width=device-width, initial-scale=1">`, 1 CSS px = 1 pt ([W3C](https://lists.w3.org/Archives/Public/public-mobile-a11y-tf/2016Jan/0003.html), trích đoạn). Điều kiện: Display Zoom = Default và trang không bị phóng to.
  - **Cẩn thận:** click AssistiveTouch là "tap", nên hai click nhanh có thể thành double-tap-zoom và làm lệch toàn bộ toạ độ.
  - Phải đặt `maximum-scale=1, user-scalable=no` và `touch-action: none` (khuyến nghị kỹ thuật, chưa kiểm chứng).
- **Độ lệch của trang so với màn hình:**
  - **[Likely]** Từ iOS 26, mọi trang được "Add to Home Screen" mặc định mở như web app (không có giao diện Safari), với công tắc "Open as Web App" bật sẵn ([heise](https://www.heise.de/en/news/iOS-26-and-iPadOS-26-Changed-web-app-behaviour-on-the-home-screen-10749652.html), [iDownloadBlog](https://www.idownloadblog.com/2025/06/17/apple-ios-26-safari-web-apps-home-screen-bookmarks/), trích đoạn).
  - **[Likely]** Kết hợp với `viewport-fit=cover` và `apple-mobile-web-app-status-bar-style=black-translucent`, gốc (0,0) của trang trùng gốc màn hình. Cần đo xác nhận trong T1.
  - **Khuyến nghị:** dùng web app chạy standalone. Không cố suy ra offset trong tab Safari, vì thanh địa chỉ và tab bar của iOS 15–26 co giãn.
  - Không đo ở vùng status bar: tap ở đó có thể bị hệ thống dùng để cuộn lên đầu.
- **[Likely]** Cmd+Space mở Spotlight và Cmd+L đưa con trỏ vào ô địa chỉ Safari trên iPhone khi có bàn phím ngoài ([Gadget Hacks](https://ios.gadgethacks.com/how-to/tired-tapping-use-external-keyboard-your-iphone-and-unlock-tons-keyboard-shortcuts-0385569/), [Apple: external keyboard](https://support.apple.com/guide/iphone/control-iphone-with-an-external-keyboard-ipha4375873f/ios), trích đoạn). **Cả hai đều dính bug modifier** (mục 3.4).
- **[Unknown]** Gõ một URL vào Spotlight rồi Return có mở thẳng Safari không. Tài liệu chỉ nói Spotlight gợi ý "tìm trong Safari" ([AppleInsider](https://appleinsider.com/inside/ios-17/tips/how-to-use-the-new-spotlight-in-ios-17), trích đoạn). Đường chắc hơn là Cmd+Space, gõ "Safari", Return, rồi Cmd+L và gõ URL. Hoặc mở web app một lần bằng tay khi cài máy.

### 3.4 Lightning, Bluetooth, và chọn chip HID

**Vì sao Lightning cần Bluetooth**
- **[Confirmed]** Lightning Digital AV Adapter mirror tối đa 1080p ([Apple](https://www.apple.com/shop/product/mw2p3am/a/lightning-digital-av-adapter), trích đoạn). Bên trong có SoC ARM và 256 MB RAM. iPhone nén H.264 rồi gửi qua Lightning, adapter giải nén ra HDMI; bản đầu chỉ ra 1600×900 kèm nhiễu nén ([Panic](https://blog.panic.com/the-lightning-digital-av-adapter-surprise/)).
  Hệ quả: hình Lightning có thêm trễ và nhiễu nén so với DP Alt Mode. Chưa có số đo trễ, **[Unknown]** T9.
- **[Likely]** Cổng Lightning trên adapter chỉ để sạc qua. Người dùng báo HDMI và USB (bàn phím/chuột) chạy được, nhưng "only on their own — not in combination at the same time" ([Apple Community 255670035](https://discussions.apple.com/thread/255670035), trích đoạn).
- **[Unknown]** Adapter combo bên thứ ba "Lightning → HDMI + USB OTG + sạc" có bán ([ví dụ](https://www.amazon.com/Lightning-Certified-Charging-Keyboard-Projector/dp/B09J869ZV2)). Chưa có bằng chứng HDMI và HID chạy **cùng lúc**. Thử một chiếc ở T9 rẻ hơn nhiều so với tự đoán.
- **[Likely]** Lightning to USB 3 Camera Adapter nhận bàn phím và chuột HID, và có cổng Lightning để sạc đồng thời ([Apple Wiki/Fandom](https://apple.fandom.com/wiki/Lightning_to_USB_3_Camera_Adapter), trích đoạn). Nhưng nó không cho hình, nên phải kèm AirPlay. Đây là vùng xám cần duyệt.
- **[Likely]** Board thương mại (iMouse, và nhiều khả năng cả SOME 3C) dùng **AirPlay cho hình + HID qua OTG**. Xem 3.8.

**USB-C có nên cũng đi Bluetooth (ESP32) không?**

| Tiêu chí | CH9329 hoặc MCU qua USB (hub) | ESP32 qua BLE |
|---|---|---|
| Absolute pointer | **[Likely]** được (Aiden) | **[Unknown]**, bằng chứng cũ nói không |
| Trễ và độ đều | Poll 1–8 ms, đều | 11,25/15/30 ms theo lô, iOS có thể đổi (ADG §58.6) |
| Kết nối lại sau reboot hoặc khi ra khỏi vùng phủ | Tự động khi cắm (qua Wired Accessories) | **[Likely]** có lỗi không tự nối lại ([forum 695730](https://developer.apple.com/forums/thread/695730)) |
| Ghép đôi | Không cần | Phải bond từng máy. Ghép nhầm máy A với ESP32 B là lỗi nguy hiểm |
| Nhiều máy trong một phòng | Không nhiễu | 2,4 GHz chung với Wi-Fi. Mấy chục kết nối BLE vẫn có thể chạy nhờ nhảy tần, nhưng **[Unknown]** ở quy mô 50+ |
| Chịu Wired Accessories / USB Restricted Mode | Có | Không (lợi thế của BLE) |
| Bug modifier | Có (Aiden) | **[Unknown]** |
| Phần cứng thêm | Hub vẫn cần cho HDMI + PD; CH9329 cắm vào USB-A | Hub vẫn cần cho HDMI + PD; ESP32 không cắm vào hub |

**Kết luận:** không nên chuyển USB-C sang BLE. Lợi ích duy nhất đáng kể là né được Wired Accessories. Cái giá phải trả là mất absolute, thêm độ trễ lô và rủi ro kết nối lại.

**MCU tự làm (ESP32-S3 / RP2040, TinyUSB) thay CH9329 trên USB-C?**
- **[Confirmed]** TinyUSB có callback `tud_hid_report_complete_cb`, "Invoked when sent REPORT successfully to host" ([hid_device.h](https://github.com/hathach/tinyusb/blob/master/src/class/hid/hid_device.h)).
  Tức là firmware biết **host đã thực sự lấy báo cáo**. Đó là mức "đã giao" mạnh nhất có được mà không cần thị giác, mạnh hơn hẳn ack "đã nhận khung" của CH9329.
- **[Confirmed]** Cách né bug modifier của Aiden:
  - Chuyển sang profile không có pointer, với **PID và serial khác** để iOS không dùng lại descriptor cũ.
  - Gửi phím tắt xong thì khôi phục profile đầy đủ.
  - Thứ tự interface bắt buộc: keyboard → Consumer Control → pointer → ECM. Đặt pointer ngay sau keyboard thì bàn phím ảo chỉ hiện lại khoảng 80% số lần; thứ tự mới đạt 10/10.
  - Không được unbind/rebind cùng danh tính khi cáp vẫn cắm, vì iOS giữ trạng thái lệch.
  Nguồn: [usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md), commit `c001083`.
- **[Likely]** iOS chốt layout bàn phím phần cứng tại thời điểm enumerate, theo bàn phím ảo đang chọn lúc đó ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)). Mỗi lần re-enumerate là một lần chốt lại.
- **[Contradicted]** CH9329 không làm được việc re-enumerate này lúc đang chạy, vì cấu hình chỉ có hiệu lực ở lần cấp nguồn sau.
  **[Unknown]** Còn một phương án chưa ai thử: **hai CH9329 riêng**, một chiếc chỉ bàn phím (mode 0x01), một chiếc chỉ chuột (mode 0x02). Aiden mô tả bug là khi bàn phím và pointer "advertised by the same USB composite". Nếu bug theo từng composite thì hai chip riêng sẽ né được; nếu theo toàn hệ thống thì không (T5).
- **[Likely]** Rủi ro phía MCU: TinyUSB trên ESP32-S3 từng có lỗi HID ("Recent updates broke `tud_hid_n_ready`", [TinyUSB #2825](https://github.com/hathach/tinyusb/issues/2825)). Phải ghim phiên bản. RP2040 là nền tham chiếu của TinyUSB nhưng không có BLE.
- **Đánh giá:**
  - MCU tự làm tốt hơn CH9329 ở bốn điểm: (1) descriptor absolute sạch, giống Aiden; (2) hai profile để né bug modifier; (3) ack "đã giao" thật; (4) pacer chạy trên MCU, khớp với khung USB, không bị jitter từ host.
  - Nhược điểm là phải phát triển firmware.
  - CH9329 vẫn là công cụ tốt nhất cho GĐ 0 vì rẻ và có sẵn.

### 3.5 iPhone 15/16/17 với hub USB-C

- **[Confirmed]** iPhone dùng giao thức DisplayPort để xuất tới màn hình USB-C, tối đa 4K60. Bản Pro của 15/16 có USB 3; các bản khác là USB 2 (đủ cho HID).
  Nguồn: [Apple 105099](https://support.apple.com/en-us/105099) (trích đoạn).
- **[Contradicted]** "Mọi iPhone USB-C": 16e không có DP Alt Mode, 17e cũng không nằm trong danh sách. iPhone Air thì có DP.
  Nguồn: [macReports](https://macreports.com/does-iphone-16e-support-hdmi-output-what-you-need-to-know/), [spec iPhone Air](https://support.apple.com/en-us/125092) (trích đoạn).
- **[Likely]** Apple USB-C Digital AV Multiport Adapter (HDMI 4K60 + USB-A + USB-C PD 60 W) được mô tả là dùng được với iPhone ([listing](https://www.amazon.com/Apple-USB-C-Digital-Multiport-Adapter/dp/B0D9MMFQNL), trích đoạn). Đây là **hub tham chiếu** nên mua đầu tiên. Hub hãng khác (Anker, Cable Matters, UGREEN…) phải tự kiểm chứng.
  **[Unknown]** Chưa có danh sách mẫu hub nào được kiểm chứng chạy HID + capture + PD cùng lúc trên iPhone. Aiden không ghi tên hub của họ.
- **[Likely]** Hub hay trục trặc khi cắm nguồn sau; nên cắm sạc PD vào hub **trước** rồi mới cắm iPhone ([Lention](https://www.lention.com/blogs/news/usb-c-hub-iphone-17-16-15-hdmi-storage-charging), trích đoạn, nguồn là blog của hãng bán hub).
- **[Likely]** ADG chỉ ghi iPad USB-C hỗ trợ USB PD Fast Role Swap (§25.3, [ADG PDF](https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf) trang 239), không nhắc iPhone.
  Suy ra: nếu nguồn PD vào hub chập chờn, hub mất nguồn và reset, **cả HDMI lẫn HID rớt rồi enumerate lại**. Đây là suy luận, cần T10.
- **[Confirmed]** iPhone cấp tối đa 900 mA (4,5 W) cho phụ kiện qua PD. Qua Type-C Current mặc định: 500 mA với 15/16/17 thường, 900 mA với bản Pro (ADG Table 25-1). Đủ cho CH9329 hoặc MCU, không đủ cho capture card (card lấy nguồn từ host).
- **[Likely]** Khi mirror, iPhone giữ tỉ lệ khoảng 19,5:9 và thêm viền đen hai bên trên khung 16:9. App không tắt được việc này ([Apple Community](https://discussions.apple.com/thread/255537489), [externaldisplayiphone](https://externaldisplayiphone.com/blog/iphone-external-display-no-black-bars/), trích đoạn).
  - Ở khung 1080p, máy để dọc chiếm khoảng 497×1080 px. Với iPhone 15 (393×852 pt) tức khoảng 1,27 px/pt, và 5 px ≈ 4 pt.
- **[Likely]** HDCP chỉ bật với nội dung có bảo vệ; nội dung đó ra màn đen trên capture card. Chấp nhận được ([Apple Community](https://discussions.apple.com/thread/252537556), trích đoạn).
- **[Unknown]** Có hub luôn bật HDCP làm capture card mất hình không (T2).

### 3.6 Wired Accessories, USB Restricted Mode, khoá màn hình

- **[Confirmed]** Quy tắc USB Restricted Mode:
  - Sau 1 giờ kể từ khi máy khoá (hoặc kể từ khi kết nối dữ liệu cuối cùng kết thúc), máy không cho kết nối dữ liệu mới cho tới khi mở khoá.
  - Trong 1 giờ đó, chỉ phụ kiện đã từng kết nối lúc máy mở khoá được phép. Danh sách này được nhớ 30 ngày.
  - Phụ kiện lạ cố kết nối sẽ khoá mọi kết nối dữ liệu.
  - Quá 3 ngày không có kết nối dữ liệu nào thì máy chặn ngay khi khoá.
  Nguồn: [Apple Platform Security](https://support.apple.com/guide/security/activating-data-connections-securely-ios-sec5044aad1b/web) (trích đoạn).
- **[Confirmed]** iOS 26 có *Privacy & Security > Wired Accessories* với 4 lựa chọn; mặc định là "Automatically Allow When Unlocked". **Máy Lightning chỉ có "Automatically Allow When Unlocked" và "Always Allow".**
  Nguồn: [Macworld](https://www.macworld.com/article/2945482/ios-26-juice-jacking-wired-accessories-always-ask.html), [Certo](https://www.certosoftware.com/insights/new-ios-26-security-setting-aims-to-curb-juice-jacking-risks-on-iphone/) (trích đoạn).
- **[Confirmed]** iOS 26.0.1 có bug khoá cứng lựa chọn ở "Always Allow". iOS 26.2 đã sửa, nhưng đặt lại mặc định thành Always Allow kể cả với người đã chọn khác.
  Nguồn: [TidBITS](https://tidbits.com/2025/10/13/juice-jacking-protection-setting-broken-in-ios-26/) (trích đoạn).
  Hệ quả: **sau mỗi lần cập nhật iOS phải kiểm tra lại mục này.**
- **[Likely]** Apple viết: "if you use a USB assistive device to enter your passcode on your locked iPhone, many assistive devices automatically turn on the setting to allow USB devices" ([Apple 111806](https://support.apple.com/en-us/111806), trích đoạn).
  Tức là với "Always Allow", HID có dây gõ được passcode trên màn khoá.
  **[Unknown]** Chưa rõ điều này có đúng ngay sau khi khởi động lại (BFU) không (T7).
- **[Likely]** Bàn phím Bluetooth đã ghép đánh thức được máy và gõ passcode trên màn khoá ([Mac OS X Hints](http://hints.macworld.com/article.php?story=20130819065232161), trích đoạn). Ngay sau khi khởi động lại thì **[Unknown]**.
- **[Likely]** Từ iOS 18.1, máy tự khởi động lại về BFU khi ở trạng thái khoá liên tục 72 giờ ([Magnet Forensics](https://www.magnetforensics.com/blog/understanding-the-security-impacts-of-ios-18s-inactivity-reboot/), trích đoạn). Nếu giữ máy luôn mở khoá (Auto-Lock = Never) thì nhiều khả năng không bị ảnh hưởng. Đây là suy luận từ cơ chế đếm theo thời gian khoá.
- **Hệ quả vận hành:**
  - Auto-Lock = Never.
  - Tắt tự cập nhật iOS.
  - Chọn giữa "Always Allow" (sống sót qua khoá hoặc khởi động lại, nhưng kém an toàn) và "Automatically Allow When Unlocked" (an toàn, nhưng phải có người mở khoá sau mỗi sự cố).
  - Với farm máy chuyên dụng, **khuyến nghị "Always Allow"** và ghi rõ đánh đổi an ninh.
  - Low Power Mode ép Auto-Lock về 30 giây, phải tắt (đã có trong `docs/iphone-setup.md`).

### 3.7 Luồng hình

**Capture card**
- **[Likely]** MS2109: USB 2.0, MJPEG 1920×1080@30 và 1280×720@60; YUYV chỉ ở độ phân giải hoặc fps thấp. Card báo "USB 3.0" thường thực ra là USB 2.0.
  Nguồn: [naut.ca](https://www.naut.ca/blog/2020/07/09/cheap-hdmi-capture-card-review/) (trích đoạn), [TinyPilot wiki](https://github.com/tiny-pilot/tinypilot/wiki/HDMI-Capture-Devices).
- **[Likely]** MS2130:
  - Trên USB 3: chỉ YUV, 1080p60 và cả 1080p120. Trễ đo được khoảng 66 ms ở 1080p60 và 49 ms ở 1080p120.
  - Trên USB 2: MJPEG.
  - Firmware mới đổi danh sách mode.
  Nguồn: [HyperHDR #499](https://github.com/awawa-dev/HyperHDR/discussions/499). TinyPilot cũng xác nhận MS2130 ra YUYV nên phải nén bằng CPU ([#1593](https://github.com/tiny-pilot/tinypilot/discussions/1593)).
  **Hệ quả:** "MS2130 cho MJPEG passthrough" là sai trên USB 3.
- **[Likely]** TinyPilot trên Pi 4 với MS2109 đạt khoảng 200 ms trễ nhờ passthrough MJPEG qua uStreamer, giảm từ 500–600 ms khi nén lại ([mtlynch.io](https://mtlynch.io/tinypilot/), trích đoạn).

**Nhiều card trên một host**
- **[Likely]** Lỗi "No space left on device" là do thiếu băng thông isochronous, không phải thiếu ổ đĩa. uvcvideo có xu hướng giữ trước băng thông của mode cao nhất ([The Good Penguin](https://www.thegoodpenguin.co.uk/blog/multiple-uvc-cameras-on-linux/), trích đoạn; [ustreamer #53](https://github.com/pikvm/ustreamer/issues/53); [linux-uvc-devel](https://linux-uvc-devel.narkive.com/hK5VJA2r/uvcvideo-overallocates-bandwidth-for-compressed-e-g-mjpg-video-proposed-fix)).
- **Tính toán** (USB 2.0 high-speed, 80% cho periodic ≈ 6000 B mỗi microframe):
  - Card giữ 3×1024 B/µframe (khoảng 24,6 MB/s) thì mỗi bus USB 2 **chỉ chứa 1 card**.
  - Nếu uvcvideo chọn alt setting 1×1024 (khoảng 8,2 MB/s) thì được khoảng 5 card.
  - MJPEG 1080p30 thực tế chỉ cần khoảng 2–6 MB/s.
  - **[Unknown]** Chưa rõ MS2109 khai `dwMaxPayloadTransferSize` bao nhiêu (T8).
- **[Confirmed]** Pi 4: "The USB 2.0 lines on all four ports are connected to a single USB 2.0 hub within the VL805", tức **một bus USB 2** cho mọi thiết bị USB 2.
  Nguồn: [usb-bus-on-raspberry-pi.adoc](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/raspberry-pi/usb-bus-on-raspberry-pi.adoc), đọc trực tiếp.
- **[Likely]** Pi 5: hai xHCI độc lập (RP1), tức hai bus USB 2 và hai bus USB 3.
  Nguồn: [Raspberry Pi docs I/O controllers](https://www.raspberrypi.com/documentation/computers/io-controllers.html) (trích đoạn).
- **[Confirmed]** Cùng tài liệu đó còn ghi hai giới hạn khác:
  - Nguồn cấp qua cổng USB: Pi 4 tổng 1200 mA; Pi 5 600 mA (với nguồn 3 A) hoặc 1600 mA (với nguồn 5 A). Vài capture card cộng adapter USB-serial có thể vượt mức này, nên phải dùng hub có nguồn riêng.
  - Hub **Single-TT** không ổn định khi cắm nhiều thiết bị full/low-speed; nên dùng hub **Multi-TT**. CH340/CP2102 và CH9329 đều là thiết bị full-speed, nên khi gom nhiều cáp serial vào một hub phải chọn hub Multi-TT.
- **[Likely]** Pi 5 **không có bộ mã hoá H.264 phần cứng**; nén 1080p30 tốn khoảng 50% CPU ([Pi forum](https://forums.raspberrypi.com/viewtopic.php?t=376952), [XDA](https://www.xda-developers.com/modern-raspberry-pi-surprisingly-powerful-transcoder/), trích đoạn).

**Gửi hình tới trình duyệt**
- **[Confirmed]** PiKVM (V4, CSI bridge, WebRTC H.264, 1080p60) đạt 35–50 ms trễ tổng. Họ gọi MJPEG là "Legacy", "Low latency if you have a good network" nhưng "Consumes a HUGE amount of traffic", chỉ dùng được qua cáp.
  Nguồn: [PiKVM latency.md](https://github.com/pikvm/pikvm/blob/master/docs/latency.md), [video.md](https://github.com/pikvm/pikvm/blob/master/docs/video.md).
- **[Likely]** JetKVM công bố 30–60 ms (H.264). Một bài review đo được khoảng 98 ms từ click tới khi màn hình đổi ([CNX](https://www.cnx-software.com/2025/03/21/jetkvm-a-69-kvm-over-ip-solution-with-open-source-software/), [WarpKVM](https://warpkvm.com/blog/jetkvm-review), trích đoạn).
- **[Unknown]** Con trỏ AssistiveTouch có hiện trên tín hiệu HDMI không. Các báo cáo mâu thuẫn:
  - Một báo cáo nói con trỏ không hiện trên TV khi mirror.
  - Báo cáo khác (iPadOS qua AirPlay) nói có hiện.
  Nguồn: [Apple Community 250082432](https://discussions.apple.com/thread/250082432), [Air Squirrels](https://blog.airsquirrels.com/how-to-view-and-record-on-screen-taps-with-mouse-support-in-ipados) (trích đoạn).
  Automation không cần điều này (không có thị giác máy). **Nhưng điều khiển kiểu KVM relative thì cần** (T6).

### 3.8 Quy mô và sản phẩm

- **[Likely]** SOME 3C (khoảng 38 USD mỗi board):
  - Mỗi board một máy, hỗ trợ iPhone 6s trở lên với iOS 15 trở lên, có bản cáp Lightning và USB-C.
  - Lên mạng qua "OTG network cable"; có API HTTP, WebSocket và Python.
  - Không công bố cách lấy hình.
  Nguồn: [SOME 3C](https://some3c.com/products/iphone-farm-ios-automation-control-board), [Medium](https://some3ccom.medium.com/iphone-box-phone-farm-ios-without-jailbreak-controlling-iphone-via-computer-unified-control-4cce12c55e09) (trích đoạn).
- **[Likely]** iMouse: phần cứng HID cắm qua OTG, hình lấy qua **AirPlay mirroring** ("有线投屏" qua đường mạng OTG), điều khiển bằng nhận dạng ảnh, màu và OCR, yêu cầu iOS 13.5 trở lên.
  Nguồn: [iosautot](https://www.iosautot.cn/python-xp/), [CSDN](https://blog.csdn.net/SANGLIJUN/article/details/134271273) (trích đoạn).
  Tức là board thương mại **dựa vào thị giác máy và AirPlay**, đúng hai thứ dự án này đã loại. Đừng kỳ vọng sao chép được độ ổn định của họ mà không có hai thứ đó, trừ khi absolute chạy được.
- **[Confirmed]** iPhone 15 trở lên có giới hạn sạc 80%; iOS 18 cho chọn 80/85/90/95/100%, thỉnh thoảng máy vẫn sạc lên 100% để hiệu chỉnh ([Apple 108055](https://support.apple.com/en-us/108055), [MacRumors](https://www.macrumors.com/how-to/use-new-iphone-charging-limit-options-ios-18/), trích đoạn).
- **[Likely]** Sạc 24/7 với màn hình luôn sáng làm nóng máy và tăng nguy cơ phồng pin. Cần giới hạn sạc, độ sáng tối thiểu, và thông gió. Nguồn phổ thông, chất lượng trung bình ([ví dụ](https://www.voltacharger.com/blogs/news/why-is-your-iphone-battery-bulging-causes-risks-and-what-to-do)).

---

## 4. Vì sao Lightning dùng ESP32 (Bluetooth) còn USB-C dùng CH9329

**Giải thích đơn giản**

- **iPhone USB-C giống một laptop nhỏ.** Qua một cổng USB-C, nó cùng lúc xuất hình (DisplayPort) và làm "máy chủ USB" cho chuột và bàn phím. Hub USB-C tách một sợi cáp thành ba đường: HDMI ra capture card, cổng USB-A cho con chip giả chuột và bàn phím (CH9329), và cổng sạc. Vì thế **một dây làm được cả ba việc**, và dùng con chip USB có dây là tự nhiên nhất.
- **iPhone Lightning chỉ có một cổng, và muốn ra HDMI thì phải cắm Lightning Digital AV Adapter chiếm luôn cổng đó.** Adapter này thực chất là một máy tính nhỏ: iPhone nén hình rồi gửi cho nó giải nén ra HDMI. Cổng Lightning cái trên adapter **chỉ để sạc**, không dẫn dữ liệu USB ra ngoài. Người dùng báo HDMI chạy riêng được, chuột/bàn phím chạy riêng được, nhưng **không chạy cùng lúc**. Adapter Camera (USB) thì cho cắm chuột nhưng lại không có hình.
  Vậy khi đã dùng HDMI thì **không còn đường dây nào cho chuột**. Chỉ còn không dây, tức Bluetooth. ESP32 đóng vai chuột và bàn phím Bluetooth, nhận lệnh từ host qua cáp serial theo đúng giao thức của CH9329, nên phần mềm host dùng chung được cho cả hai dòng.

**Có nên dùng chung một cách cho cả hai dòng?**

| Phương án | Đánh giá |
|---|---|
| **Cả hai dòng dùng BLE (ESP32)** | **Không nên.** USB-C sẽ mất absolute (BLE absolute chưa được chứng minh), thêm độ trễ theo lô 11–30 ms, gặp lỗi tự kết nối lại của iOS, phải quản lý ghép đôi cho từng máy, và nghẽn 2,4 GHz khi nhiều máy. Lợi duy nhất là né Wired Accessories. |
| **Cả hai dòng dùng CH9329** | **Không làm được** cho Lightning, vì không có đường USB khi đang mirror HDMI, trừ khi T9 tìm ra adapter combo chạy được. |
| **Cùng một nền MCU (ESP32-S3): USB HID cho USB-C, BLE HID cho Lightning, cùng giao thức host** | **Nên nhắm tới cho sản phẩm.** Một mạch, một firmware với hai backend. Có descriptor absolute giống Aiden, profile chỉ bàn phím để né bug modifier, ack "đã giao" thật (`tud_hid_report_complete_cb` cho USB, ACK tầng link cho BLE), và pacer chạy trên MCU. Giao thức host vẫn là khung CH9329, cộng vài lệnh mở rộng (0x40+) cho absolute 16 bit, chuyển profile và trạng thái giao. |
| **Giữ CH9329 cho USB-C, ESP32 cho Lightning (như hiện tại)** | **Hợp lý cho GĐ 0 và MVP** nếu T1 cho thấy CH9329 absolute chạy được **và** T5 cho thấy bug modifier không đáng kể, hoặc né được bằng hai chip riêng. |

**Khuyến nghị:**
1. Dùng CH9329 để kiểm chứng nhanh ở GĐ 0 (T0–T5).
2. Song song, làm firmware ESP32-S3 với hai backend (USB và BLE) trên cùng giao thức, vì firmware BLE cho Lightning đằng nào cũng phải viết.
3. Nếu T1 thất bại với CH9329, hoặc T5 cho thấy bug modifier thường xuyên, chuyển dòng USB-C sang ESP32-S3 USB. Nếu gặp lỗi TinyUSB trên S3 thì dùng RP2040.
4. Không đưa USB-C sang Bluetooth.

---

## 5. Đề xuất thay đổi và lựa chọn tốt nhất

1. **Đổi thứ tự ưu tiên con trỏ: absolute trước, relative là dự phòng.**
   - Test đầu tiên phải là lệnh `0x04` (SEND_MS_ABS_DATA) của CH9329 trên iPhone USB-C (T1).
   - Nếu chạy, ánh xạ 0..4095 → pt là tuyến tính. Hiệu chỉnh bằng lưới 3×3 trên trang Safari, fit affine 6 tham số cho mỗi hướng màn hình.
   - Mỗi tap: move, chờ 80 ms, nhấn, giữ 60 ms, nhả 2–3 lần (theo Aiden).
   - Reset góc và pacer chỉ còn dùng cho BLE/Lightning.
2. **Nếu CH9329 absolute không chạy:** dùng MCU TinyUSB với descriptor giống Aiden/PiKVM: Mouse, Pointer, Collection Physical, X/Y 16 bit 0..32767, interface pointer đứng cuối. Bằng chứng mạnh nhất hiện có là cách này chạy trên iOS.
3. **Nếu buộc phải dùng relative** (Lightning/BLE):
   - Chạy CH9329 ở 115200 baud (cấu hình một lần trên PC; có hiệu lực sau khi cấp nguồn lại).
   - Đặt nhịp pacer là bội của `bInterval` (USB) hoặc của connection interval (BLE).
   - Dùng bước nhỏ, nằm trong vùng hệ số gần tuyến tính.
   - Neo ở **hai góc trên**.
   - Đo độ lặp lại (T4) trước khi hứa mục tiêu 4 pt.
   - Nếu độ lệch hệ số lớn hơn khoảng 0,5%, phải chấp nhận sai số lớn hơn hoặc chia quãng đi thành nhiều pha.
4. **Tầng lệnh "không mất lệnh" phải trung thực:**
   - Mọi báo cáo mang trạng thái đầy đủ: toạ độ tuyệt đối và trạng thái nút. Nhờ vậy retry an toàn: gửi lại vị trí đích và trạng thái nút cuối cùng.
   - Báo cáo nhả (chuột và phím) luôn gửi 2–3 lần.
   - Relative: sau mọi lỗi mơ hồ thì reset góc (như thiết kế).
   - Đọc `GET_INFO` (trạng thái USB) trước và sau mỗi thao tác để phát hiện iOS ngừng nhận.
   - Tài liệu và API phải nói rõ mức xác nhận: "chip đã nhận" với CH9329, "host đã lấy" với TinyUSB, "đã ACK ở tầng link" với BLE. **Không có mức "iOS đã xử lý".**
5. **Hub:**
   - Mua Apple USB-C Digital AV Multiport Adapter làm chuẩn, cộng 2 hub rẻ để so sánh.
   - Thứ tự cắm: nguồn PD vào hub, rồi hub vào iPhone, rồi HID và HDMI.
   - Dùng nguồn PD ổn định (tốt nhất có UPS). Mỗi lần mất nguồn là một lần rớt toàn bộ.
6. **Capture và host:**
   - MS2109 cho MJPEG passthrough. MS2130 chỉ dùng khi cắm cổng USB 2 (cho MJPEG), hoặc khi cố ý nén trên host.
   - Mỗi bus USB 2 độc lập một card, cho tới khi T8 chứng minh được nhiều hơn.
   - Host cho quy mô lớn: mini PC hoặc desktop x86 có khe PCIe, gắn card USB có **nhiều controller** (mỗi cổng một controller), iGPU Intel để nén H.264 theo yêu cầu.
   - Pi 5 chỉ hợp cho 1–2 máy. Không dùng Pi 4 cho quá 1 máy.
   - Gom các adapter USB-serial (CH340) qua hub USB 2.0 **Multi-TT có nguồn riêng**. Đặt chúng ở bus khác với capture card nếu được.
7. **Luồng hình:**
   - Mặc định MJPEG passthrough trong LAN.
   - Cắt vùng màn hình điện thoại và nén H.264 **chỉ khi có người xem từ xa**, bằng VAAPI/QSV trên x86, gửi qua WebRTC hoặc WebSocket + WebCodecs (như "Direct H.264" của PiKVM).
8. **Console điều khiển từ xa:**
   - Nếu có absolute, dùng "absolute KVM": toạ độ chuột trên video → báo cáo HID absolute. Không cần Pointer Lock, chạy cả trên trình duyệt điện thoại (PiKVM ghi rõ relative không chạy trên trình duyệt mobile, [mouse.md](https://github.com/pikvm/pikvm/blob/master/docs/mouse.md)).
   - Console tự vẽ con trỏ ở vị trí đã biết, nên không phụ thuộc con trỏ có hiện trên HDMI hay không.
   - Pointer Lock relative chỉ dùng cho máy BLE.
9. **Cài đặt iPhone** (bổ sung cho `docs/iphone-setup.md`):
   - Wired Accessories = **Always Allow** cho máy farm (ghi rõ đánh đổi an ninh), và kiểm tra lại sau mỗi lần cập nhật.
   - Tắt tự cập nhật và hoãn iOS 27 cho tới khi test xong.
   - Tắt Dwell, Hot Corners và Zoom.
   - Bật "Show Onscreen Keyboard" trong AssistiveTouch (Aiden khuyến nghị để nhập liệu ổn định).
   - Khoá cả hai thanh Tracking Speed.
   - Giới hạn sạc 80%, độ sáng tối thiểu.
10. **Phạm vi máy:**
    - USB-C: 15/16/17/Air, **trừ 16e/17e**.
    - Lightning: nếu làm, chỉ iPhone 11–14 và SE 2/3 (iOS 26/27), để ma trận test gọn lại.
    - Nếu vẫn cần 16e/17e hoặc máy Lightning cũ hơn, chỉ còn hướng AirPlay + OTG như board thương mại. Hướng này phải được duyệt.
11. **Trang hiệu chỉnh:**
    - Cài bằng "Add to Home Screen" (iOS 26 mặc định mở như web app).
    - Viewport: `viewport-fit=cover, width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no`.
    - CSS: `touch-action:none`.
    - Ghi mọi loại sự kiện kèm `pointerType` và timestamp; gửi về host qua WebSocket.

---

## 6. Ước lượng hiệu năng và quy mô

> Các con số dưới đây là **ước lượng có giả định**, chưa đo. Mỗi dòng ghi rõ giả định.

**Thời gian một lần tap (USB-C, CH9329 ở 115200 baud)**

| Chế độ | Tính toán | Kết quả |
|---|---|---|
| Absolute | move (khoảng 5 ms serial) + chờ 80 ms + nhấn (5 ms) + giữ 60 ms + nhả ×3 (khoảng 30 ms) | **khoảng 0,18–0,25 s/tap**, tối đa khoảng 4–5 tap/s. Thực tế bị giới hạn bởi tốc độ UI phản hồi. |
| Relative (reset góc + pacer) | t ≈ t_neo + 2·t_nghỉ + (dx/g_x + dy/g_y)·T + t_click. Giả định: T = 25 ms, g = 5–15 pt/báo cáo, dx trung bình 200 pt, dy trung bình 430 pt, t_neo khoảng 0,5 s, t_nghỉ khoảng 0,15 s | **khoảng 1,5–4 s/tap** (T = 8 ms thì khoảng 0,7–1,5 s) |
| Relative qua BLE | như trên, với T = 15 hoặc 30 ms theo connection interval | **khoảng 1,5–5 s/tap** |

**Độ chính xác**
- Absolute: bước lưới 852 pt / 4096 ≈ 0,21 pt (iPhone 15). Sai số chủ yếu do hiệu chỉnh affine, **ước lượng ≤ 1–2 pt** nếu T1 đạt.
- Relative: sai số ≈ (độ lệch hệ số) × quãng đường. Ví dụ để 95% nằm trong 4 pt trên quãng 400 pt, cần σ(hệ số) ≲ 0,5%. **Chưa có dữ liệu** (T4).

**Băng thông HID**
- CH9329 ở 9600 baud: khoảng 22–24 ms mỗi lệnh có ack, tức khoảng 40 lệnh/s. **Không đủ biên cho pacer 25 ms.**
- Ở 115200 baud: khoảng 4–5 ms mỗi lệnh, tức khoảng 200 lệnh/s (bị chặn tiếp bởi `bInterval`).

**Video (mỗi máy)**

| Giả định | Con số |
|---|---|
| MJPEG 1080p30 từ MS2109, 60–200 KB/khung (vùng viền đen nén rất tốt) | **khoảng 15–50 Mbit/s cho mỗi người xem**. LAN 1 GbE chứa khoảng 20–60 luồng người xem. |
| H.264 sau khi cắt vùng màn hình, khoảng 540×1170 @30 | khoảng 1,5–3 Mbit/s |
| Trễ glass-to-glass trong LAN, USB-C + MS2109 + MJPEG | **khoảng 150–250 ms** (TinyPilot đo khoảng 200 ms với MS2109 trên Pi 4) |
| Trễ với Lightning (thêm nén/giải nén H.264 trong adapter) | **[Unknown]**, ước lượng thêm 50–150 ms |
| MS2130 1080p60 YUYV (USB 3) | 1920·1080·2·60 ≈ 249 MB/s ≈ 2 Gbit/s mỗi card, nên khoảng 1–2 card trên một controller 5 Gbps, và phải nén trên host |

**Số máy trên mỗi host** (chưa đo, T8 quyết định)

| Host | Bus USB 2 độc lập | MS2109 ở 1080p30 (nếu mỗi card giữ 3×1024 B/µframe) | Nếu uvcvideo chọn alt setting nhỏ | Nén H.264 phần cứng |
|---|---|---|---|---|
| Pi 4 | 1 | **1** | khoảng 3–4 | có (V4L2 M2M, 1 luồng 1080p) |
| Pi 5 | 2 | **2** | khoảng 6–8 | **không** |
| Mini PC x86 (N100/N305), 1–2 controller | 1–2 | 1–2 | khoảng 4–8 | QSV, vài luồng |
| Desktop x86 + 2 card PCIe mỗi card 4 controller | 8 + onboard | **khoảng 8–10** | khoảng 20+ (CPU và USB-serial lúc đó mới là giới hạn) | QSV/NVENC |

**Công suất và nhiệt (mỗi máy, ước lượng)**
- iPhone màn hình bật, độ sáng thấp, đã chạm giới hạn 80%: khoảng 2–4 W. Khi đang sạc lên có thể tới 20 W trở lên.
- Hub khoảng 0,5–1 W; capture card khoảng 1–2,5 W (lấy từ host); CH9329/MCU khoảng 0,1–0,3 W.
- Tổng khoảng **4–8 W khi ổn định, khoảng 25 W lúc đỉnh**. 20 máy: khoảng 100–160 W ổn định, và **bộ nguồn phải chịu được khoảng 500 W** khi cả dãy cùng sạc sau một lần mất điện.

**BOM mỗi máy (ước lượng giá lẻ 2026, chưa có báo giá; không tính iPhone)**

| Hạng mục | Dòng USB-C | Dòng Lightning |
|---|---|---|
| HID: CH9329+CH340 hoặc ESP32-S3 + USB-UART | 5–10 USD | ESP32 5–10 USD |
| Hub USB-C (HDMI + USB-A + PD): Apple khoảng 69 USD, hãng khác 15–35 USD | 15–69 | — |
| Apple Lightning Digital AV Adapter | — | khoảng 49 USD |
| Capture card MS2109 (MS2130 khoảng 19–25 USD, [CNX](https://www.cnx-software.com/2022/11/07/ms2130-based-4k-hdmi-to-usb-3-0-video-capture-dongle-sells-for-19/)) | 8–15 | 8–15 |
| Sạc PD 20–30 W (hoặc chia từ bộ nguồn nhiều cổng) | 8–15 | 8–15 |
| Cáp, giá đỡ | khoảng 5 | khoảng 5 |
| Phần host chia cho mỗi máy | 15–50 | 15–50 |
| **Tổng** | **khoảng 55–165 USD** | **khoảng 90–145 USD** |

So sánh: SOME 3C bán board khoảng 38 USD/máy, nhưng phần lấy hình của họ không công bố và nhiều khả năng là AirPlay.

---

## 7. Test phần cứng, xếp theo rủi ro

Chạy theo đúng thứ tự. Mỗi test ghi model iPhone, bản iOS (**26.x và 27.0**), mẫu hub và `lsusb`. Mã trong ngoặc là bước tương ứng trong `docs/phase0-checklist.md`, nếu có.

| ID | Mục đích (rủi ro) | Cách làm | Đạt khi | Chi phí |
|---|---|---|---|---|
| **T0** | R5, R1: hiểu CH9329 trước khi đụng iPhone | Cắm đầu HID của CH9329 vào **PC Linux**. `lsusb -v -d 1a86:` để đọc interface, `bInterval` và report descriptor (dùng `usbhid-dump`). `evtest`: gửi 1000 báo cáo relative ở các nhịp 2/5/10/25 ms, so tổng REL_X nhận được với tổng đã gửi. Gửi `0x04` absolute, xem ABS_X/ABS_Y. Đo độ trễ mỗi lệnh ở 9600 và 115200 baud. | Biết `bInterval`, biết chip xếp hàng hay ghi đè, và nhịp tối thiểu để không mất báo cáo | 1 giờ, không cần iPhone |
| **T1** | R1: absolute trên iPhone | iPhone 15+ (USB-C) qua hub, mở trang hiệu chỉnh dạng web app. Gửi `0x04` theo lưới 3×3 kèm click; đọc `clientX/Y` và `pointerType`. Lặp lại 50 lần mỗi điểm. Thử ở work mode 0x00 và 0x02. | Con trỏ nhảy đúng điểm; sai số ≤ 1–2 pt sau fit affine | ½ ngày |
| **T2** | R3: hub | Với 3 hub (Apple Multiport + 2 hãng khác): có hình trên capture, `GET_INFO` báo USB connected, PD sạc, cả ba cùng lúc. Rút/cắm nguồn PD và ghi lại thứ gì rớt. Kiểm tra HDCP. (C1) | Ít nhất 1 hub đạt cả ba, và tự hồi phục sau khi mất nguồn | 1 ngày |
| **T3** | R5, A7: giao báo cáo tới iOS | Relative: gửi N bước nhỏ ở các nhịp khác nhau rồi click, so quãng đường đo được với N·g. Absolute: gửi cùng đích 1 lần và 3 lần. Đếm số lần nhả nút bị lỡ (UI kẹt ở trạng thái kéo). | Tìm được nhịp an toàn; lỗi nhả nút bằng 0 khi dùng nhả ×3 | ½ ngày |
| **T4** | R2, A4: relative (bắt buộc nếu T1 thất bại, và cho BLE) | Neo góc trên-trái và trên-phải, rồi đi (dx, dy) cố định 100 lần, click, đo. Lặp với step 2/4/8/16 và T = 8/16/25 ms. Đo độ lệch hệ số, kiểm tra con trỏ có bị kẹp ở góc, và số báo cáo cần để neo. (C5) | σ(hệ số) ≤ 0,5% ở ít nhất một cấu hình; vị trí neo lặp lại trong ±0,5 pt | 1 ngày |
| **T5** | R4: bug modifier | Cmd+Space, Cmd+A, Cmd+V: mỗi phím 50 lần với (a) một CH9329 mode 0x00; (b) **hai CH9329**, một chỉ bàn phím (0x01), một chỉ chuột (0x02); (c) chỉ bàn phím. (C7, X2) | Biết tỉ lệ lỗi. Nếu (b) ≈ (c) thì né được mà không cần MCU | ½ ngày |
| **T6** | R8: remote UX và trễ | Con trỏ có hiện trên capture không (C4). Đo glass-to-glass bằng đồng hồ ms trên iPhone so với ảnh trong trình duyệt, cho MJPEG passthrough. | Có số trễ; biết có cần vẽ con trỏ ảo hay không | 2 giờ |
| **T7** | R6: khoá và khởi động lại | Với từng lựa chọn Wired Accessories: khoá máy 5 phút và 70 phút rồi thử HID. Khởi động lại (BFU): HID có gõ được passcode không, với cả USB và BLE. Rút/cắm hub khi máy khoá. (C8) | Có quy trình vận hành cho mỗi trường hợp | ½ ngày |
| **T8** | R7: quy mô capture | Trên host đích: 1, 2, 3… card MS2109 (và MS2130 ở cổng USB 2 và USB 3). Ghi `v4l2-ctl --list-formats-ext`, alt setting được chọn (`/sys/kernel/debug/usb/devices` hoặc usbmon), và thời điểm xuất hiện "No space left on device". (B, C9, X6) | Biết số card trên mỗi bus và mỗi host | ½ ngày |
| **T9** | Dòng Lightning | (a) Digital AV Adapter với capture card: có hình và trễ bao nhiêu (L1). (b) ESP32 BLE với descriptor relative **và** absolute kiểu Aiden. (c) Connection interval iOS thực cấp (log trên ESP32). (d) Tắt/bật Bluetooth, khởi động lại iPhone, ra/vào vùng phủ: có tự kết nối lại không. (e) Một adapter combo Lightning → HDMI + USB: HDMI và HID có chạy cùng lúc không. (L2, L3) | Quyết định go/no-go cho dòng Lightning | 1–2 ngày |
| **T10** | Độ bền | Chạy liên tục 72 giờ, 3 máy: tap ngẫu nhiên mỗi 5 s, ghi log `GET_INFO` và no-signal, nhiệt độ, % pin, số lần hub reset. Có một lần rút điện có chủ đích. | Không có lỗi nào không tự hồi phục; nhiệt dưới ngưỡng throttle | 3 ngày (chạy nền) |

**Điều kiện dừng sớm:**
- Nếu T1 đạt và T2 có ít nhất một hub đạt, dòng USB-C chuyển sang **GO**. Làm tiếp T3, T5, T6, T7 để hoàn thiện.
- Nếu T1 thất bại, làm T4 **trước khi** đầu tư thêm vào mục tiêu 4 pt. Song song, dựng MCU absolute (mục 5.2) và lặp lại T1 với MCU.
