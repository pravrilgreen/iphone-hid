# Đánh giá khả thi độc lập: lớp điều khiển iPhone chỉ bằng phần cứng ngoài

- **Ngày:** 24/09/2026, cập nhật 25/09/2026 theo quyết định dùng hộp Orange Pi 5 Plus và chỉ hỗ trợ iPhone USB-C (mục 0.1). iOS 27 đã phát hành ngày 14/09/2026 ([MacRumors](https://www.macrumors.com/2026/09/09/apple-announces-ios-27-release-date/)). Gần như mọi bằng chứng bên dưới là từ iOS 26 trở về trước, nên **mọi test phần cứng phải chạy trên cả iOS 26.x và iOS 27.0**.
- **Phạm vi:** chỉ lớp điều khiển iPhone: lấy hình qua HDMI, đưa lệnh qua HID. Chỉ iPhone có USB-C (15 trở lên, trừ 16e và 17e). Không dùng thị giác máy (đã chốt). Không jailbreak, không Developer Mode, không cài app. Không dùng công cụ chuyên cho iPhone.
- **Cách làm:** đọc tài liệu Apple (Accessory Design Guidelines R31, bản 21/09/2026), mã nguồn các dự án mở (Aiden, PiKVM, TinyPilot…), device tree mainline của Orange Pi 5 Plus, issue GitHub về board này, diễn đàn và bài viết kỹ thuật. Chưa có test phần cứng nào.

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

Mã bài test (B1–B8, D1–D3, X1–X2) là các bài trong [phase0-checklist.md](phase0-checklist.md).

---

## 0. Cập nhật

### 0.1 Quyết định 25/09/2026: hộp Orange Pi 5 Plus, chỉ iPhone USB-C

- **Chỉ hỗ trợ iPhone có USB-C:** iPhone 15 trở lên, trừ 16e và 17e (không xuất hình). Không làm dòng Lightning,
  không dùng Bluetooth.
- **Sản phẩm chính: mỗi iPhone một Orange Pi 5 Plus** ([gadget.md](gadget.md)):
  - cổng Type-C USB 3.0/DP của board chạy **Linux USB gadget**: board chính là bàn phím và chuột của iPhone
    (bàn phím, phím media, phím hệ thống, chuột tương đối, chuột tuyệt đối; mỗi loại một interface HID);
  - **HDMI IN** của board đọc màn hình iPhone và nén JPEG (bộ mã hoá phần cứng qua GStreamer nếu image có, nếu không
    thì bộ đọc riêng của hộp, không cần cài gì), nên phần còn lại của hệ thống thấy đúng các khung JPEG như từ một
    capture card MJPEG;
  - board chạy server, web console và API.
- **Dự phòng:** cáp CH9329 cho bàn phím + chuột (image không có chế độ device, hoặc một host Linux khác), hình từ
  HDMI IN của board hoặc capture card MS2109.
- **Hai rủi ro mới**, chưa kiểm chứng trên đúng board này: cổng Type-C có chạy chế độ device không (R1, A10), và
  HDMI IN có đủ nhanh, đủ ổn định không (R3, A11). Chi tiết ở mục 3.5.

Các mục bên dưới đã được viết lại theo quyết định này. Kết quả nghiên cứu về iOS giữ nguyên.

### 0.2 Nghiên cứu bổ sung (24/09/2026)

Hai báo cáo mới (tiếng Anh, có nguồn và nhãn tin cậy): [chuột tuyệt đối trên iPhone](research/absolute-pointer.md)
và [phần cứng giá rẻ của Trung Quốc](research/china-market.md). Những điểm làm thay đổi đánh giá bên dưới:

| Điểm | Trước | Sau | Nguồn chính |
|---|---|---|---|
| iPhone theo **chuột tuyệt đối qua USB** (cần AssistiveTouch) | [Likely] | **[Confirmed]**: Aiden (mặc định iOS từ 04/2026), glassbox (iPhone 17 Pro Max, iOS 26.5, cùng kiểu HDMI + USB HID như dự án này), NanoKVM-Go (tài liệu cho iPhone 15/16/17), EasyClick (trích đoạn) | absolute-pointer §1, china-market §2.1 |
| **Digitizer / màn cảm ứng HID** như chạm thật | chưa xét | **[Contradicted]**: iOS 13.4 đã chặn; thêm collection Touch Screen cạnh chuột còn làm iOS bỏ qua nút chuột | absolute-pointer §3 |
| CH9329 ở chế độ tuyệt đối trên iPhone | [Unknown] | vẫn **[Unknown]**: chưa ai công bố descriptor của nó. Nay CH9329 chỉ còn là dự phòng; gadget của hộp dùng đúng bố cục đã chạy được (interface riêng, không report ID, 0..32767) | absolute-pointer §4, §7 |
| Kiến trúc có "lỗi thời" so với thị trường? | | **Không.** Mọi giải pháp iPhone không jailbreak, không app đều dùng chuột + phím HID qua AssistiveTouch (thêm Full Keyboard Access). Các box "群控" của Trung Quốc lấy hình qua **AirPlay** thay vì HDMI | china-market §0–1 |

Việc đã làm theo đó:
- **Phần mềm:** hiệu chỉnh đo thời gian iOS "trượt" con trỏ tới vị trí tuyệt đối trước khi click (mặc định 0,25 s
  cho tới khi đo). Lệnh nhả nút qua báo cáo tuyệt đối gửi 3 lần. Mọi phím/nút được nhả khi iPhone vừa nhận phụ
  kiện (sửa lỗi "kẹt kéo" mà NanoKVM-Go phải có nút "Repair iPhone drag"). Chưa bao giờ gửi báo cáo tuyệt đối
  (0,0) khi không chủ ý.
- **Gadget của hộp:** chuột tuyệt đối theo bố cục đã chạy được (Mouse > Pointer > Physical, 3 nút, X/Y 16 bit
  0..32767, wheel tương đối, interface riêng nên không có report ID); thứ tự interface bàn phím → phím media → phím
  hệ thống → chuột tương đối → chuột tuyệt đối (con trỏ đứng cuối, như Aiden); mỗi profile một serial USB (iOS lưu
  cache descriptor).
- **Checklist:** B4 kiểm chuột tuyệt đối ở profile RA (có chuột tương đối) và A (chỉ chuột tuyệt đối), vòng Caps
  Lock (đèn Caps Lock do iOS gửi về chứng minh iOS đã xử lý lệnh phím), tổ hợp Tab+phím của Full Keyboard Access;
  B5 đo thời gian trượt; D1 lấy descriptor của CH9329.

**Lựa chọn cần chủ dự án quyết định:**
- **Phím tắt iOS Shortcuts làm kênh phụ** (clipboard để gõ tiếng Việt, xác nhận app đã mở). Cần chủ dự án quyết
  định Shortcuts do người dùng tự tạo có tính là "cài app" không.
- **Sipeed NanoKVM-Go** (59–89 USD): một bo nhỏ cùng ý tưởng với hộp (một dây USB-C, khoảng 60 ms ở 1080p60, chế độ
  chuột tuyệt đối cho iPhone). Nên mua một cái để đối chiếu độ chính xác và độ trễ (X2).

---

## 1. Kết luận ngắn

### 1.1 Phán quyết

| Dòng máy / mục tiêu | Phán quyết | Lý do chính |
|---|---|---|
| **iPhone USB-C (15/16/17/Air, trừ 16e và 17e), hộp Orange Pi 5 Plus** | **CONDITIONAL GO, khả năng cao** | Phía iOS đã rõ: chuột tuyệt đối qua USB chạy trên iOS 26 ở nhiều dự án (Aiden cũng là một Linux USB gadget, cùng cách làm với hộp), với sai số ước lượng 1–2 pt và khoảng 0,2 s mỗi tap, không cần neo góc hay pacer. Điều kiện còn lại nằm ở phần cứng của hộp: cổng Type-C chạy chế độ device (R1) và HDMI IN đủ nhanh (R3). Nếu một trong hai không đạt, vẫn có phương án dự phòng: cáp CH9329, capture card MS2109. |
| **iPhone 16e / 17e** | **Không hỗ trợ** | Hai máy này có cổng USB-C nhưng **không có DisplayPort Alt Mode** (16e: nhiều nguồn xác nhận; 17e: [Likely]). Cách duy nhất để lấy hình là AirPlay; dự án không làm. |
| **Mục tiêu 1: con trỏ chính xác, không mất lệnh, không thị giác** | **GO nếu có absolute; CONDITIONAL nếu chỉ có relative** | Với relative, muốn đạt 95% trong 4 pt (≈ 5 px ở khung 1080p) thì độ lặp lại của hệ số gia tốc phải tốt hơn khoảng 0,5% trên quãng 400 pt. Chưa ai đo được con số này trên iOS. "Không mất lệnh" **không thể chứng minh ở phía iOS** khi không có thị giác máy. Ta chỉ biết lệnh đã tới host USB (gadget: iPhone đã lấy báo cáo) hoặc đã tới chip (CH9329). Vòng Caps Lock là cách kiểm rẻ rằng iOS còn xử lý phím. |
| **Mục tiêu 2: điều khiển từ xa có video trên trình duyệt** | **GO trong LAN** (MJPEG passthrough); trễ của HDMI IN trên hộp **chưa đo**; **cần H.264 cho WAN** | MJPEG tốn băng thông khoảng 10–50 Mbit/s cho mỗi người xem. Với capture card MS2109, trễ khoảng 150–250 ms (TinyPilot). Với HDMI IN của hộp, có một báo cáo khoảng 2 s nhưng với pipeline khác hẳn (A11); bài B7 đo. |

### 1.2 Rủi ro hàng đầu và test giải quyết

| # | Rủi ro | Hệ quả nếu xấu | Test |
|---|---|---|---|
| R1 | Cổng Type-C USB 3.0/DP của Orange Pi 5 Plus không chạy chế độ device với image dùng được, hoặc chạy nhưng HID bị nhận lỗi (A10) | Board không làm bàn phím + chuột được; dùng cáp CH9329, hộp vẫn giữ HDMI IN | **B1, B2**, B4 |
| R2 | iOS không theo **chuột tuyệt đối** của gadget (hoặc của CH9329, ở phương án dự phòng) | Quay về relative + neo góc, chậm và kém chính xác hơn nhiều | **B4**, B5 (D3) |
| R3 | HDMI IN: driver (`rk_hdmirx` của hãng, `snps_hdmirx` mainline) trục trặc, tối đa 4K30, trễ cao (A11) | Hình chậm hoặc chập chờn; phải đổi pipeline, hoặc dùng capture card MS2109 | **B3, B7** |
| R4 | Relative + gia tốc không lặp lại đủ (độ lệch hệ số > 0,5%) | Không đạt mục tiêu 4 pt nếu không dùng thị giác (chỉ quan trọng nếu R2 xấu) | B5 |
| R5 | Hub không cho HDMI + HID + PD chạy cùng lúc, hoặc reset khi nguồn chập chờn | Máy đứng | B3, B4, B6 |
| R6 | Bug phím tắt Cmd/Shift/Option khi có cả bàn phím lẫn chuột | Phím tắt (Cmd+Space, Cmd+V…) không tin cậy. Gadget đổi được sang profile chỉ bàn phím bằng phần mềm; CH9329 thì không | B4 (D3) |
| R7 | Báo cáo "đã giao" nhưng iOS bỏ hoặc gộp (A6, A7) | Mất lệnh mà không biết | B4 (`capscheck`), B5, D1 |
| R8 | Khoá máy, khởi động lại, cập nhật iOS, popup Wired Accessories | Máy "chết" khi không có người trông | B6, B8 |
| R9 | Con trỏ AssistiveTouch không hiện trên HDMI | Người vận hành "mò" nếu điều khiển kiểu KVM relative | B7 |
| R10 | iOS 27 thay đổi hành vi (HID, AssistiveTouch, phụ kiện) | Mọi kết luận bên trên phải test lại | B4–B6 trên iOS 27 |

---

## 2. Bảng giả định

| # | Giả định | Trạng thái | Nguồn | Hệ quả |
|---|---|---|---|---|
| A1 | iOS chỉ nhận chuột **tương đối**. Descriptor absolute thì con trỏ đứng yên | **[Contradicted]** với USB (nghiên cứu bổ sung: chuột tuyệt đối qua USB đã **[Confirmed]** trên iOS 26) | Aiden dùng absolute cho iOS: [aiden-usb-gadget](https://github.com/AidenAI-IO/aiden-firmware/blob/main/overlay-debian/usr/lib/aiden/aiden-usb-gadget), [tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go); [research/absolute-pointer.md](research/absolute-pointer.md) §1 | Gadget có interface chuột tuyệt đối riêng. Test absolute **trước tiên** (B4). Nếu được, không cần neo góc và pacer. |
| A2 | Trên iPhone, con trỏ chỉ có khi bật AssistiveTouch | **[Confirmed]** | [Apple 111775](https://support.apple.com/en-us/111775) (trích đoạn); [README Aiden](https://github.com/AidenAI-IO/aiden-firmware) | Giữ bước cài đặt. |
| A3 | Không tắt được gia tốc, chỉ chỉnh được Tracking Speed | **[Likely]** | [Apple Community, iPadOS 26](https://discussions.apple.com/thread/256143470) (trích đoạn); [Apple Community 253828185](https://discussions.apple.com/thread/253828185) (trích đoạn) | Relative phải có mô hình gia tốc hoặc pacer. |
| A4 | Con trỏ bị kẹp ở mép màn hình, nên reset góc cho ra vị trí đã biết | **[Unknown]** | Không có nguồn cho iPhone | B5 (chỉ cần nếu phải dùng relative). |
| A5 | Dồn con trỏ vào góc không gây tác dụng phụ | **[Likely]** với mặc định; **[Unknown]** chi tiết | Hot Corners chỉ chạy khi bật cả AssistiveTouch lẫn Dwell ([macmost](https://macmost.com/hot-corners-on-the-iphone-and-ipad.html), trích đoạn). Về Home hoặc mở Control Center cần nhấn giữ rồi kéo ([iDownloadBlog](https://www.idownloadblog.com/2023/10/10/how-to-use-mouse-with-iphone/), trích đoạn) | Giữ Dwell, Zoom và Hot Corners tắt. Chỉ reset góc khi không giữ nút. |
| A6 | Mỗi báo cáo có xác nhận; xác nhận nghĩa là lệnh đã tới iOS | **[Contradicted]**: ack của CH9329 chỉ là chip đã nhận khung; xác nhận của gadget là iPhone đã lấy báo cáo ở tầng USB, vẫn chưa phải iOS đã xử lý | Tài liệu CH9329 (xem `docs/ch9329-protocol.md`). Aiden: "a successful `/dev/hidg0` write only proves that the gadget accepted the HID report" ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)) | Phải có lệnh idempotent (mục 5). Gadget của hộp chờ tới khi iPhone lấy báo cáo, không dừng ở chỗ ghi được vào `/dev/hidgN`. |
| A7 | iOS không bỏ hay gộp báo cáo | **[Contradicted]** (thực địa) | Aiden gửi nhả nút 3 lần vì "a single final release report can be missed or coalesced", và giữ tap 60 ms vì "iOS drops faster events" ([tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go)) | Lặp báo cáo nhả, đặt thời gian giữ tối thiểu. |
| A8 | Hub USB-C cho HDMI + USB-A HID + PD cùng lúc | **[Likely]** | Aiden dùng hub "Type-C hub (with HDMI and USB)" ([hardware.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/01-getting-started/hardware.md)). Apple USB-C Digital AV Multiport Adapter hỗ trợ iPhone ([mô tả sản phẩm](https://www.amazon.com/Apple-USB-C-Digital-Multiport-Adapter/dp/B0D9MMFQNL), trích đoạn) | B3, B4, B6. |
| A9 | Mọi iPhone USB-C đều xuất được hình | **[Contradicted]** | 16e không có DP Alt Mode ([macReports](https://macreports.com/does-iphone-16e-support-hdmi-output-what-you-need-to-know/), trích đoạn). 17e không nằm trong danh sách xuất hình qua USB-C (trích đoạn, [spec 17e](https://support.apple.com/en-us/126470)) | Loại 16e/17e. |
| A10 | Cổng Type-C USB 3.0/DP của Orange Pi 5 Plus chạy được chế độ USB device (gadget) | **[Likely]** | Device tree mainline: FUSB302 với `data-role = "dual"`, controller `usb_host0_xhci` có `usb-role-switch` ([rk3588-orangepi-5-plus.dts](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/rockchip/rk3588-orangepi-5-plus.dts), đọc trực tiếp). Orange Pi dùng cáp Type-C này để nạp image và cho ADB ([wiki](http://www.orangepi.org/orangepiwiki/index.php/Orange_Pi_5_Plus), trích đoạn). Một người dùng image Ubuntu Rockchip dựng gadget chuột HID trên board này: Windows nhận thiết bị nhưng báo "USB Input" "with a problem"; issue đóng vì cũ, không có lời giải ([ubuntu-rockchip #437](https://github.com/Joshua-Riek/ubuntu-rockchip/issues/437)) | B1, B2. Không được thì dùng cáp CH9329. |
| A11 | HDMI IN của board đọc được hình iPhone đủ nhanh cho điều khiển từ xa | **[Unknown]** | Driver `rk_hdmirx` (kernel của hãng) thiếu `VIDIOC_G_PARM`, cần vá để hợp chuẩn V4L2; người dùng chỉ lấy được tối đa 4K ở 30 fps ([ubuntu-rockchip #830](https://github.com/Joshua-Riek/ubuntu-rockchip/issues/830)). Một người dùng đo được khoảng 2 s ở 30 fps với `ffmpeg ... -c:v hevc_rkmpp` ghi ra file ([rockchip-linux/mpp #587](https://github.com/rockchip-linux/mpp/issues/587)) | B3, B7. Server khai EDID 1080p60 (không cần 4K) và nén JPEG với hàng đợi một khung. |
| A12 | MS2130 (USB 3) tốt hơn MS2109 cho MJPEG passthrough | **[Contradicted]** (tuỳ firmware) | Trên USB 3, MS2130 chỉ có YUV; trên USB 2 mới có MJPEG ([HyperHDR #499](https://github.com/awawa-dev/HyperHDR/discussions/499), [TinyPilot #1593](https://github.com/tiny-pilot/tinypilot/discussions/1593)) | Ở phương án dự phòng, dùng MS2109 hoặc cắm MS2130 vào cổng USB 2. |
| A13 | Nhiều capture card trên một host là được | **[Unknown]**, dễ bị giới hạn | "No space left on device" khi có 2 card ([ustreamer #53](https://github.com/pikvm/ustreamer/issues/53); [The Good Penguin](https://www.thegoodpenguin.co.uk/blog/multiple-uvc-cameras-on-linux/), trích đoạn) | Chỉ liên quan host nhiều máy ở phương án dự phòng. Tính theo số bus USB 2 độc lập. |
| A14 | Trang Safari trả `clientX/Y` đúng khi click bằng con trỏ AssistiveTouch | **[Unknown]** (khá có khả năng) | Con trỏ iPhone là tính năng trợ năng, không phải hệ pointer của iPadOS ([HIG](https://developer.apple.com/design/human-interface-guidelines/pointing-devices) chỉ nói Mac/iPad/Vision Pro) | B5 dùng chính trang hiệu chỉnh. |
| A15 | Safari phát `pointermove` khi rê chuột trên iPhone | **[Unknown]** | Trên iPadOS: `any-hover` đổi khi cắm chuột ([WebKit 209292](https://bugs.webkit.org/show_bug.cgi?id=209292), trích đoạn); không có nguồn cho iPhone | Không thiết kế dựa vào hover. |
| A16 | 1 CSS px = 1 pt khi đặt `width=device-width` | **[Likely]** | [W3C Mobile A11y TF](https://lists.w3.org/Archives/Public/public-mobile-a11y-tf/2016Jan/0003.html) (trích đoạn) | Giữ Display Zoom = Default, `initial-scale=1`. |
| A17 | Máy phải luôn mở khoá thì phụ kiện có dây mới chạy | **[Confirmed]** (mặc định); có ngoại lệ "Always Allow" | [Apple Platform Security](https://support.apple.com/guide/security/activating-data-connections-securely-ios-sec5044aad1b/web), [Apple 111806](https://support.apple.com/en-us/111806), [Macworld](https://www.macworld.com/article/2945482/ios-26-juice-jacking-wired-accessories-always-ask.html) (trích đoạn) | Đặt Auto-Lock = Never. Cân nhắc "Always Allow". |
| A18 | Có thể mô phỏng cách né bug của Aiden bằng cách đổi work mode CH9329 | **[Contradicted]** | Cấu hình CH9329 chỉ có hiệu lực ở lần cấp nguồn sau (`docs/ch9329-protocol.md`). Aiden cần re-enumerate bằng phần mềm với PID khác ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)) | Gadget của hộp đổi profile bằng phần mềm. Với CH9329 chỉ còn cách hai chip riêng (D3). |

---

## 3. Kết quả nghiên cứu theo chủ đề

### 3.1 Con trỏ iOS với chuột USB trên iPhone

**Điều kiện và phiên bản**
- **[Confirmed]** iPhone dùng được chuột với con trỏ tròn khi bật AssistiveTouch. Tính năng có từ iOS 13. Các nút phụ gán hành động được qua *AssistiveTouch > Devices > [thiết bị] > nút*.
  Nguồn: [Apple 111775](https://support.apple.com/en-us/111775) (trích đoạn). Aiden: "iOS control also requires AssistiveTouch to be enabled" ([README](https://github.com/AidenAI-IO/aiden-firmware)).
- **[Confirmed]** Tài liệu HIG về pointing devices chỉ nói Mac, iPad và Vision Pro. Các hiệu ứng "magnetic", highlight, lift là của iPadOS.
  Nguồn: [HIG Pointing devices](https://developer.apple.com/design/human-interface-guidelines/pointing-devices).
  **[Likely]** Suy ra: con trỏ iPhone không hút về phần tử, nên không làm lệch điểm click. Đây là suy luận từ việc tài liệu không nhắc tới iPhone.
- **[Confirmed]** iOS 26 và iOS 27 hỗ trợ iPhone 11 trở lên. iOS 18 là bản cuối cho XS/XR.
  Nguồn: [MacRumors iOS 26](https://www.macrumors.com/2025/06/09/ios-26-supports-iphone-11-and-newer/), [MacRumors iOS 27](https://www.macrumors.com/2026/09/14/ios-27-compatible-iphones/) (trích đoạn).
  Hệ quả: mọi iPhone mà dự án hỗ trợ (15 trở lên) đều chạy iOS 26 và 27, nên ma trận test chỉ có hai bản iOS.

**Relative hay absolute: phát hiện quan trọng nhất**
- **[Confirmed]** (mã nguồn) Aiden, bản đang phát hành, dùng **descriptor chuột tuyệt đối** cho iOS. Cấu trúc: Usage Page Generic Desktop, Usage Mouse, Collection Application, Usage Pointer, Collection Physical, 8 nút, X/Y 16 bit tuyệt đối 0..32767, wheel tương đối. Interface HID không phải boot (protocol 0, subclass 0). Mặc định `device_type = "iOS"` suy ra `pointer_mode = "absolute"`.
  Nguồn: [aiden-usb-gadget](https://github.com/AidenAI-IO/aiden-firmware/blob/main/overlay-debian/usr/lib/aiden/aiden-usb-gadget), [config.go/SKILL.md qua tìm kiếm mã](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/04-agent/configuration.md).
- **[Confirmed]** (lịch sử git) Lịch sử đổi hướng của Aiden:
  - Ngày 29/04/2026, commit `651f68d` "switch to relative mouse for iOS compatibility" với lý do "iOS only supports relative".
  - Ngay hôm sau, commit `664be64` quay lại absolute với ghi chú "Testing iOS absolute positioning support".
  - Từ đó đến nay họ giữ absolute cho iOS.
  Nguồn: [lịch sử repo](https://github.com/AidenAI-IO/aiden-firmware/commits/main).
- **[Likely]** Vậy absolute qua USB chạy được trên iPhone đời hiện tại (nghiên cứu bổ sung nâng điểm này lên [Confirmed], mục 0.2). Hai chi tiết thực địa trong mã Aiden ủng hộ điều này:
  - "iOS HID cursor mode smoothly animates the cursor toward the target". Họ đợi 80 ms trước khi nhấn, nếu không cú nhấn thành thao tác kéo.
  - Toạ độ 0..32767 phủ đúng vùng màn hình điện thoại (phần ảnh đã bỏ viền đen).
  Nguồn: [tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go).
- **[Likely]** Trên iPad, PiKVM mặc định dùng absolute. Một issue năm 2024 báo di chuyển chuột chạy được nhưng click trái không nhận (đã được đánh dấu fixed).
  Nguồn: [PiKVM mouse.md](https://github.com/pikvm/pikvm/blob/master/docs/mouse.md), [PiKVM #1202](https://github.com/pikvm/pikvm/issues/1202).
- **[Likely]** Digitizer (touchscreen) không được iOS chuyển thành con trỏ.
  Nguồn: commit Aiden `d65e498` "macOS/iOS do not automatically convert Digitizer input to cursor movement".
- Gadget của hộp dùng một interface chuột tuyệt đối riêng, như Aiden. **[Unknown]** CH9329 (dự phòng) thì gộp chuột relative (report ID 1) và absolute (report ID 2, lưới 4096) trong cùng chức năng chuột; iOS có nhận lệnh `0x04` của nó không phải đo (D3).

**Gia tốc, Tracking Speed và các tuỳ chọn**
- **[Likely]** Không có công tắc tắt gia tốc trên iPadOS/iOS, kể cả iPadOS 26. Chỉ có Tracking Speed và Trackpad Inertia (dành cho trackpad).
  Nguồn: [Apple Community 256143470](https://discussions.apple.com/thread/256143470) (trích đoạn), [AbilityNet iOS 26](https://mcmw.abilitynet.org.uk/how-to-use-a-mouse-or-trackpad-with-your-iphone-or-ipad-in-ios-26) (trích đoạn).
- **[Likely]** Tracking Speed nằm ở *Settings > General > Trackpad & Mouse*, và có cả một thanh trượt trong *Accessibility > Touch > AssistiveTouch*. Chưa rõ hai thanh này có liên kết với nhau không.
  Nguồn: [Apple: Use AssistiveTouch](https://support.apple.com/guide/iphone/use-assistivetouch-iph96b21954/ios) (trích đoạn). Hệ quả: khoá cả hai và ghi lại giá trị.
- **[Confirmed]** Pointer Control trên iPhone có: Increase Contrast, Automatically Hide Pointer, Color, Pointer Size, Scrolling Speed. Pointer Animations có trong tài liệu nhưng nhiều khả năng chỉ tác động trên iPad.
  Nguồn: [Apple iPhone guide](https://support.apple.com/guide/iphone/adjust-pointer-settings-iphec6e1e60b/ios) (trích đoạn).
- **[Likely]** Khi xoay ngang với chuột relative (báo cáo gốc dùng chuột Bluetooth), trục X/Y bị đảo và con trỏ không qua được giữa màn hình. Lỗi được báo từ iOS 16.5 và vẫn còn ở iOS 18.5 (FB17806167). Chưa rõ tình trạng trên iOS 26/27 và với absolute.
  Nguồn: [forum 786963](https://developer.apple.com/forums/thread/786963).
  Hệ quả: tạm thời chỉ hỗ trợ màn hình dọc.

**Mép và góc màn hình**
- **[Unknown]** Không có nguồn nào nói con trỏ iPhone bị kẹp ở mép. Hành vi này là bắt buộc để reset góc chạy được. Cũng chưa rõ màn hình bo góc có làm "góc" thực tế lệch vào trong không (B5).
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

**USB (gadget của hộp, hoặc CH9329)**
- **[Confirmed]** Với thiết bị USB full-speed, endpoint interrupt khai `bInterval` từ 1 đến 255 ms. Host poll không thưa hơn giá trị đó. Mỗi lần poll lấy tối đa một báo cáo cho mỗi endpoint.
  Nguồn: đặc tả USB 2.0 §9.6.6 ([usb.org](https://www.usb.org/document-library/usb-20-specification)). Chuột thường khai 8 ms, chuột game 1 ms ([ghi chú](https://github.com/ventixy/notebook/blob/master/src/python/Project/hid.md)).
- **[Likely]** Gadget (chức năng HID `f_hid` của Linux): mỗi interface giữ một báo cáo đang gửi, và node `/dev/hidgN` chỉ nhận báo cáo mới khi host đã lấy báo cáo trước. `GadgetBackend` dựa vào điều này để biết iPhone đã poll báo cáo, mạnh hơn mức "ghi được vào `/dev/hidg0`" mà Aiden cảnh báo (A6). Cách `f_hid` hoạt động như vậy được mô tả trong `ihc/hid/gadget.py` và kiểm bằng node giả trong `tests/test_gadget.py`; chưa xác nhận trên board (B2, B4).
- **[Unknown]** `bInterval` của CH9329, và chip xử lý thế nào khi nhận khung serial nhanh hơn nhịp poll: xếp hàng, ghi đè hay trả lỗi.
  - Với relative, ghi đè nghĩa là **mất quãng đường**.
  - Không tìm thấy tài liệu công khai. Tool của cùng hãng cho CH9350L phải chèn mặc định 8 ms giữa hai báo cáo để chip "does not drop characters" ([wch-usb-hid-serial-keycode-tools](https://github.com/sunasaji/wch-usb-hid-serial-keycode-tools)). Đây là dấu hiệu gián tiếp, không phải cùng chip.
  - **D1 đo được trên một PC Linux, chưa cần iPhone.** `bInterval` của gadget đọc ở B2.
- **[Likely]** iOS có lúc ngừng poll HID. Khi đó hàng đợi phía thiết bị đầy và lệnh ghi bị chặn.
  Nguồn: Aiden đặt timeout 750 ms cho mỗi lần ghi vì "Linux hidg writes can otherwise block forever when the USB host stops polling" ([tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go)).
  Gadget của hộp: báo cáo không được lấy trong 500 ms thì báo timeout, và server báo máy mất kết nối. Với CH9329, chưa rõ lúc đó chip trả `00`, `E6` hay im lặng (D1, D3).
- **[Likely]** iOS có thể bỏ lỡ hoặc gộp báo cáo. Aiden phải làm mấy việc sau:
  - Lặp báo cáo nhả nút 3 lần, cách nhau 15 ms.
  - Giữ tap 60 ms.
  - Giữ bộ phím kèm modifier 120 ms.
  - Chờ con trỏ ổn định 80 ms trước khi nhấn.
  Nguồn: [tools_hid.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/tools_hid.go).
- **[Confirmed]** (CH9329) Ở 9600 baud, một khung chuột relative kèm ack mất 18 byte trên dây, tức 18,75 ms. Cộng 3 ms ngưỡng "packet interval" của chip, tổng khoảng 22–24 ms. **Pacer 25 ms gần như không còn biên**: mỗi lần trễ hay retry đều làm lệch nhịp, và nhịp lệch thì gia tốc lệch. Ở 115200 baud chỉ còn khoảng 1,6 ms + 3 ms. Gadget không có chặng serial này.
  Nguồn: tính từ khung trong `docs/ch9329-protocol.md`.

### 3.3 Safari trên iPhone làm đích hiệu chỉnh

- **[Unknown]** Click từ con trỏ AssistiveTouch có sinh `pointerdown`/`click` với `clientX/Y` đúng hay không.
  - **[Likely]** Nhiều khả năng là có, và dưới dạng *touch* (`pointerType = "touch"`), vì AssistiveTouch giả lập ngón tay. Aiden thấy thao tác kéo bằng chuột giữ "fling velocity" giống vuốt tay ([hid_provider.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/mnk/hid_provider.go)).
  - Trang hiệu chỉnh phải ghi **mọi** sự kiện (`pointer*`, `touch*`, `mouse*`, `click`) kèm `pointerType` để B5 trả lời.
- **[Unknown]** Có `pointermove`/`mousemove` khi chỉ rê chuột (hover) trên iPhone không.
  - Trên iPadOS, WebKit có hỗ trợ hover với trackpad: `any-hover` đổi khi cắm chuột ([WebKit 209292](https://bugs.webkit.org/show_bug.cgi?id=209292), trích đoạn).
  - Có một bug iOS phát `pointerenter` kiểu mouse kèm touch ([WebKit 214609](https://bugs.webkit.org/show_bug.cgi?id=214609), trích đoạn).
  - iOS 26 có "Hover Text" cho con trỏ ở cấp hệ thống ([AbilityNet](https://mcmw.abilitynet.org.uk/how-to-display-a-larger-version-of-text-when-you-hover-it-with-the-pointer-in-ios-26-on-your-iphone-or-ipad), trích đoạn), nhưng điều đó không bảo đảm sự kiện web. **Không thiết kế dựa vào hover.**
- **[Likely]** Với `<meta name="viewport" content="width=device-width, initial-scale=1">`, 1 CSS px = 1 pt ([W3C](https://lists.w3.org/Archives/Public/public-mobile-a11y-tf/2016Jan/0003.html), trích đoạn). Điều kiện: Display Zoom = Default và trang không bị phóng to.
  - **Cẩn thận:** click AssistiveTouch là "tap", nên hai click nhanh có thể thành double-tap-zoom và làm lệch toàn bộ toạ độ.
  - Phải đặt `maximum-scale=1, user-scalable=no` và `touch-action: none` (khuyến nghị kỹ thuật, chưa kiểm chứng).
- **Độ lệch của trang so với màn hình:**
  - **[Likely]** Từ iOS 26, mọi trang được "Add to Home Screen" mặc định mở như web app (không có giao diện Safari), với công tắc "Open as Web App" bật sẵn ([heise](https://www.heise.de/en/news/iOS-26-and-iPadOS-26-Changed-web-app-behaviour-on-the-home-screen-10749652.html), [iDownloadBlog](https://www.idownloadblog.com/2025/06/17/apple-ios-26-safari-web-apps-home-screen-bookmarks/), trích đoạn).
  - **[Likely]** Kết hợp với `viewport-fit=cover` và `apple-mobile-web-app-status-bar-style=black-translucent`, gốc (0,0) của trang trùng gốc màn hình. Cần đo xác nhận trong B5.
  - **Khuyến nghị:** dùng web app chạy standalone. Không cố suy ra offset trong tab Safari, vì thanh địa chỉ và tab bar của iOS 15–26 co giãn.
  - Không đo ở vùng status bar: tap ở đó có thể bị hệ thống dùng để cuộn lên đầu.
- **[Likely]** Cmd+Space mở Spotlight và Cmd+L đưa con trỏ vào ô địa chỉ Safari trên iPhone khi có bàn phím ngoài ([Gadget Hacks](https://ios.gadgethacks.com/how-to/tired-tapping-use-external-keyboard-your-iphone-and-unlock-tons-keyboard-shortcuts-0385569/), [Apple: external keyboard](https://support.apple.com/guide/iphone/control-iphone-with-an-external-keyboard-ipha4375873f/ios), trích đoạn). **Cả hai đều dính bug modifier** (mục 3.4).
- **[Unknown]** Gõ một URL vào Spotlight rồi Return có mở thẳng Safari không. Tài liệu chỉ nói Spotlight gợi ý "tìm trong Safari" ([AppleInsider](https://appleinsider.com/inside/ios-17/tips/how-to-use-the-new-spotlight-in-ios-17), trích đoạn). Đường chắc hơn là Cmd+Space, gõ "Safari", Return, rồi Cmd+L và gõ URL. Hoặc mở web app một lần bằng tay khi cài máy.

### 3.4 Chọn phần cứng HID: gadget của board, CH9329 dự phòng

- **[Confirmed]** Aiden, dự án đã chạy chuột tuyệt đối trên iPhone, chính là một Linux USB gadget: script `aiden-usb-gadget` dựng các interface HID, và phần mềm ghi báo cáo vào `/dev/hidg0` ([aiden-usb-gadget](https://github.com/AidenAI-IO/aiden-firmware/blob/main/overlay-debian/usr/lib/aiden/aiden-usb-gadget), [usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)). Hộp làm cùng cách.
- **[Confirmed]** Cách né bug modifier của Aiden:
  - Chuyển sang profile không có pointer, với **PID và serial khác** để iOS không dùng lại descriptor cũ.
  - Gửi phím tắt xong thì khôi phục profile đầy đủ.
  - Thứ tự interface bắt buộc: keyboard → Consumer Control → pointer → ECM. Đặt pointer ngay sau keyboard thì bàn phím ảo chỉ hiện lại khoảng 80% số lần; thứ tự mới đạt 10/10.
  - Không được unbind/rebind cùng danh tính khi cáp vẫn cắm, vì iOS giữ trạng thái lệch.
  Nguồn: [usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md), commit `c001083`.
- Gadget của hộp đã theo thứ tự đó (bàn phím → phím media → phím hệ thống → chuột tương đối → chuột tuyệt đối) và có profile chỉ bàn phím (`K`). Khác Aiden ở một điểm: các profile giữ nguyên PID, chỉ đổi serial (`ihc-RA`, `ihc-A`…). **[Unknown]** iOS có coi đó là thiết bị mới không (B4 bước 6); nếu không, `ihc gadget up --pid` đổi được PID. Việc tự chuyển profile quanh mỗi phím tắt chưa làm.
- **[Likely]** iOS chốt layout bàn phím phần cứng tại thời điểm enumerate, theo bàn phím ảo đang chọn lúc đó ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)). Mỗi lần re-enumerate là một lần chốt lại.
- **[Contradicted]** CH9329 không làm được việc re-enumerate này lúc đang chạy, vì cấu hình chỉ có hiệu lực ở lần cấp nguồn sau.
  **[Unknown]** Còn một phương án chưa ai thử: **hai CH9329 riêng**, một chiếc chỉ bàn phím (mode 0x01), một chiếc chỉ chuột (mode 0x02). Aiden mô tả bug là khi bàn phím và pointer "advertised by the same USB composite". Nếu bug theo từng composite thì hai chip riêng sẽ né được; nếu theo toàn hệ thống thì không (D3).
- **Đánh giá:**
  - Gadget của hộp hơn CH9329 ở bốn điểm: (1) descriptor absolute sạch, giống Aiden; (2) đổi profile bằng phần mềm để né bug modifier; (3) xác nhận "đã giao" thật (iPhone đã poll báo cáo); (4) không có chặng USB-serial, bớt một nguồn jitter cho chế độ tương đối.
  - Nhược điểm: chế độ device trên cổng Type-C của board này chưa kiểm chứng (A10).
  - CH9329 vẫn là dự phòng tốt vì rẻ, có sẵn và cắm vào host Linux nào cũng được.

### 3.5 Orange Pi 5 Plus: cổng Type-C làm USB device, HDMI IN

**Cổng Type-C USB 3.0/DP làm USB device**
- **[Confirmed]** (mã nguồn, đọc trực tiếp) Device tree mainline `rk3588-orangepi-5-plus.dts`:
  - chip điều khiển USB-C FUSB302 (`fcs,fusb302`) với connector `data-role = "dual"`, `power-role = "dual"`, `try-power-role = "source"`;
  - controller `usb_host0_xhci` có `usb-role-switch`, nối với connector đó.
  Nguồn: [rk3588-orangepi-5-plus.dts](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/rockchip/rk3588-orangepi-5-plus.dts).
  Hệ quả: cổng đổi được vai trò host/device. **[Likely]** Cáp USB-A → USB-C cho board biết bên kia là host, nên cổng chuyển sang device; với cáp C–C, board ưu tiên làm nguồn nên có thể thành host. Vì vậy hộp dùng cáp USB-A → USB-C.
- **[Likely]** Orange Pi dùng cáp Type-C dữ liệu nối cổng này để nạp image (RKDevTool) và cho ADB ([wiki Orange Pi 5 Plus](http://www.orangepi.org/orangepiwiki/index.php/Orange_Pi_5_Plus), trích đoạn). ADB chạy được nghĩa là image của hãng đã đưa cổng này về chế độ device.
- **[Likely]** Một người dùng image Ubuntu Rockchip dựng gadget chuột HID trên Orange Pi 5 Plus (`libcomposite`, script cấu hình): Windows thấy thiết bị nhưng báo "USB Input" "with a problem", dù đã sửa descriptor. Issue đóng vì cũ, không có lời giải ([ubuntu-rockchip #437](https://github.com/Joshua-Riek/ubuntu-rockchip/issues/437)). Tức là chế độ device có lên (máy tính enumerate được), còn phần HID thì hỏng. **[Unknown]** lỗi do board, do kernel hay do script của họ.
- **Hệ quả:** nhiều khả năng được, chưa chắc. B1 (có UDC không) và B2 (laptop nhận bàn phím + chuột không) trả lời trong khoảng một giờ, chưa cần iPhone. Nếu thất bại: thử image của Orange Pi, rồi mới tới cáp CH9329.

**HDMI IN**
- **[Likely]** Driver: `rk_hdmirx` trong kernel của hãng, `snps_hdmirx` ở mainline. Trên image của hãng, node video có thể là `/dev/video20` ([rockchip-linux/mpp #587](https://github.com/rockchip-linux/mpp/issues/587)). Khung ra là ảnh thô, nên phải nén.
- **[Likely]** Driver của hãng thiếu `VIDIOC_G_PARM` (OBS bị crash) và cần vá để hợp chuẩn V4L2. Người dùng chỉ lấy được **tối đa 4K ở 30 fps** bằng GStreamer hoặc ffmpeg, dù thông số ghi 4K60 ([ubuntu-rockchip #830](https://github.com/Joshua-Riek/ubuntu-rockchip/issues/830)).
  Hệ quả: hộp không cần 4K. Server khai EDID 1080p60 để iPhone mirror ở 1920×1080.
- **[Likely]** Một người dùng (image Orange Pi 1.0.8, Ubuntu jammy, kernel 5.10.160) đo được trễ **khoảng 2 s ở 30 fps** là tốt nhất, với `ffmpeg -re -f v4l2 /dev/video20 -c:v hevc_rkmpp ...` ghi ra file; không có trả lời từ nhóm phát triển ([rockchip-linux/mpp #587](https://github.com/rockchip-linux/mpp/issues/587)).
  **[Unknown]** bao nhiêu phần do driver, bao nhiêu do pipeline đó (nén HEVC, ghi file). Pipeline của hộp khác: nén JPEG, hàng đợi chỉ giữ một khung (bỏ khung cũ khi bộ mã hoá chậm). Phải đo (B3, B7).
- **Hệ quả:** R3 là rủi ro thật, nhưng có đường lui: đổi bộ mã hoá (`mpp`, `gst`, `ffmpeg`, hoặc lệnh riêng qua `video.command`), hoặc dùng capture card MS2109 cắm vào board.

### 3.6 iPhone 15/16/17 với hub USB-C

- **[Confirmed]** iPhone dùng giao thức DisplayPort để xuất tới màn hình USB-C, tối đa 4K60. Bản Pro của 15/16 có USB 3; các bản khác là USB 2 (đủ cho HID).
  Nguồn: [Apple 105099](https://support.apple.com/en-us/105099) (trích đoạn).
- **[Contradicted]** "Mọi iPhone USB-C": 16e không có DP Alt Mode, 17e cũng không nằm trong danh sách. iPhone Air thì có DP.
  Nguồn: [macReports](https://macreports.com/does-iphone-16e-support-hdmi-output-what-you-need-to-know/), [spec iPhone Air](https://support.apple.com/en-us/125092) (trích đoạn).
- **[Likely]** Apple USB-C Digital AV Multiport Adapter (HDMI 4K60 + USB-A + USB-C PD 60 W) được mô tả là dùng được với iPhone ([listing](https://www.amazon.com/Apple-USB-C-Digital-Multiport-Adapter/dp/B0D9MMFQNL), trích đoạn). Đây là một **hub tham chiếu**. Hub hãng khác (UGREEN, Anker, Cable Matters…) phải tự kiểm chứng. Ví dụ đang nhắm: UGREEN Revodok 105 (15495): HDMI 4K30 qua DP Alt Mode, 1 cổng USB-A 3.0 + 2 cổng USB-A 2.0, cổng USB-C PD 100 W vào (chỉ để cấp nguồn).
  **[Unknown]** Chưa có danh sách mẫu hub nào được kiểm chứng chạy HID + HDMI + PD cùng lúc trên iPhone. Aiden không ghi tên hub của họ.
- **[Likely]** Hub hay trục trặc khi cắm nguồn sau; nên cắm sạc PD vào hub **trước** rồi mới cắm iPhone ([Lention](https://www.lention.com/blogs/news/usb-c-hub-iphone-17-16-15-hdmi-storage-charging), trích đoạn, nguồn là blog của hãng bán hub).
- **[Likely]** ADG chỉ ghi iPad USB-C hỗ trợ USB PD Fast Role Swap (§25.3, [ADG PDF](https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf) trang 239), không nhắc iPhone.
  Suy ra: nếu nguồn PD vào hub chập chờn, hub mất nguồn và reset, **cả HDMI lẫn HID rớt rồi enumerate lại**. Đây là suy luận, cần B6.
- **[Confirmed]** iPhone cấp tối đa 900 mA (4,5 W) cho phụ kiện qua PD. Qua Type-C Current mặc định: 500 mA với 15/16/17 thường, 900 mA với bản Pro (ADG Table 25-1). Đủ cho CH9329 (dự phòng). Board của hộp có nguồn 5 V/4 A riêng, không lấy nguồn từ iPhone.
- **[Likely]** Khi mirror, iPhone giữ tỉ lệ khoảng 19,5:9 và thêm viền đen hai bên trên khung 16:9. App không tắt được việc này ([Apple Community](https://discussions.apple.com/thread/255537489), [externaldisplayiphone](https://externaldisplayiphone.com/blog/iphone-external-display-no-black-bars/), trích đoạn).
  - Ở khung 1080p, máy để dọc chiếm khoảng 497×1080 px. Với iPhone 15 (393×852 pt) tức khoảng 1,27 px/pt, và 5 px ≈ 4 pt.
- **[Likely]** HDCP chỉ bật với nội dung có bảo vệ; nội dung đó ra màn đen khi capture. Chấp nhận được ([Apple Community](https://discussions.apple.com/thread/252537556), trích đoạn).
- **[Unknown]** Có hub luôn bật HDCP làm HDMI IN mất hình không (B3).

### 3.7 Wired Accessories, USB Restricted Mode, khoá màn hình

- **[Confirmed]** Quy tắc USB Restricted Mode:
  - Sau 1 giờ kể từ khi máy khoá (hoặc kể từ khi kết nối dữ liệu cuối cùng kết thúc), máy không cho kết nối dữ liệu mới cho tới khi mở khoá.
  - Trong 1 giờ đó, chỉ phụ kiện đã từng kết nối lúc máy mở khoá được phép. Danh sách này được nhớ 30 ngày.
  - Phụ kiện lạ cố kết nối sẽ khoá mọi kết nối dữ liệu.
  - Quá 3 ngày không có kết nối dữ liệu nào thì máy chặn ngay khi khoá.
  Nguồn: [Apple Platform Security](https://support.apple.com/guide/security/activating-data-connections-securely-ios-sec5044aad1b/web) (trích đoạn).
- **[Confirmed]** iOS 26 có *Privacy & Security > Wired Accessories* với 4 lựa chọn; mặc định là "Automatically Allow When Unlocked".
  Nguồn: [Macworld](https://www.macworld.com/article/2945482/ios-26-juice-jacking-wired-accessories-always-ask.html), [Certo](https://www.certosoftware.com/insights/new-ios-26-security-setting-aims-to-curb-juice-jacking-risks-on-iphone/) (trích đoạn).
- **[Confirmed]** iOS 26.0.1 có bug khoá cứng lựa chọn ở "Always Allow". iOS 26.2 đã sửa, nhưng đặt lại mặc định thành Always Allow kể cả với người đã chọn khác.
  Nguồn: [TidBITS](https://tidbits.com/2025/10/13/juice-jacking-protection-setting-broken-in-ios-26/) (trích đoạn).
  Hệ quả: **sau mỗi lần cập nhật iOS phải kiểm tra lại mục này.**
- **[Likely]** Apple viết: "if you use a USB assistive device to enter your passcode on your locked iPhone, many assistive devices automatically turn on the setting to allow USB devices" ([Apple 111806](https://support.apple.com/en-us/111806), trích đoạn).
  Tức là với "Always Allow", HID có dây gõ được passcode trên màn khoá.
  **[Unknown]** Chưa rõ điều này có đúng ngay sau khi khởi động lại (BFU) không (B6).
- **[Likely]** Từ iOS 18.1, máy tự khởi động lại về BFU khi ở trạng thái khoá liên tục 72 giờ ([Magnet Forensics](https://www.magnetforensics.com/blog/understanding-the-security-impacts-of-ios-18s-inactivity-reboot/), trích đoạn). Nếu giữ máy luôn mở khoá (Auto-Lock = Never) thì nhiều khả năng không bị ảnh hưởng. Đây là suy luận từ cơ chế đếm theo thời gian khoá.
- **Hệ quả vận hành:**
  - Auto-Lock = Never.
  - Tắt tự cập nhật iOS.
  - Chọn giữa "Always Allow" (sống sót qua khoá hoặc khởi động lại, nhưng kém an toàn) và "Automatically Allow When Unlocked" (an toàn, nhưng phải có người mở khoá sau mỗi sự cố).
  - Với farm máy chuyên dụng, **khuyến nghị "Always Allow"** và ghi rõ đánh đổi an ninh.
  - Low Power Mode ép Auto-Lock về 30 giây, phải tắt (đã có trong `docs/iphone-setup.md`).

### 3.8 Luồng hình

**HDMI IN của hộp** (bằng chứng ở mục 3.5)
- Server ghi EDID 1080p60 thẳng vào driver, đọc HDMI IN và nén JPEG: `mppjpegenc` qua GStreamer (bộ mã hoá JPEG phần cứng, nếu image có), nếu không thì `jpegenc` hoặc ffmpeg (phần mềm), cuối cùng là bộ đọc V4L2 riêng của hộp (OpenCV, không cần cài gì, chạy được khi board không có internet). Hàng đợi một khung, bỏ khung cũ khi bộ mã hoá chậm. Pipeline tự chạy lại khi mất tín hiệu hoặc đổi độ phân giải.
- **[Unknown]** fps thực tế, tải CPU với bộ mã hoá phần mềm, và trễ glass-to-glass (B3, B7).

**Capture card USB (dự phòng)**
- **[Likely]** MS2109: USB 2.0, MJPEG 1920×1080@30 và 1280×720@60; YUYV chỉ ở độ phân giải hoặc fps thấp. Card báo "USB 3.0" thường thực ra là USB 2.0.
  Nguồn: [naut.ca](https://www.naut.ca/blog/2020/07/09/cheap-hdmi-capture-card-review/) (trích đoạn), [TinyPilot wiki](https://github.com/tiny-pilot/tinypilot/wiki/HDMI-Capture-Devices).
- **[Likely]** MS2130:
  - Trên USB 3: chỉ YUV, 1080p60 và cả 1080p120. Trễ đo được khoảng 66 ms ở 1080p60 và 49 ms ở 1080p120.
  - Trên USB 2: MJPEG.
  - Firmware mới đổi danh sách mode.
  Nguồn: [HyperHDR #499](https://github.com/awawa-dev/HyperHDR/discussions/499). TinyPilot cũng xác nhận MS2130 ra YUYV nên phải nén bằng CPU ([#1593](https://github.com/tiny-pilot/tinypilot/discussions/1593)).
  **Hệ quả:** "MS2130 cho MJPEG passthrough" là sai trên USB 3.
- **[Likely]** TinyPilot trên Pi 4 với MS2109 đạt khoảng 200 ms trễ nhờ passthrough MJPEG qua uStreamer, giảm từ 500–600 ms khi nén lại ([mtlynch.io](https://mtlynch.io/tinypilot/), trích đoạn).

**Nhiều card trên một host (dự phòng, host nhiều máy)**
- **[Likely]** Lỗi "No space left on device" là do thiếu băng thông isochronous, không phải thiếu ổ đĩa. uvcvideo có xu hướng giữ trước băng thông của mode cao nhất ([The Good Penguin](https://www.thegoodpenguin.co.uk/blog/multiple-uvc-cameras-on-linux/), trích đoạn; [ustreamer #53](https://github.com/pikvm/ustreamer/issues/53); [linux-uvc-devel](https://linux-uvc-devel.narkive.com/hK5VJA2r/uvcvideo-overallocates-bandwidth-for-compressed-e-g-mjpg-video-proposed-fix)).
- **Tính toán** (USB 2.0 high-speed, 80% cho periodic ≈ 6000 B mỗi microframe):
  - Card giữ 3×1024 B/µframe (khoảng 24,6 MB/s) thì mỗi bus USB 2 **chỉ chứa 1 card**.
  - Nếu uvcvideo chọn alt setting 1×1024 (khoảng 8,2 MB/s) thì được khoảng 5 card.
  - MJPEG 1080p30 thực tế chỉ cần khoảng 2–6 MB/s.
  - **[Unknown]** Chưa rõ MS2109 khai `dwMaxPayloadTransferSize` bao nhiêu (`capture_check.py multi` đo khi cần).
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
  Automation không cần điều này (không có thị giác máy). **Nhưng điều khiển kiểu KVM relative thì cần** (B7).

### 3.9 Quy mô và sản phẩm

- **[Likely]** SOME 3C (khoảng 38 USD mỗi board):
  - Mỗi board một máy, hỗ trợ iPhone 6s trở lên với iOS 15 trở lên.
  - Lên mạng qua "OTG network cable"; có API HTTP, WebSocket và Python.
  - Không công bố cách lấy hình.
  Nguồn: [SOME 3C](https://some3c.com/products/iphone-farm-ios-automation-control-board), [Medium](https://some3ccom.medium.com/iphone-box-phone-farm-ios-without-jailbreak-controlling-iphone-via-computer-unified-control-4cce12c55e09) (trích đoạn).
- **[Likely]** iMouse: phần cứng HID cắm qua OTG, hình lấy qua **AirPlay mirroring** ("有线投屏" qua đường mạng OTG), điều khiển bằng nhận dạng ảnh, màu và OCR, yêu cầu iOS 13.5 trở lên.
  Nguồn: [iosautot](https://www.iosautot.cn/python-xp/), [CSDN](https://blog.csdn.net/SANGLIJUN/article/details/134271273) (trích đoạn).
  Tức là board thương mại **dựa vào thị giác máy và AirPlay**, đúng hai thứ dự án này đã loại. Đừng kỳ vọng sao chép được độ ổn định của họ mà không có hai thứ đó, trừ khi absolute chạy được.
- **[Confirmed]** Sipeed NanoKVM-Go (59–89 USD) có chế độ chuột tuyệt đối ("Follow Mouse") được ghi cho iPhone 15/16/17, một dây USB-C, khoảng 60 ms ở 1080p60 ([research/china-market.md](research/china-market.md) §2.9). Cùng ý tưởng "mỗi máy một bo" như hộp; dùng làm mốc đối chiếu (X2).
- **[Confirmed]** iPhone 15 trở lên có giới hạn sạc 80%; iOS 18 cho chọn 80/85/90/95/100%, thỉnh thoảng máy vẫn sạc lên 100% để hiệu chỉnh ([Apple 108055](https://support.apple.com/en-us/108055), [MacRumors](https://www.macrumors.com/how-to/use-new-iphone-charging-limit-options-ios-18/), trích đoạn).
- **[Likely]** Sạc 24/7 với màn hình luôn sáng làm nóng máy và tăng nguy cơ phồng pin. Cần giới hạn sạc, độ sáng tối thiểu, và thông gió. Nguồn phổ thông, chất lượng trung bình ([ví dụ](https://www.voltacharger.com/blogs/news/why-is-your-iphone-battery-bulging-causes-risks-and-what-to-do)).

---

## 4. Vì sao chọn hộp all-in-one

**Giải thích đơn giản**

- **iPhone USB-C giống một laptop nhỏ.** Qua một cổng USB-C, nó cùng lúc xuất hình (DisplayPort) và làm "máy chủ
  USB" cho chuột và bàn phím. Hub USB-C tách một sợi cáp thành ba đường: HDMI, cổng USB-A cho bàn phím + chuột, và
  cổng sạc. Vì thế **một dây làm được cả ba việc**.
- **Orange Pi 5 Plus làm được cả phần còn lại.** Cổng Type-C của nó giả làm bàn phím + chuột (Linux USB gadget, cách
  Aiden đang làm), cổng HDMI IN của nó nhận hình, và nó đủ mạnh để chạy server. Không cần chip HID riêng, không cần
  capture card, không phải ghép cặp linh kiện.

**Các phương án**

| Phương án | Đánh giá |
|---|---|
| **Hộp Orange Pi 5 Plus: gadget + HDMI IN** (chính) | Một board mỗi máy. "Đã giao" nghĩa là iPhone đã lấy báo cáo. Descriptor chuột tuyệt đối theo bố cục đã chạy được. Đổi profile bằng phần mềm. Rủi ro: chế độ device trên board này (R1, A10) và HDMI IN (R3, A11), cả hai chưa kiểm chứng. |
| **Board + cáp CH9329, hình từ HDMI IN** | Dự phòng khi image không có chế độ device. Chuột tuyệt đối của CH9329 chưa kiểm chứng (descriptor gộp). "Đã giao" chỉ là chip đã nhận. Đổi work mode phải rút cáp. |
| **Host Linux bất kỳ + cáp CH9329 + capture card MS2109** | Dự phòng chung; một host phục vụ nhiều máy, nhưng bị giới hạn bởi băng thông USB của capture card (A13). |
| **Sipeed NanoKVM-Go** | Sản phẩm có sẵn cùng ý tưởng. Dùng làm mốc đối chiếu (X2), không thay hộp: độ sâu API lập trình và độ bền chạy dài chưa rõ (china-market §2.9). |

**Khuyến nghị:**
1. Làm B1–B3 ngay khi có board. B1–B2 không cần iPhone; B3 chỉ cần iPhone làm nguồn hình.
2. Nếu B1/B2 thất bại: thử image của Orange Pi; nếu vẫn không được, dùng cáp CH9329 (D1–D3) và giữ HDMI IN.
3. Nếu B3/B7 cho hình chậm: thử bộ mã hoá khác hoặc lệnh riêng (`video.command`); cuối cùng là capture card MS2109.

---

## 5. Đề xuất thay đổi và lựa chọn tốt nhất

1. **Thứ tự ưu tiên con trỏ: absolute trước, relative là dự phòng** (phần mềm đã làm như vậy).
   - Test đầu tiên trên iPhone là chuột tuyệt đối của gadget (B4, profile RA rồi A).
   - Nếu chạy, ánh xạ lưới → pt là tuyến tính. Hiệu chỉnh bằng lưới điểm trên trang Safari, fit affine 6 tham số cho mỗi hướng màn hình.
   - Mỗi tap: move, chờ iOS trượt con trỏ tới (đo được khi hiệu chỉnh), nhấn, giữ 60 ms, nhả 2–3 lần (theo Aiden).
   - Reset góc và pacer chỉ còn dùng khi không có absolute.
2. **Descriptor:** gadget của hộp đã dùng bố cục giống Aiden/PiKVM: Mouse, Pointer, Collection Physical, X/Y 16 bit 0..32767, interface riêng không report ID, interface con trỏ đứng cuối. Bằng chứng mạnh nhất hiện có là cách này chạy trên iOS. Không thêm collection digitizer.
3. **Nếu buộc phải dùng relative:**
   - Với cáp CH9329: chạy ở 115200 baud (cấu hình một lần trên PC; có hiệu lực sau khi cấp nguồn lại).
   - Đặt nhịp pacer là bội của `bInterval`.
   - Dùng bước nhỏ, nằm trong vùng hệ số gần tuyến tính.
   - Neo ở **hai góc trên**.
   - Đo độ lặp lại (B5) trước khi hứa mục tiêu 4 pt.
   - Nếu độ lệch hệ số lớn hơn khoảng 0,5%, phải chấp nhận sai số lớn hơn hoặc chia quãng đi thành nhiều pha.
4. **Tầng lệnh "không mất lệnh" phải trung thực:**
   - Mọi báo cáo mang trạng thái đầy đủ: toạ độ tuyệt đối và trạng thái nút. Nhờ vậy retry an toàn: gửi lại vị trí đích và trạng thái nút cuối cùng.
   - Báo cáo nhả (chuột và phím) luôn gửi 2–3 lần.
   - Relative: sau mọi lỗi mơ hồ thì reset góc (như thiết kế).
   - Đọc trạng thái USB trước và sau mỗi thao tác để phát hiện iOS ngừng nhận.
   - Tài liệu và API phải nói rõ mức xác nhận: "host đã lấy" với gadget, "chip đã nhận" với CH9329. **Không có mức "iOS đã xử lý".**
5. **Hub:**
   - Thử 2–3 mẫu, ví dụ UGREEN Revodok 105 (15495), với Apple USB-C Digital AV Multiport Adapter làm tham chiếu.
   - Thứ tự cắm: sạc vào cổng PD của hub, HDMI và USB từ hub sang board, hub vào iPhone, cấp nguồn board sau cùng.
   - Dùng nguồn PD ổn định (tốt nhất có UPS). Mỗi lần mất nguồn là một lần rớt toàn bộ.
6. **Hộp và video:**
   - HDMI IN với EDID 1080p60, JPEG phần cứng (`mppjpegenc`) khi image có. Đo fps, CPU và trễ (B3, B7) trước khi chốt image.
   - Dự phòng: MS2109 cho MJPEG passthrough. MS2130 chỉ dùng khi cắm cổng USB 2 (cho MJPEG), hoặc khi cố ý nén trên host.
   - Host nhiều máy (dự phòng): mỗi bus USB 2 độc lập một capture card; gom các adapter USB-serial (CH340) qua hub USB 2.0 **Multi-TT có nguồn riêng**. Pi 5 chỉ hợp cho 1–2 máy, Pi 4 không quá 1 máy.
7. **Luồng hình:**
   - Mặc định MJPEG passthrough trong LAN.
   - Cắt vùng màn hình điện thoại và nén H.264 **chỉ khi có người xem từ xa**, gửi qua WebRTC hoặc WebSocket + WebCodecs (như "Direct H.264" của PiKVM). Việc này để sau.
8. **Console điều khiển từ xa:**
   - Nếu có absolute, dùng "absolute KVM": toạ độ chuột trên video → báo cáo HID absolute. Không cần Pointer Lock, chạy cả trên trình duyệt điện thoại (PiKVM ghi rõ relative không chạy trên trình duyệt mobile, [mouse.md](https://github.com/pikvm/pikvm/blob/master/docs/mouse.md)).
   - Console tự vẽ con trỏ ở vị trí đã biết, nên không phụ thuộc con trỏ có hiện trên HDMI hay không.
   - Pointer Lock relative chỉ dùng khi máy không theo absolute.
9. **Cài đặt iPhone** (bổ sung cho `docs/iphone-setup.md`):
   - Wired Accessories = **Always Allow** cho máy farm (ghi rõ đánh đổi an ninh), và kiểm tra lại sau mỗi lần cập nhật.
   - Tắt tự cập nhật và hoãn iOS 27 cho tới khi test xong.
   - Tắt Dwell, Hot Corners và Zoom.
   - Bật "Show Onscreen Keyboard" trong AssistiveTouch (Aiden khuyến nghị để nhập liệu ổn định).
   - Khoá cả hai thanh Tracking Speed.
   - Giới hạn sạc 80%, độ sáng tối thiểu.
10. **Phạm vi máy:** iPhone 15/16/17/Air, **trừ 16e/17e**. Hai máy này chỉ lấy được hình qua AirPlay, như board thương mại; dự án không làm.
11. **Trang hiệu chỉnh:**
    - Cài bằng "Add to Home Screen" (iOS 26 mặc định mở như web app).
    - Viewport: `viewport-fit=cover, width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no`.
    - CSS: `touch-action:none`.
    - Ghi mọi loại sự kiện kèm `pointerType` và timestamp; gửi về host qua WebSocket.

---

## 6. Ước lượng hiệu năng và quy mô

> Các con số dưới đây là **ước lượng có giả định**, chưa đo. Mỗi dòng ghi rõ giả định.

**Thời gian một lần tap**

| Chế độ | Tính toán | Kết quả |
|---|---|---|
| Absolute | move (vài ms) + chờ trượt 80 ms (Aiden) + nhấn (5 ms) + giữ 60 ms + nhả ×3 (khoảng 30 ms) | **khoảng 0,18–0,25 s/tap**, tối đa khoảng 4–5 tap/s. Nếu hiệu chỉnh đo ra thời gian trượt 250 ms (glassbox) thì khoảng 0,35 s. Thực tế bị giới hạn bởi tốc độ UI phản hồi. |
| Relative (reset góc + pacer) | t ≈ t_neo + 2·t_nghỉ + (dx/g_x + dy/g_y)·T + t_click. Giả định: T = 25 ms, g = 5–15 pt/báo cáo, dx trung bình 200 pt, dy trung bình 430 pt, t_neo khoảng 0,5 s, t_nghỉ khoảng 0,15 s | **khoảng 1,5–4 s/tap** (T = 8 ms thì khoảng 0,7–1,5 s) |

**Độ chính xác**
- Absolute: bước lưới 852 pt / 4096 ≈ 0,21 pt (iPhone 15). Sai số chủ yếu do hiệu chỉnh affine, **ước lượng ≤ 1–2 pt** nếu B4 đạt.
- Relative: sai số ≈ (độ lệch hệ số) × quãng đường. Ví dụ để 95% nằm trong 4 pt trên quãng 400 pt, cần σ(hệ số) ≲ 0,5%. **Chưa có dữ liệu** (B5).

**Băng thông HID**
- Gadget: không có serial. Mỗi interface một báo cáo đang chờ, host lấy theo `bInterval` (đọc ở B2).
- CH9329 (dự phòng) ở 9600 baud: khoảng 22–24 ms mỗi lệnh có ack, tức khoảng 40 lệnh/s. **Không đủ biên cho pacer 25 ms.** Ở 115200 baud: khoảng 4–5 ms mỗi lệnh, tức khoảng 200 lệnh/s (bị chặn tiếp bởi `bInterval`).

**Video (mỗi máy)**

| Giả định | Con số |
|---|---|
| MJPEG 1080p30, 60–200 KB/khung (vùng viền đen nén rất tốt; với HDMI IN của hộp, kích thước khung tuỳ bộ mã hoá, đo ở B3) | **khoảng 15–50 Mbit/s cho mỗi người xem**. LAN 1 GbE chứa khoảng 20–60 luồng người xem. |
| H.264 sau khi cắt vùng màn hình, khoảng 540×1170 @30 | khoảng 1,5–3 Mbit/s |
| Trễ glass-to-glass trong LAN, hộp (HDMI IN + JPEG) | **[Unknown]**, đo ở B7. Một báo cáo khoảng 2 s với pipeline ffmpeg + HEVC khác hẳn (A11) |
| Trễ glass-to-glass trong LAN, USB-C + MS2109 + MJPEG (dự phòng) | **khoảng 150–250 ms** (TinyPilot đo khoảng 200 ms với MS2109 trên Pi 4) |
| MS2130 1080p60 YUYV (USB 3, dự phòng) | 1920·1080·2·60 ≈ 249 MB/s ≈ 2 Gbit/s mỗi card, nên khoảng 1–2 card trên một controller 5 Gbps, và phải nén trên host |

**Số máy**
- Hộp: **1 máy mỗi board**, theo thiết kế. Thêm máy là thêm board.
- Dự phòng, host nhiều máy với capture card (chưa đo):

| Host | Bus USB 2 độc lập | MS2109 ở 1080p30 (nếu mỗi card giữ 3×1024 B/µframe) | Nếu uvcvideo chọn alt setting nhỏ | Nén H.264 phần cứng |
|---|---|---|---|---|
| Pi 4 | 1 | **1** | khoảng 3–4 | có (V4L2 M2M, 1 luồng 1080p) |
| Pi 5 | 2 | **2** | khoảng 6–8 | **không** |
| Mini PC x86 (N100/N305), 1–2 controller | 1–2 | 1–2 | khoảng 4–8 | QSV, vài luồng |
| Desktop x86 + 2 card PCIe mỗi card 4 controller | 8 + onboard | **khoảng 8–10** | khoảng 20+ (CPU và USB-serial lúc đó mới là giới hạn) | QSV/NVENC |

**Công suất và nhiệt (mỗi máy, ước lượng)**
- iPhone màn hình bật, độ sáng thấp, đã chạm giới hạn 80%: khoảng 2–4 W. Khi đang sạc lên có thể tới 20 W trở lên.
- Hub khoảng 0,5–1 W.
- Orange Pi 5 Plus: nguồn 5 V/4 A, tức tối đa 20 W; mức tiêu thụ thực tế **chưa đo** (B8 ghi nhiệt độ).
- Mỗi hộp cần hai nguồn: sạc 30 W trở lên cho hub và nguồn riêng của board. Khi cả dãy cùng sạc sau một lần mất điện, bộ nguồn chung phải chịu được mức đỉnh của cả hai.

**BOM mỗi máy (ước lượng giá lẻ 2026, chưa có báo giá; không tính iPhone)**

| Hạng mục | Hộp (chính) | Dự phòng: host nhiều máy |
|---|---|---|
| Orange Pi 5 Plus + nguồn 5 V/4 A | chưa có báo giá | — |
| HID: cáp CH9329 + CH340 | — | 5–10 USD |
| Hub USB-C (HDMI + USB-A + PD): Apple khoảng 69 USD, hãng khác 15–35 USD | 15–69 | 15–69 |
| Capture card MS2109 (MS2130 khoảng 19–25 USD, [CNX](https://www.cnx-software.com/2022/11/07/ms2130-based-4k-hdmi-to-usb-3-0-video-capture-dongle-sells-for-19/)) | — | 8–15 |
| Sạc PD (hoặc chia từ bộ nguồn nhiều cổng) | 8–15 | 8–15 |
| Cáp (HDMI, USB-A → USB-C, Ethernet), giá đỡ | khoảng 5 | khoảng 5 |
| Phần host chia cho mỗi máy | — (board là host) | 15–50 |
| **Tổng** | **chưa tính được** (thiếu giá board) | **khoảng 55–165 USD** |

So sánh: SOME 3C bán board khoảng 38 USD/máy, nhưng phần lấy hình của họ không công bố và nhiều khả năng là AirPlay. NanoKVM-Go 59–89 USD.

---

## 7. Test phần cứng

Chạy theo đúng thứ tự trong [phase0-checklist.md](phase0-checklist.md). Mỗi test ghi model iPhone, bản iOS (**26.x và 27.0**), image và kernel của board, mẫu hub.

| ID | Mục đích (rủi ro) | Cách làm (tóm tắt) | Đạt khi | Chi phí |
|---|---|---|---|---|
| **B1** | R1: cổng Type-C làm được device | `ls /sys/class/udc`, `tools/gadget.py status`; nếu trống thì thử image của Orange Pi | Có đúng một UDC, không bị gadget khác chiếm | 15 phút, không cần iPhone |
| **B2** | R1: gadget chạy thật | Gadget nối laptop bằng cáp USB-A → USB-C: `hidtest --gadget "info; type ...; move ...; abstest; bench"`, `lsusb -v`; đổi profile A rồi về RA | Laptop nhận bàn phím + chuột, gõ, di chuột, chuột tuyệt đối đúng; biết `bInterval` | 1 giờ, không cần iPhone |
| **B3** | R3, R5: HDMI IN | `capture_check list/snapshot/probe`, so bộ mã hoá `mpp`/`gst`/`ffmpeg`, EDID | Có hình iPhone ở 1080p, fps gần 30, CPU chấp nhận được | ½ ngày |
| **B4** | R2, R5, R6, R7: HID trên iPhone qua hub | `hidtest --gadget`: `info`, `abstest`, `type`, `capscheck`, `trial cmd+space`, Home/App Switcher; profile K cho bug phím tắt; profile A | Con trỏ tròn; chuột tuyệt đối chạy; gõ đúng; Caps Lock khứ hồi; biết tỉ lệ lỗi phím tắt | ½ ngày |
| **B5** | R2, R4, R7: hiệu chỉnh và độ chính xác | `deploy/install.sh`, Calibrate trên console; vuốt, tap lặp, hai mức Tracking Speed; chế độ tương đối ép buộc 3 lần | `mode: absolute`, sai số tối đa ≤ 2 pt (≤ 4 pt nếu relative); 0 lần kẹt kéo | ½ ngày |
| **B6** | R8, R5: độ bền | Khoá 5 và 70 phút, rút/cắm USB, HDMI, sạc, hub; khởi động lại board và iPhone | Có quy trình cho mỗi trường hợp; hộp tự lên sau khi khởi động lại | ½ ngày |
| **B7** | R3, R9: trễ hình | Đồng hồ ms trên iPhone so với ảnh trong trình duyệt, 5 lần; con trỏ có hiện trên HDMI không | Có số trễ; biết có cần vẽ con trỏ ảo | 2 giờ |
| **B8** | Tất cả: chạy bền | 72 giờ, script SDK tap mỗi 5 s, một lần rút điện có chủ đích | Không có lỗi nào không tự hồi phục; nhiệt dưới ngưỡng throttle | 3 ngày (chạy nền) |
| **D1** | Dự phòng: hiểu CH9329 trước khi đụng iPhone | Cả hai đầu cáp vào PC Linux: `cfg`, `bench`, `hid_loopback`, `lsusb -v`, dump descriptor | Biết `bInterval`, chip xếp hàng hay ghi đè, nhịp tối thiểu, descriptor tuyệt đối | 1 giờ, không cần iPhone |
| **D2** | Dự phòng: baud | 115200 baud, `RESET` có áp dụng cấu hình không | Biết độ trễ mỗi lệnh ở hai mức baud | 1 giờ |
| **D3** | Dự phòng: CH9329 trên iPhone | Các bước của B4 và B5 với `--port`; work mode 0x02 nếu absolute không chạy; work mode 0x01 cho phím tắt | Biết CH9329 có dùng được chuột tuyệt đối không | ½ ngày |

**Điều kiện dừng sớm:**
- Nếu B1, B2 đạt và B4 cho chuột tuyệt đối, hộp chuyển sang **GO**. Làm tiếp B5–B8 để hoàn thiện.
- Nếu B1 hoặc B2 thất bại (sau khi đã thử image của Orange Pi), chuyển phần HID sang D1–D3; B3, B5–B8 vẫn làm với cáp CH9329.
- Nếu B4 không có chuột tuyệt đối ở cả profile RA lẫn A, làm phần tương đối của B5 **trước khi** hứa mục tiêu 4 pt.
