# Giao thức serial CH9329: ghi chú đã đối chiếu

Cáp CH9329 là **phương án dự phòng** cho bàn phím + chuột: dùng khi image của board không có chế độ USB device,
hoặc trên một host Linux khác. Cấu hình chính là hộp Orange Pi 5 Plus, trong đó chính board là bàn phím + chuột
(Linux USB gadget, [orange-pi-box.md](../guide/orange-pi-box.md)). Hai đường có cùng API trong phần mềm.

Nguồn: tài liệu của WCH **"CH9329芯片串口通信协议" V1.0** (nằm trong gói CH9329EVT.ZIP) và datasheet
**CH9329DS1 V1.0**. Các khung mẫu in trong tài liệu được dùng làm dữ liệu kiểm thử chuẩn trong
`tests/test_protocol.py`. Thư viện `pych9329-hid` (MIT) chỉ dùng để đối chiếu, không copy code.

Ký hiệu: ✅ tài liệu gốc ghi rõ · ⚠️ tài liệu không ghi, đang giả định · 🔬 phải đo trên phần cứng.

## Khung lệnh ✅

```
57 AB | ADDR | CMD | LEN | DATA[LEN] | SUM        SUM = (tổng mọi byte phía trước) & 0xFF
```

- UART 8N1. Baud mặc định 9600. Tốc độ phổ biến được hỗ trợ: 9600, 19200, 38400, 57600, 115200.
- `LEN` từ 0 đến 64.
- Mã lệnh host gửi nằm trong khoảng 0x01–0x3F. Chip trả `CMD | 0x80` khi thành công và `CMD | 0xC0` khi lỗi.
  Khung lỗi mang 1 byte trạng thái.
- Host phải coi là thất bại nếu **không có phản hồi hợp lệ trong 500 ms**. Driver dùng giá trị này làm timeout
  mặc định.
- Chip nhận khung lỗi thì hoặc trả khung lỗi, hoặc bỏ qua im lặng. Vì vậy driver phải chịu được cả hai.
- Chip coi một gói đã kết thúc nếu quá **packet interval** (mặc định 3 ms) mà không nhận thêm byte. ⚠️ Chưa rõ
  gửi dồn nhiều khung sát nhau có ổn không. Driver gửi từng khung và `flush()` đến khi bytes ra hết đường
  truyền. 🔬 Bài D1 (`bench`) của giai đoạn 0 sẽ đo.

### Địa chỉ ✅

- Mặc định `0x00`: chip nhận khung với **bất kỳ** địa chỉ nào.
- Nếu đặt 0x01–0xFE: chip chỉ nhận khung đúng địa chỉ đó, hoặc `0xFF`.
- `0xFF` là **broadcast**: chip thực thi nhưng **không trả lời**. Driver không cho gửi lệnh cần phản hồi tới
  0xFF.
- Công cụ dò (`python -m ihc.hid.scan --scan-addr`) quét 0x01–0xFE khi địa chỉ bị đổi mà không nhớ.

### Mã trạng thái ✅

| Mã | Tên | Ý nghĩa |
|---|---|---|
| 00 | SUCCESS | thành công |
| E1 | ERR_TIMEOUT | chip chờ byte tiếp theo quá lâu |
| E2 | ERR_HEAD | sai header |
| E3 | ERR_CMD | mã lệnh không hợp lệ |
| E4 | ERR_SUM | sai checksum |
| E5 | ERR_PARA | sai tham số |
| E6 | ERR_OPERATE | khung đúng nhưng thực thi thất bại |

⚠️ Tài liệu không nói lệnh gửi HID sẽ phản hồi thế nào khi phía USB chưa được enumerate (iPhone khoá, chưa cho
phép phụ kiện). Chip giả trả E6. 🔬 Bài D3 (`watch`) của giai đoạn 0 sẽ ghi lại hành vi thật.

## Bảng lệnh ✅

| CMD | Tên | DATA gửi | Phản hồi |
|---|---|---|---|
| 0x01 | GET_INFO | — | 8 byte: version (0x30 = V1.0), USB (0x01 = đã kết nối và được nhận), LED (bit0 Num, bit1 Caps, bit2 Scroll), 5 byte dự trữ |
| 0x02 | SEND_KB_GENERAL_DATA | 8 byte `mod 00 k1..k6` | status |
| 0x03 | SEND_KB_MEDIA_DATA | ACPI `01 bits` **hoặc** multimedia `02 b1 b2 b3` | status |
| 0x04 | SEND_MS_ABS_DATA | 7 byte `02 btn Xlo Xhi Ylo Yhi wheel`, lưới 4096×4096, little-endian | status |
| 0x05 | SEND_MS_REL_DATA | 5 byte `01 btn dx dy wheel` (int8) | status |
| 0x06 | SEND_MY_HID_DATA | 0–64 byte | status |
| 0x87 | (chip tự gửi) | dữ liệu host USB ghi vào custom HID | — không phản hồi |
| 0x08 | GET_PARA_CFG | — | 50 byte cấu hình |
| 0x09 | SET_PARA_CFG | 50 byte | status |
| 0x0A | GET_USB_STRING | 1 byte loại (0 vendor, 1 product, 2 serial) | `loại, độ dài, chuỗi` |
| 0x0B | SET_USB_STRING | `loại, độ dài, chuỗi` (≤ 23 byte) | status |
| 0x0C | SET_DEFAULT_CFG | — | status |
| 0x0F | RESET | — | status |

- **Chuột tương đối:** dx dương = sang phải, dy dương = xuống. Wheel dương = **cuộn lên**.
- **Nút chuột:** bit0 trái, bit1 phải, bit2 giữa.
- **Modifier:** 0x01 LCtrl, 0x02 LShift, 0x04 LAlt, 0x08 LGUI (Command), 0x10–0x80 là các phím tương ứng bên
  phải.
- **Multimedia** (bit n = bit n%8 của byte n//8):

  | Bit | Phím |
  |---|---|
  | 0–7 | Volume+, Volume−, Mute, Play/Pause, Next, Prev, CD Stop, Eject |
  | 8–15 | E-Mail, WWW Search, Favorites, WWW Home, Back, Forward, WWW Stop, Refresh |
  | 16–23 | Media, Explorer, Calculator, Screen Save, My Computer, Minimize, Record, Rewind |

- **ACPI:** bit0 Power, bit1 Sleep, bit2 Wake-up.

## Cấu hình 50 byte ✅ (thứ tự byte ⚠️)

| Byte | Trường | Mặc định |
|---|---|---|
| 0 | work mode | 0x80 |
| 1 | serial mode | 0x80 |
| 2 | address | 0x00 |
| 3–6 | baud, **big-endian ✅** | 00 00 25 80 = 9600 |
| 7–8 | dự trữ | |
| 9–10 | packet interval (ms) | 3 |
| 11–12 | VID | 0x1A86 |
| 13–14 | PID | 0xE129 (khác nhau theo work mode) |
| 15–16 | kb upload interval (chỉ ASCII mode) | 0 |
| 17–18 | kb release delay (chỉ ASCII mode) | 1 |
| 19 | auto enter (chỉ ASCII mode) | 0 |
| 20–27 | ký tự enter, 2 nhóm × 4 byte | 0x0D |
| 28–35 | chuỗi lọc bắt đầu/kết thúc | |
| 36 | cờ USB string: bit7 bật chuỗi tuỳ chỉnh; bit2 vendor, bit1 product, bit0 serial | 0 |
| 37 | kb fast upload (chỉ ASCII mode) | 0 |
| 38–49 | dự trữ | |

⚠️ Tài liệu chỉ ghi rõ thứ tự byte cho baud. Các trường 2 byte đang được giả định là big-endian.
🔬 Kiểm chứng: dump cấu hình một chip mới xuất xưởng phải thấy VID = `0x1A86` và release delay = `1`. Lệnh
`cfg` của `hidtest` tự cảnh báo nếu thấy `0x861A`.

**Work mode** ✅:

| Giá trị | Chế độ |
|---|---|
| 0x00 | bàn phím (thường + media) + chuột (tuyệt đối + tương đối) |
| 0x01 | **chỉ bàn phím thường** (không có media) |
| 0x02 | **chỉ chuột** |
| 0x03 | custom HID |
| 0x80–0x83 | như trên, nhưng chọn bằng chân MODE1/MODE0 |

Serial mode: 0 giao thức, 1 ASCII, 2 trong suốt; 0x8x là chọn bằng chân CFG.

**Quy tắc ghi** ✅:
- Khi ghi, work mode chỉ nhận 0x00–0x03, serial mode chỉ nhận 0x00–0x02. Chip xuất xưởng đọc ra 0x80/0x80,
  nên ghi lại nguyên văn sẽ bị từ chối. `ChipConfig.for_write()` đổi sang giá trị phần mềm tương đương và
  thông báo rõ khi làm vậy.
- **Mọi thay đổi chỉ có hiệu lực ở lần cấp nguồn tiếp theo.** ⚠️ Chưa rõ lệnh `RESET` có áp dụng không.
  🔬 Bài D2 của checklist giai đoạn 0 thử điều này.

## Cứu chip ✅

- **Quên baud/địa chỉ:** chạy `python -m ihc.hid.scan` (thêm `--scan-addr` nếu cần).
- **Khôi phục mặc định bằng phần mềm:** `hidtest cfg default`, sau đó rút ra cắm lại, rồi kết nối ở 9600.
- **Khôi phục mặc định bằng phần cứng:** kéo chân **DEF** xuống mức thấp hơn 3 s, thả ra, đợi 200 ms.
- **Buộc về chế độ giao thức** (khi lỡ đặt ASCII/trong suốt): kéo chân **SET** xuống thấp.
- Trên cáp thành phẩm, DEF/SET thường không đưa ra ngoài, nên **công cụ không bao giờ tự ghi cấu hình**.
  Mọi lần ghi đều in diff, tự backup, và phải gõ `YES`.

## Những điểm khác với giả định ban đầu

| Giả định ban đầu | Thực tế theo tài liệu gốc |
|---|---|
| work mode 0x02 = "bàn phím + chuột (?)" | 0x02 = **chỉ chuột** |
| cấu hình có hiệu lực sau reset | có hiệu lực ở **lần cấp nguồn tiếp theo** |
| media: `02 b1 b2 b3` | đúng, LEN = 4 (bảng trong tài liệu ghi 2, nhưng khung mẫu dùng 4) |
| — | địa chỉ 0xFF là broadcast, không phản hồi |
| — | timeout host 500 ms; LEN tối đa 64 |

## Hệ quả cho thiết kế

- **Cách né bug phím tắt bằng "profile chỉ bàn phím":** đổi sang work mode 0x01 bắt buộc phải cấp nguồn lại
  chip. Trên cáp thành phẩm, chip lấy nguồn từ phía iPhone/hub, nên phải rút cáp. Đây là thao tác tay, không
  làm được trong lúc chạy. Nếu bug phím tắt xảy ra thường xuyên, dùng gadget của hộp: nó đổi profile bằng phần
  mềm (`sudo ihc gadget up --replace --profile K`). Xem `docs/research/feasibility.md`, mục 3.4.
- **Thông lượng:** mỗi báo cáo chuột kèm ack mất 18 byte trên dây (11 byte gửi đi, 7 byte ack), tức 18,75 ms ở
  9600 baud (~50 báo cáo/s) nếu chờ từng ack. Ở 115200 baud còn khoảng 1,6 ms. Nhịp chạy mặc định là 20 ms/báo
  cáo; trong một đoạn chạy, driver không chờ từng ack (ack đi đường riêng, UART full-duplex), nên mỗi báo cáo
  chỉ chiếm khoảng 11,5 ms dây gửi ở 9600 baud. Nâng baud giúp giảm độ trễ từng lệnh và tăng biên cho nhịp.
