# ESP32-S3 HID bridge: nói giao thức serial của CH9329

Firmware cho **ESP32-S3** (ESP-IDF v5.4). Host gửi khung lệnh CH9329 qua cổng serial. ESP32 biến chúng thành báo cáo
HID bàn phím / chuột / phím media rồi gửi sang iPhone qua **Bluetooth LE** (biến thể mặc định) hoặc qua **USB**
(biến thể TinyUSB).

Driver host `ihc/hid/ch9329.py` dùng được **nguyên trạng**: cùng khung `57 AB …`, cùng mã lệnh, cùng mã lỗi, cùng
timeout 500 ms.

> **Trạng thái:** cả hai biến thể, mỗi biến thể với hai cách chọn con trỏ (mặc định và chỉ tuyệt đối), **đã build
> (biên dịch + link) thành công, 0 warning** với ESP-IDF v5.4 trong môi trường phát triển. Firmware **chưa được nạp và chưa chạy trên phần cứng thật**. Mọi hành vi phía iPhone vẫn phải
> kiểm chứng (xem [Phải kiểm chứng trên phần cứng](#phải-kiểm-chứng-trên-phần-cứng)). Lõi giao thức đã được kiểm
> thử kỹ trên máy host, kể cả bằng driver Python thật (xem [Kiểm thử](#kiểm-thử-trên-máy-host-không-cần-phần-cứng)).

## Vì sao có firmware này

- **iPhone cổng USB-C (15 trở lên):** hub USB-C vừa xuất HDMI vừa nhận bàn phím/chuột USB. Chip WCH **CH9329**
  (serial → USB HID) cắm vào hub là đủ.
- **iPhone cổng Lightning:** chỉ có **một** cổng, và cổng đó đã dành cho **Lightning Digital AV Adapter** để lấy HDMI.
  Cổng Lightning phụ trên adapter **chỉ để sạc**, không có dữ liệu USB. Vì vậy không có đường nào cho HID có dây, và
  điều khiển phải đi qua **Bluetooth**: ESP32 đóng vai bàn phím + chuột tương đối + consumer control Bluetooth LE.
  Con trỏ của iOS (AssistiveTouch) đi theo chuột tương đối.
- **Cùng giao thức với CH9329:** host không cần biết đầu kia là CH9329 hay ESP32. `hidtest`, `bench`, pacer,
  closed-loop… chạy y hệt. Chỉ khác cổng serial (`--port`).
- **Biến thể USB:** cùng một board ESP32-S3 thay được CH9329 cho dòng USB-C. Nó có thêm lợi ích của firmware tự viết:
  - con trỏ **tuyệt đối**;
  - profile **chỉ bàn phím** có PID riêng;
  - xác nhận từng báo cáo đã được máy chủ USB đọc.

## Hai biến thể

| | BLE (mặc định) | USB (TinyUSB) |
|---|---|---|
| Dùng cho | iPhone Lightning (và bất kỳ iPhone nào qua Bluetooth) | iPhone USB-C qua hub (thay CH9329) |
| Đường tới iPhone | Bluetooth LE, HID over GATT | cổng **USB** native của DevKit (GPIO19/20) |
| Build | `sdkconfig.defaults` | `sdkconfig.defaults` + `sdkconfig.defaults.usb` |
| "00" nghĩa là | NimBLE đã nhận notification cho central đã kết nối, đã mã hoá, đã subscribe báo cáo đó | TinyUSB đã nhận báo cáo **và** máy chủ USB đã đọc nó khỏi endpoint |
| Console log | cổng USB Serial/JTAG (cổng "USB" của DevKit) | tắt (cổng USB đã thuộc về HID) |

Chọn lúc build bằng Kconfig `BRIDGE_OUTPUT` (menu *CH9329 HID bridge*).

## Nối dây

```
Biến thể BLE:
  Host Linux ──USB──► DevKit cổng "UART"/"COM" (chip USB-UART CP2102N/CH343 → UART0: GPIO43 TX, GPIO44 RX)
  ESP32-S3 ~~~ Bluetooth LE ~~~ iPhone (Lightning, HDMI qua Digital AV Adapter)
  (tuỳ chọn) cổng "USB" của DevKit → máy tính để xem log

Biến thể USB:
  Host Linux ──USB──► DevKit cổng "UART"/"COM" (như trên)
  DevKit cổng "USB" (native, GPIO19/20) ──USB──► hub USB-C của iPhone
```

- Chỉ cần **một cáp** cho biến thể BLE: cáp đó vừa cấp nguồn vừa mang dữ liệu.
- Dùng adapter USB-UART rời thay cho cổng "UART" của DevKit cũng được: TX adapter → GPIO44, RX adapter → GPIO43, nối
  GND, mức 3,3 V. Chân đổi được trong menuconfig (`BRIDGE_UART_TX_GPIO` / `BRIDGE_UART_RX_GPIO`).
- Baud mặc định **9600 8N1**, giống CH9329 xuất xưởng. Đổi bằng `hidtest cfg set baud=115200` rồi `RESET`.

## Build và nạp

### Cách build thông thường (có Internet, ESP-IDF v5.4)

```bash
. $IDF_PATH/export.sh                       # ESP-IDF v5.4
cd firmware/esp32_ble_hid

# Biến thể BLE
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash                # cổng "UART" của DevKit (esptool tự reset qua DTR/RTS)

# Biến thể USB (thư mục build và sdkconfig riêng)
idf.py -B build_usb -D SDKCONFIG=build_usb/sdkconfig \
       -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.usb" set-target esp32s3 build
idf.py -B build_usb -p /dev/ttyUSB0 flash
```

### Chọn con trỏ lúc build (`BRIDGE_POINTERS`)

Mặc định có cả chuột tương đối lẫn con trỏ tuyệt đối. Lớp `sdkconfig.defaults.abs_only` chọn **chỉ con trỏ
tuyệt đối** (T1 run B, T9); chỉ tương đối thì chọn trong menuconfig (*CH9329 HID bridge > Pointer collections*).
Mỗi biến thể một thư mục build:

```bash
# USB, chỉ con trỏ tuyệt đối (T1 run B: cấu hình tham chiếu)
idf.py -B build_usb_abs -D SDKCONFIG=build_usb_abs/sdkconfig \
       -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.usb;sdkconfig.defaults.abs_only" \
       set-target esp32s3 build
# USB, tương đối + tuyệt đối (T1 run D): chính là lệnh build_usb ở trên
# BLE, chỉ con trỏ tuyệt đối (T9)
idf.py -B build_abs -D SDKCONFIG=build_abs/sdkconfig \
       -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.abs_only" set-target esp32s3 build
```

- Mỗi biến thể có tên và serial riêng (tag `-RA`, `-A`, `-R`…, xem [Danh tính descriptor](#danh-tính-descriptor-ios-cache-descriptor)).
- `SDKCONFIG_DEFAULTS` **không ghi đè** giá trị đã có trong `sdkconfig` của thư mục build, chỉ điền giá trị còn
  thiếu. Đổi biến thể trong cùng thư mục thì xoá `sdkconfig` của nó trước, hoặc dùng menuconfig.
- Thư mục build cũ còn `CONFIG_BRIDGE_PID_COMPOSITE=0x4008` (PID của thứ tự interface USB cũ) sẽ dừng ở `#error`
  trong `bridge_config.h`: xoá `sdkconfig` của thư mục đó (PID mặc định mới là 0x4028).

### Ghi chú

- IDF Component Manager tự tải `espressif/esp_tinyusb ^1.7.0` (khai báo trong `main/idf_component.yml`) cho cả hai
  biến thể. Bản BLE có biên dịch nó nhưng không khởi động và không link vào.
- Nếu nạp không vào: giữ **BOOT**, nhấn **RST**, thả BOOT, rồi nạp lại.
- Log của biến thể BLE nằm ở cổng "USB": `idf.py -p /dev/ttyACM0 monitor`.

### Build đã thực sự chạy ở đâu và thế nào (ghi chép trung thực)

- **Thời điểm và bộ công cụ:**
  - Ngày 2026-09-24.
  - ESP-IDF **v5.4**, commit `67c1de1eebe095d554d281952fde63c16ee2dca0`, clone bằng
    `git clone --depth 1 -b v5.4 --recursive --shallow-submodules`.
  - Toolchain `xtensa-esp-elf` **esp-14.2.0_20241119** (GCC 14.2.0) và `esp-rom-elfs 20241011`, cài bằng
    `idf_tools.py install xtensa-esp-elf esp-rom-elfs` (tải từ GitHub releases).
  - CMake 3.28.3 và Ninja 1.11.1 của hệ thống, Python 3.11.
- **Giới hạn mạng:** proxy chặn `dl.espressif.com` và `components.espressif.com`. Hệ quả:
  - Python env cài từ PyPI không kèm constraints file: `IDF_PYTHON_CHECK_CONSTRAINTS=no IDF_PIP_WHEELS_URL=`, rồi
    `idf_tools.py install-python-env --no-constraints`.
  - Một số gói phải ghim lại bản tương thích IDF 5.4: `idf-component-manager 2.5.2`, `esp-idf-kconfig 2.5.4`,
    `esptool 4.12.0`, `esp-idf-size 1.7.1`. Bản 3.x của component manager không chạy được với IDF 5.4.
  - `export.sh` đòi đủ mọi tool (gdb, openocd…) nên môi trường được đặt tay: `IDF_PATH`, `PATH`, `IDF_PYTHON_ENV_PATH`,
    `ESP_ROM_ELF_DIR`, `ESP_IDF_VERSION=5.4`.
- **Không dùng Component Manager** (`IDF_COMPONENT_MANAGER=0`):
  - **esp_tinyusb 1.7.6~2**: repo `espressif/esp-usb`, nhánh `esp_tinyusb/v1`, commit `c0be948c`.
  - **tinyusb v0.18.0.6**: repo `espressif/tinyusb`, commit `d018ab60`.
  - Cả hai clone từ GitHub, truyền vào bằng `EXTRA_COMPONENT_DIRS`.
  - Bản copy tạm của esp_tinyusb có thêm `tinyusb` vào `REQUIRES`: dòng này Component Manager bình thường tự thêm.
  - Khi `IDF_COMPONENT_MANAGER=0`, `main/CMakeLists.txt` tự thêm `esp_tinyusb` vào `REQUIRES`.
- **Lệnh đã chạy:** thư mục build nằm ngoài cây mã nguồn.

  ```bash
  export IDF_COMPONENT_MANAGER=0
  idf.py -B /tmp/fwbuild -DSDKCONFIG=/tmp/fwbuild/sdkconfig -DEXTRA_COMPONENT_DIRS=/tmp/ext \
         set-target esp32s3 build
  idf.py -B /tmp/fwbuild_usb -DSDKCONFIG=/tmp/fwbuild_usb/sdkconfig -DEXTRA_COMPONENT_DIRS=/tmp/ext \
         -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.usb" set-target esp32s3 build
  # chỉ con trỏ tuyệt đối: thêm ";sdkconfig.defaults.abs_only" vào SDKCONFIG_DEFAULTS, thư mục build riêng
  ```

- **Kết quả** (lần build lại ngày 2026-09-24, sau các thay đổi descriptor, run 0,25 ms / E7 và khớp xác nhận USB;
  cùng bộ công cụ, thư mục build mới): cả bốn cấu hình **0 warning, 0 error**. Code của bridge được biên dịch với
  `-Werror -Wshadow -Wsign-compare -Wunused-parameter`; lõi giao thức thêm `-Wextra -Wconversion -Wsign-conversion`.

  | Cấu hình | Ảnh | Kiểm tra bằng `nm` |
  |---|---|---|
  | BLE, tương đối + tuyệt đối | **558 864 byte** | có NimBLE, không có TinyUSB |
  | BLE, chỉ tuyệt đối | 558 864 byte (khác nội dung, cùng kích thước) | như trên |
  | USB, tương đối + tuyệt đối | **292 576 byte** | có TinyUSB và `hid_confirm`, không có Bluetooth |
  | USB, chỉ tuyệt đối | 292 576 byte (khác nội dung, cùng kích thước) | như trên |

  - Mỗi `sdkconfig` sinh ra có đúng `BRIDGE_POINTERS`, PID composite 0x4028 và `BRIDGE_REL_RUN_LATE_US=2000`.
  - Build lại trong thư mục cũ (sdkconfig còn PID 0x4008) dừng đúng ở `#error` của `bridge_config.h`.
  - Phân vùng app là 1,5 MB (`partitions.csv`): BLE dùng 36%, USB 19%.
- **Chưa làm:**
  - Chưa build qua đường Component Manager thật (registry bị chặn).
  - **Chưa nạp, chưa chạy trên phần cứng.**

## Ghép với iPhone (biến thể BLE)

1. Nạp firmware và cắm DevKit vào host. Log (cổng "USB") hiện `advertising as "HID Bridge XXXX-RA"`. XXXX là 2 byte
   cuối địa chỉ Bluetooth, `-RA` là tag của bản build (xem [Danh tính descriptor](#danh-tính-descriptor-ios-cache-descriptor)).
2. Trên iPhone: **Settings > Bluetooth**. Chọn **HID Bridge XXXX-RA** ở mục *Other Devices*. Nếu iOS hỏi, bấm
   **Pair**. Ghép theo kiểu *Just Works* với LE Secure Connections: không có mã PIN. Bridge không tự xin ghép: iPhone
   tự bắt đầu khi đọc đặc tính cần mã hoá, đúng như Apple yêu cầu.
3. Bật con trỏ: **Settings > Accessibility > Touch > AssistiveTouch: ON**. Tuỳ chỉnh con trỏ ở *Pointer Control*.
   Xem thêm `docs/iphone-setup.md`.
4. Kiểm tra từ host:

   ```bash
   python tools/hidtest.py --port /dev/ttyUSB0 "info; move 200 0; move 0 200"
   ```

   `info` in ra `chip unknown (0x40)` (0x40 là mã của bridge, xem bên dưới) và `USB connected`, tức byte trạng thái
   liên kết = 1.
5. **Kết nối lại:** tự động sau khi iPhone hoặc ESP32 khởi động lại, vì khoá ghép được lưu trong NVS. Bridge quảng bá
   lại ngay khi mất kết nối.
6. **Đổi sang iPhone khác / xoá ghép:**
   - Giữ nút **BOOT** 3 giây **khi firmware đang chạy** để xoá mọi bond trong ESP32 (log: `all bonds deleted`).
     Không giữ BOOT lúc cắm điện hay lúc nhấn RST: GPIO0 là chân strapping, giữ BOOT khi khởi động sẽ vào chế độ
     nạp của ROM chứ không chạy firmware.
   - Trên iPhone cũ: Settings > Bluetooth > (i) > **Forget This Device**.
   - Mỗi ESP32 chỉ nên ghép với một iPhone. Trong giá nhiều máy, ghép từng cặp một.
7. **Đổi tên Bluetooth:** `SET_USB_STRING` loại 1 (product), rồi `RESET`. Tag vẫn được nối vào sau tên mới. iOS có
   thể nhớ tên cũ cho tới khi Forget + ghép lại.
8. **Sau mọi thay đổi descriptor** (đổi `BRIDGE_POINTERS`, đổi work mode, nạp firmware làm đổi Report Map): làm
   đủ [ba bước Forget + xoá bond + ghép lại](#danh-tính-descriptor-ios-cache-descriptor). Nếu không, kết quả đo có
   thể là của descriptor cũ trong cache của iPhone.

## Tương thích với CH9329

| Lệnh / hành vi | CH9329 | Bridge |
|---|---|---|
| `0x01 GET_INFO` | version 0x30…, byte 1 = USB đã enumerate | version **0x40** ("ihc bridge v1.0"). Byte 1 = **1 chỉ khi báo cáo thật sự tới được iPhone** (xem dưới). Byte 2 = LED do iPhone ghi. Byte 3–7 = thông tin bridge |
| `0x02` bàn phím | có | có. **00 chỉ khi báo cáo đã giao** (xem Độ tin cậy) |
| `0x03` media: `02 b1 b2 b3` | có | consumer control, report id 3, 24 bit theo đúng thứ tự bit của CH9329 |
| `0x03` ACPI: `01 bits` | có | System Control (Power/Sleep/Wake), report id 4. iOS xử lý thế nào: chưa rõ |
| `0x04` chuột tuyệt đối | có | con trỏ tuyệt đối riêng: BLE report id 5 (không có report id khi map chỉ có một collection), USB interface riêng. X/Y 0..4095 được đổi sang 0..32767. **X/Y > 4095 → E5**. Build không có con trỏ tuyệt đối → nhận rồi bỏ (hoặc E5, tuỳ `BRIDGE_ABS_MOUSE_REJECT`). Firmware không bao giờ tự phát báo cáo (0,0), xem [dưới](#không-bao-giờ-tự-phát-báo-cáo-tuyệt-đối-00) |
| `0x05` chuột tương đối | có | có. Giá trị −128 được gửi thành −127, vì descriptor là −127..127 |
| `0x06` custom HID | có | **E3** |
| `0x08/0x09` cấu hình 50 byte | có | có, lưu NVS. Khi ghi, kiểm tra: work mode 0x00–0x03, serial mode phải 0x00, baud ∈ {9600, 19200, 38400, 57600, 115200}, địa chỉ ≠ 0xFF. Sai → E5 |
| Cấu hình có hiệu lực | lần cấp nguồn sau | lần **khởi động** sau, **kể cả sau `RESET`**, vì bridge khởi động lại thật |
| VID/PID trong cấu hình | dùng cho USB | chỉ lưu và đọc lại. USB và BLE PnP ID dùng VID/PID trong Kconfig, để không bao giờ giả danh ID của WCH |
| `0x0A/0x0B` chuỗi USB | chuỗi USB | vendor → nhà sản xuất (DIS/USB), product → **tên Bluetooth**/chuỗi USB product, serial → số serial. Product và serial được nối thêm tag của bản build (`-RA`…); `0x0A` vẫn đọc ra đúng chuỗi đã lưu. Byte cờ 36 được lưu nhưng bỏ qua: chuỗi khác rỗng là được dùng |
| `0x0C` mặc định | có | có (cấu hình + chuỗi). **Không** xoá bond Bluetooth |
| `0x0F` RESET | có | trả lời trước, rồi ngắt BLE/USB gọn gàng và khởi động lại |
| `0x30` | E3 | **SEND_MS_REL_RUN** (mở rộng, xem dưới) |
| E7 | không có | mã của bridge, chỉ cho `0x30`: mọi báo cáo đã giao nhưng có báo cáo trễ |
| Địa chỉ | 0x00 nhận mọi địa chỉ, 0x01–0xFE chỉ nhận địa chỉ của mình hoặc 0xFF, 0xFF broadcast không trả lời | giống hệt, kể cả không trả lời E4/E1 cho khung broadcast |
| E1 (khung dở dang) | sau packet interval | sau packet interval + độ trễ gom byte của UART (thêm 23 ms ở 9600, 4 ms ở 115200). Chỉ gửi khi đã nhận đủ ADDR và CMD |
| E2 | ? | không bao giờ gửi: byte rác trước header bị bỏ qua |
| E4 | có | có, với lệnh trong khung. Sau đó dò lại header ngay trong khung hỏng |
| LEN > 64 | ? | coi là header giả, bỏ qua im lặng rồi dò lại |
| Serial mode ASCII/trong suốt | có | không hỗ trợ (ghi → E5) |
| Work mode 0x03 (custom HID) | có | chạy như 0x00 |
| Cấu hình xuất xưởng | work/serial mode đọc ra 0x80 (theo chân) | đọc ra 0x00 (không có chân MODE/CFG) |

### GET_INFO của bridge (version 0x40)

| Byte | Ý nghĩa |
|---|---|
| 0 | `0x40` = ihc bridge v1.0. CH9329 thật trả 0x30–0x39. Driver host hiện in "unknown (0x40)" và vẫn chạy bình thường |
| 1 | `0x01` chỉ khi **mọi báo cáo chính** của profile tới được iPhone. Báo cáo chính là bàn phím (nếu có) và chuột tương đối (hoặc con trỏ tuyệt đối nếu không có chuột tương đối). BLE: đã kết nối, đã mã hoá, iPhone đã subscribe các báo cáo đó, và bộ đệm không bị tắc. USB: đã được cấu hình, bus không suspend, endpoint không kẹt |
| 2 | LED bàn phím do iPhone ghi (bit0 Num, bit1 Caps, bit2 Scroll) |
| 3 | đầu ra: `0x01` BLE, `0x02` USB, `0x7F` bộ mô phỏng host |
| 4 | các collection HID đang có: bit0 bàn phím, bit1 chuột tương đối, bit2 consumer, bit3 system, bit4 con trỏ tuyệt đối |
| 5 | tính năng: bit0 có `SEND_MS_REL_RUN`, bit1 lệnh đó nhận interval theo 0,25 ms (bit 7 của byte cờ), bit2 lệnh đó trả E7 khi có báo cáo trễ. Bridge hiện tại báo `0x07` (không có đồng hồ thì `0x00`) |
| 6–7 | **chu kỳ báo cáo**: iPhone thực sự nhận báo cáo HID bao lâu một lần. Số 16 bit **little-endian** (byte 6 là byte thấp), đơn vị **0,25 ms**. BLE: connection interval hiện tại (`60` = 15 ms). USB: chu kỳ poll của endpoint IN (`bInterval` 1 ms → `4`). `0` = chưa biết / chưa kết nối |

Byte 1 = 0 không có nghĩa là mọi lệnh đều hỏng. Mỗi lệnh HID tự nhận câu trả lời thật của riêng nó. Ví dụ bàn phím
vẫn chạy khi iPhone chưa subscribe báo cáo chuột.

**Byte 6–7 dùng để làm gì:** iOS tăng tốc chuột tương đối theo tốc độ. Cùng một chuỗi báo cáo chỉ cho cùng quãng
đường khi các báo cáo tới iPhone cách nhau đều. Qua BLE, báo cáo chỉ lên sóng ở các connection event. Host gửi mỗi
20 ms mà interval là 15 ms thì iPhone nhận lúc cách 15 ms, lúc cách 30 ms, và quãng đường không lặp lại được. Host nên
canh nhịp con trỏ theo **bội số** của chu kỳ này, và hỏi lại GET_INFO trước khi canh nhịp, vì iPhone đổi được
interval bất cứ lúc nào.

- **BLE:** đọc từ connection descriptor của NimBLE (`ble_gap_conn_find()`) khi kết nối và mỗi khi thông số kết nối
  đổi (`BLE_GAP_EVENT_CONN_UPDATE`). `conn_itvl` tính theo 1,25 ms nên giá trị = `conn_itvl × 5`. Về 0 khi mất kết
  nối. Có giá trị ngay khi kết nối, kể cả khi byte 1 còn là 0 (chưa mã hoá, chưa subscribe).
- **USB:** hằng số `bInterval` mà firmware tự khai báo cho mọi endpoint IN (1 ms, full speed) → `4`. Chỉ báo khi
  iPhone đã cấu hình thiết bị, ngược lại 0.
- **Bộ mô phỏng host:** mặc định `60` (15 ms), `0` khi chạy với `--not-ready`. Đổi bằng lệnh `period` trên stdin.

### Lệnh mở rộng `0x30 SEND_MS_REL_RUN`

```
57 AB ADDR 30 05 | dx(int8) dy(int8) count(1..255) interval(u8) flags(u8) | SUM
flags: bit 0-2 nút (bit0 trái, bit1 phải, bit2 giữa) | bit 3-6 dự trữ, phải = 0 | bit 7 = interval tính bằng 0,25 ms
```

- Bridge phát `count` báo cáo chuột tương đối `{nút, dx, dy, 0}`. Báo cáo thứ i đi lúc `t0 + i·interval`, **tự định
  giờ trên chip** theo đồng hồ micro giây. Nhờ vậy nhịp gửi không bị jitter của hệ điều hành host làm lệch, mà iOS
  tăng tốc con trỏ theo tốc độ, nên nhịp chính xác thì vị trí chính xác.
- **Đơn vị của `interval`:**
  - bit 7 = 0: mili giây nguyên, 0–255 ms. Đây là cách mã hoá ban đầu, host cũ không bị ảnh hưởng.
  - bit 7 = 1: **0,25 ms**, 0–63,75 ms. Cần cho link BLE 7,5 / 11,25 / 18,75 ms, vì nhịp phải là bội số của
    interval mà mili giây nguyên không biểu diễn được. Ví dụ 22,5 ms = `interval 90` + bit 7; 37,5 ms = `150` + bit 7.
  - Giới hạn `(count−1)·interval ≤ 2000 ms` tính theo **thời gian thật**, đơn vị nào cũng vậy.
- Run **chiếm `count` ô**: nó kết thúc lúc `t0 + count·interval`, nên các run gửi nối đuôi nhau tiếp tục đúng một
  lịch đều.
- **Chỉ một câu trả lời**, sau ô cuối:
  - `B0 00`: mọi báo cáo đã giao **và đúng giờ**: link nhận từng báo cáo không trễ quá ngưỡng sau ô của nó.
  - `F0 E7` (**mã riêng của bridge**): mọi báo cáo đã giao, nhưng **ít nhất một báo cáo trễ** hơn ngưỡng. Cả nước đi
    vẫn được phát hết. Nhưng iPhone thấy khoảng cách không đều, mà iOS tăng tốc theo tốc độ, nên quãng đường không
    còn chính xác: host không được coi điểm đến là chính xác (nên đo lại bằng hình ảnh).
  - `F0 E6`: dừng ở báo cáo đầu tiên không giao được. Các bước sau không gửi, và host nên coi vị trí con trỏ là chưa
    biết. E6 thắng E7.
  - `F0 E5`: count = 0, bit dự trữ 3–6 khác 0, hoặc `(count−1)·interval > 2000 ms`.
- **"Trễ" đo thế nào:** lấy thời điểm link **nhận** báo cáo trừ thời điểm của ô. Báo cáo đầu tiên cũng tính, ô của
  nó là t0.
  - BLE: NimBLE đã xếp notification cho connection event kế tiếp.
  - USB: báo cáo đã nằm trong endpoint, trước khi iPhone đọc. Việc chờ lần poll kế tiếp là lưới của chính link,
    không tính là trễ.
- **Ngưỡng:** Kconfig `BRIDGE_REL_RUN_LATE_US`, mặc định **2000 µs**, tức hai tick FreeRTOS 1 ms.
  - Một tick nhiễu lịch, hay một lần chờ bộ đệm BLE 1 ms, vẫn dưới ngưỡng. Báo cáo phải chờ link lâu hơn thì bị
    đánh dấu.
  - Vẫn nhỏ hơn nhiều so với interval BLE ngắn nhất mà host canh nhịp theo (7,5 ms).
  - Bộ mô phỏng host (`sim_bridge`) dùng 10 ms, vì Linux có thể ngủ quá vài ms khi máy bận.
- **Độ chính xác:** đồng hồ micro giây (esp_timer, 32 bit, quay vòng sau 71,6 phút; lõi xử lý được).
  - Phần ngủ vẫn theo tick 1 ms (`vTaskDelay`), nên chỉ đưa task tới 1–3 ms trước ô. Phần còn lại là vòng chờ bận
    trên esp_timer.
  - Báo cáo được giao cho link trong vài micro giây sau ô, trừ khi task đọc UART (ưu tiên cao hơn) hay một ngắt
    chạy đúng lúc đó (thường vài chục µs). **Chưa đo trên phần cứng.**
  - Sau đó link tự lượng tử hoá: BLE theo connection event, USB theo lần poll 1 ms.
- Báo cáo đi trễ (link chậm) được gửi ngay, không bị bỏ. Lịch là tuyệt đối nên không cộng dồn độ trễ.
- Trên BLE, báo cáo chỉ lên sóng theo từng connection interval (15 ms). Chọn `interval` là bội số của interval đó
  thì nhịp mới đều trên iPhone. Interval hiện tại đọc ở GET_INFO byte 6–7.
- GET_INFO byte 5 báo các phần này: bit0 lệnh, bit1 đơn vị 0,25 ms, bit2 mã E7.
- Driver host hiện tại:
  - chưa có hàm cho bit 7: dùng `transact_raw()`;
  - chưa biết tên E7, nhưng vẫn báo nó thành lỗi `HidStatusError` với `status == 0xE7` (e2e kiểm cả hai);
  - thời gian trả lời có thể tới 2 s, lâu hơn timeout 500 ms mặc định.
- Log thống kê mỗi phút có `runs N late M (max X us)`: số run, số run trả E7, độ trễ lớn nhất đã gặp.

### Profile (work mode) và bug phím tắt của iOS

| Work mode | Profile | Collection | PID USB mặc định | Tag (cả hai / chỉ tương đối / chỉ tuyệt đối) |
|---|---|---|---|---|
| 0x00, 0x03 | composite | bàn phím, consumer, system + con trỏ theo `BRIDGE_POINTERS` | **0x4028** (trước đây 0x4008) | `-RA` / `-R` / `-A` |
| 0x01 | chỉ bàn phím | bàn phím | 0x4004 | `-K` |
| 0x02 | chỉ chuột | con trỏ theo `BRIDGE_POINTERS` | 0x4002 | `-MRA` / `-MR` / `-MA` |

- Đã có báo cáo rằng khi AssistiveTouch bật và iOS thấy cả bàn phím lẫn chuột, phím tắt Cmd/Shift/Option đôi khi bị
  chuyển sang SpringBoard. Profile **chỉ bàn phím** loại bỏ mọi con trỏ.
- Cách đổi: `hidtest cfg set work_mode=0x01` (hoặc `SET_PARA_CFG`) rồi **`RESET`**. **Khác CH9329**, bridge áp dụng
  ngay khi `RESET` vì nó khởi động lại. Không cần rút nguồn.
- **USB:** mỗi profile có PID riêng, nên iPhone coi đó là thiết bị khác và đọc lại descriptor.
- **BLE:** bảng GATT giữ nguyên; Report Map và giá trị Report Reference đổi theo profile. Bridge báo *Service
  Changed* cho iPhone đã ghép. iOS có đọc lại Report Map hay không là **điểm phải kiểm chứng**. Cho tới lúc đó, coi
  như phải Forget + xoá bond + ghép lại (xem [Danh tính descriptor](#danh-tính-descriptor-ios-cache-descriptor)).
- `BRIDGE_POINTERS` (Kconfig) chọn con trỏ nào có mặt: cả hai (mặc định), chỉ tương đối, hoặc chỉ tuyệt đối.
  - Aiden điều khiển iPhone bằng **chuột tuyệt đối qua USB**, iOS cho con trỏ trượt tới đích (họ chờ ~80 ms trước khi
    click). glassbox cũng chạy được trên iPhone 17 Pro Max, iOS 26.5.
  - Qua **BLE**, con trỏ tuyệt đối **chưa được chứng minh**.
  - Nếu iOS xử lý sai khi có hai con trỏ cùng lúc, build lại với một loại.

### Danh tính descriptor (iOS cache descriptor)

iOS lưu descriptor HID vào cache: qua USB theo danh tính thiết bị (VID, PID, số serial), qua BLE theo bond, suốt
đời bond. Một iPhone đã thấy một bản build có thể áp descriptor cũ cho bản build khác cùng danh tính, và khi đó kết
quả đo không đáng tin (mirrordeck đã rút sai vài kết luận vì vậy).

- **Tên** (tên Bluetooth, chuỗi USB product) và **số serial** luôn kết thúc bằng một **tag** chỉ tập collection mà
  iPhone thấy (bảng ở trên). Ví dụ `HID Bridge 1A2B-RA`, serial `A0B1C2D3E4F5-A`.
  - Hai bản build có cùng tag khi và chỉ khi iPhone thấy cùng tập collection (unit test kiểm mọi cặp).
  - Chuỗi đặt bằng `SET_USB_STRING` (product, serial) cũng được nối tag. Tên dài quá 29 ký tự thì phần trước bị
    cắt, tag thì không bao giờ.
- **USB:** tag trong serial tách các biến thể con trỏ dùng chung một PID. Ví dụ T1 run B (`-A`) và run D (`-RA`)
  đều là composite, PID 0x4028. Đổi thứ tự interface hay descriptor USB thì phải đổi cả PID (bảng dưới).
- **BLE:** cache gắn với bond, không gắn với tên. Tên mới chỉ giúp **nhìn thấy** một bond cũ (iOS có thể giữ tên
  cũ tới khi ghép lại). **Sau mọi thay đổi descriptor qua BLE** (đổi `BRIDGE_POINTERS`, đổi work mode, nạp firmware
  làm đổi Report Map, như bản này với profile một collection):
  1. Trên iPhone: Settings > Bluetooth > (i) cạnh bridge > **Forget This Device**.
  2. Trên ESP32, khi firmware **đang chạy**: giữ **BOOT 3 giây**. Mọi bond bị xoá; log ghi `all bonds deleted`.
     Không giữ BOOT lúc cắm điện hay lúc nhấn RST: đó là chế độ nạp của ROM.
  3. Ghép lại: Settings > Bluetooth > *Other Devices*.
  - Firmware tự báo *Service Changed* cho iPhone đã ghép khi Report Map hoặc Report Reference khác lần khởi động
    trước (so dấu vân tay FNV-1a lưu trong NVS, khoá `gatt_map`), và in cảnh báo lên log. iOS có đọc lại hay không
    chưa được kiểm chứng, nên vẫn làm đủ ba bước.

### USB: thứ tự interface và PID

| Profile | Interface, theo thứ tự | PID | So với bản trước |
|---|---|---|---|
| composite | bàn phím → extras (consumer + system, report id 3/4) → chuột tương đối → con trỏ tuyệt đối | **0x4028** | thứ tự mới |
| chỉ bàn phím | bàn phím | 0x4004 | không đổi |
| chỉ chuột | chuột tương đối → con trỏ tuyệt đối | 0x4002 | không đổi |
| *(cũ)* composite | bàn phím → chuột tương đối → con trỏ tuyệt đối → extras | 0x4008 | không dùng nữa: build với PID này bị từ chối (`#error`) |

- Chỉ interface có collection trong bản build mới xuất hiện. Ví dụ composite chỉ tuyệt đối (T1 run B): bàn phím →
  extras → con trỏ tuyệt đối.
- **Vì sao con trỏ ở cuối:** Aiden ghi nhận bàn phím ảo của iOS chỉ quay lại khoảng 80% số lần sau khi enumerate
  lại nếu con trỏ đứng ngay sau bàn phím, và 10/10 lần với thứ tự bàn phím → consumer control → con trỏ. Điều này
  quan trọng cho T5 và việc đổi sang profile chỉ bàn phím, không ảnh hưởng T1.
- Mọi PID trên đều là PID phát triển trong dải 0x303A:0x40xx. Phải xin PID riêng trước khi triển khai.
- Đổi thứ tự interface hay descriptor USB ⇒ **PID mới** (Kconfig `BRIDGE_PID_*`), vì iOS có thể dùng lại
  descriptor đã cache cho một danh tính quen.

### BLE: Report ID trong Report Map

- Profile có **nhiều collection** (composite; chỉ chuột với cả hai con trỏ): mỗi collection có Report ID (bàn phím
  1, chuột 2, consumer 3, system 4, con trỏ tuyệt đối 5). Report Reference của từng đặc tính ghi đúng id đó.
- Profile có **một collection** (chỉ bàn phím; chỉ chuột với chỉ tương đối hoặc chỉ tuyệt đối): Report Map **không
  có Report ID**, Report Reference của collection đó ghi **id 0**.
  - Lý do: trên iOS 13.2.3, map một collection có khai Report ID thì iOS bỏ qua notification; bỏ Report ID thì
    chạy. Map nhiều collection, mỗi collection có id, thì chạy (Apple Developer Forums thread 126757).
  - Unit test ghim từng byte của các map này, có và không có Report ID.
- Đặc tính của collection không có trong profile vẫn giữ id riêng (không có trong map, không bao giờ được notify),
  nên không có hai đặc tính cùng id và cùng loại.
- So với bản firmware trước, Report Map của các profile một collection đã đổi: **Forget + xoá bond + ghép lại**.
- T9 nên so sánh cả hai cách ([research §7.4](../../docs/research/absolute-pointer.md)). Bản này chỉ build được cách không có Report ID; muốn thử cách có id
  thì dùng bản firmware trước (commit `49884dd`).

### Không bao giờ tự phát báo cáo tuyệt đối (0,0)

- Mỗi báo cáo tuyệt đối là một **vị trí**. X = Y = 0 đưa con trỏ iPhone lên góc trên bên trái (mirrordeck, PiKVM).
- Giá trị đọc được của báo cáo tuyệt đối (đặc tính BLE, `GET_REPORT` USB) là **giữa màn hình (16384, 16384),
  không nút, bánh xe 0** trước báo cáo đầu tiên và sau mỗi lần kết nối BLE / cấu hình USB lại. Sau đó nó là báo
  cáo cuối cùng đã gửi.
- Các báo cáo khác (bàn phím, chuột tương đối, consumer, system) về 0 như trước: không phím nào bị giữ.
- Firmware không gửi báo cáo "giữ kết nối" hay "trung tính" nào. (0,0) chỉ đi ra khi host tự gửi `0x04` với X = Y
  = 0, như T1 check 5.

## Độ tin cậy: không bao giờ mất lệnh âm thầm

Nguyên tắc: **`00` chỉ được trả khi báo cáo đã được giao** (với `SEND_MS_REL_RUN`: và đúng giờ, nếu không thì E7).
Mọi trường hợp khác host đều thấy, qua E6, E5, E1, E4 hoặc qua việc không có trả lời trong 500 ms. Không có đường nào để một báo cáo bị bỏ mà vẫn nhận `00`.

**Thứ tự:** một task duy nhất xử lý khung theo đúng thứ tự nhận. Báo cáo sau chỉ được xếp hàng khi báo cáo trước đã
có kết quả. Chuột mang trạng thái nút nên **mỗi báo cáo đều được gửi riêng**, không bao giờ gộp.

**BLE:**

- `00` = `ble_gatts_notify_custom()` trả 0, tức NimBLE đã nhận notification vào hàng đợi của đúng kết nối đó. Điều
  kiện: iPhone đã kết nối, liên kết đã mã hoá, iPhone đã subscribe **đúng báo cáo đó**, và báo cáo có trong Report
  Map hiện tại. Sau đó tầng link tự gửi lại cho tới khi iPhone xác nhận hoặc liên kết đứt.
- **E6 ngay** khi: chưa kết nối, chưa mã hoá, chưa subscribe báo cáo đó, báo cáo không có trong profile, hoặc NimBLE
  báo lỗi khác ENOMEM.
- **Hết bộ đệm:** task bridge (không phải task NimBLE) thử lại mỗi 1 ms, tối đa `BRIDGE_NOTIFY_WAIT_MS` (100 ms), rồi
  mới trả **E6**. Host gửi lại được.
- Báo cáo chỉ được dùng mbuf khi stack còn trên 12 khối trống. Nhờ vậy loạt báo cáo dồn dập không làm stack hết bộ đệm
  cho việc trả lời ATT hay signalling của chính nó.
- **Tắc:** nếu bộ đệm đầy suốt thời gian chờ, bridge coi liên kết là tắc. GET_INFO báo 0 cho tới khi hàng đợi trôi trở
  lại.

**USB:**

- `00` = `tud_hid_n_report()` nhận báo cáo **và** `tud_hid_report_complete_cb()` xác nhận máy chủ USB đã đọc nó, trong
  `BRIDGE_USB_CONFIRM_WAIT_MS` (50 ms). Với chu kỳ poll 1 ms, việc này bình thường mất 1–2 ms.
- **E6** khi:
  - chưa được cấu hình;
  - endpoint vẫn bận sau 100 ms;
  - bus bị suspend mà remote wakeup không đánh thức được;
  - máy chủ không đọc trong thời hạn;
  - hoặc cấu hình USB bị đặt lại (bus reset, enumerate lại) trong lúc báo cáo đang chờ: stack đã bỏ nó.
- **Mỗi xác nhận khớp đúng báo cáo của nó** (`components/ch9329_proto/src/hid_confirm.c`).
  - TinyUSB đánh dấu endpoint rảnh *trước* khi gọi callback hoàn tất, và callback không cho biết đó là báo cáo nào.
    Bản trước có kẽ hở: một báo cáo không được xác nhận kịp (E6) mà sau đó mới được đọc có thể làm báo cáo *kế
    tiếp* nhận `00` khi iPhone chưa đọc nó.
  - Nay mỗi báo cáo lấy một vé (số thứ tự) trước khi vào endpoint. Endpoint interrupt chỉ có một transfer mỗi lúc
    và hoàn tất theo thứ tự, nên callback thứ n thuộc về báo cáo thứ n.
  - Khi cấu hình bị đặt lại, báo cáo đang chờ nhận E6 ngay và bộ đếm được cân lại, để báo cáo sau không phải chờ một
    callback sẽ không bao giờ tới.
  - Có unit test cho các tình huống tranh chấp và một mô hình ngẫu nhiên của endpoint TinyUSB.
- **Lưu ý riêng cho E6 do quá hạn xác nhận:** báo cáo vẫn nằm trong endpoint và **có thể** được đọc muộn nếu iPhone
  poll lại. Nếu host gửi lại một bước chuột tương đối, bước đó có thể bị tính hai lần. Sau E6, host nên coi vị trí con
  trỏ là chưa biết (vòng kín bằng hình ảnh đã làm việc này).

**UART phía host:**

- Byte được đóng dấu thời gian **lúc đến**, trong một task đọc riêng. Vì vậy thời gian bridge chờ bộ đệm BLE không
  bao giờ bị hiểu nhầm thành khoảng lặng trên đường truyền, và không sinh E1 giả.
- Tràn bộ đệm UART (host bắn quá nhanh ở chế độ không chờ ack) thì khung bị mất không có trả lời. Host thấy timeout
  (`HidTimeout`) hoặc `lost_acks` ở chế độ `wait_ack=False`. Không có gì bị mất mà vẫn nhận `00`.

**Những gì vẫn có thể mất dù đã nhận `00` (BLE):**

- iPhone biến mất **không báo trước**: ra khỏi tầm, treo, hết pin. Liên kết vẫn được coi là còn cho tới hết
  *supervision timeout*.
- Hướng dẫn hiện hành của Apple yêu cầu mức này **6–18 s**; bridge xin **6 s**.
- Trong khoảng đó, các báo cáo đã vào hàng đợi (tối đa vài chục, giới hạn bởi bộ đệm của stack) sẽ mất khi liên kết
  đứt. Khi chạy với nhịp bình thường (pacer 25 ms/báo cáo), hàng đợi thường chỉ có 0–2 báo cáo.
- Ngay khi hàng đợi đầy, các lệnh tiếp theo nhận **E6** sau 100 ms và GET_INFO báo 0, nên host phát hiện được trong
  khoảng 0,1–0,5 s chứ không phải 6 s.
- Khi iPhone chủ động ngắt (tắt Bluetooth, khởi động lại), bridge biết ngay.
- Với các phím đang giữ lúc mất liên kết, iOS tự nhả khi kết nối đóng.

## Thông số kết nối BLE (Apple Accessory Design Guidelines)

Đã đối chiếu với bản **"Accessory Design Guidelines for Apple Devices" ngày 2026-09-21**, chương *Bluetooth Low
Energy (BLE)*:

- **58.6 Connection Parameters:**
  - Peripheral Latency ≤ 30.
  - **Supervision Timeout 6–18 s.** Các bản cũ ghi 2–6 s.
  - **Interval Min ≥ 15 ms và là bội số của 15 ms.**
  - Interval Max ≥ Min + 15 ms, **hoặc Min = Max = 15 ms**.
  - Interval Max·(Latency+1) ≤ 6 s; Timeout > Interval Max·(Latency+1)·3.
  - *"Nếu BLE HID là một dịch vụ đang kết nối, một số thiết bị có thể chấp nhận interval xuống tới 11,25 ms."*
- **Bridge xin:** interval 15–15 ms, latency 0, timeout 6 s. Yêu cầu gửi sau khi mã hoá xong, thử lại 1 lần sau 5 s
  nếu chưa đạt. Đổi được trong menuconfig. Ví dụ `BRIDGE_CONN_ITVL_MIN=9` để thử 11,25 ms.
- **58.5 Quảng bá:** 20 ms trong 30 s đầu, sau đó 152,5 ms, một trong các giá trị Apple khuyên dùng.
- **58.3/58.4:** chỉ dùng `ADV_IND`. Gói quảng bá có Flags, TX Power, tên (không có `:` hay `;`) và dịch vụ HID 0x1812
  (58.13).
- **58.10:** phụ kiện không tự xin ghép; các đặc tính HID yêu cầu mã hoá, nên iPhone nhận *Insufficient
  Authentication* và tự bắt đầu ghép.
- **58.12:** Device Information Service không đưa vào gói quảng bá. Service Changed có mặt vì Report Map đổi theo work
  mode.

## Kiểm thử trên máy host (không cần phần cứng)

Chạy từ `firmware/esp32_ble_hid/`:

```bash
make -C test_host test     # unit test lõi C: gcc -Wall -Wextra -Werror (+ -Wconversion cho lõi),
                           # -fsanitize=address,undefined
make -C test_host e2e      # driver Python thật (ihc/hid/ch9329.py) ↔ lõi C chạy sau một pty
make -C test_host check    # cả hai
```

`e2e` dùng `/home/user/iphone-hid/.venv/bin/python` nếu có.

**Unit test** (`test_host/test_proto.c`, `test_hid_desc.c`, `test_hid_confirm.c`): 91 test, khoảng 116 000 phép kiểm,
tất cả qua. Bao gồm:

- **Khung mẫu trong tài liệu WCH:**
  - GET_INFO `57 AB 00 01 00 03`;
  - nhấn A, nhả, Shift+A;
  - mute `57 AB 00 03 04 02 04 00 00 0F`;
  - chuột tương đối nhấn trái và lùi 3 px;
  - chuột tuyệt đối `57 AB 00 04 07 02 00 40 01 15 02 00 67`.
- **Luồng byte:**
  - luồng gộp, tách từng byte, tách ngẫu nhiên;
  - rác trước header;
  - sai checksum → E4;
  - LEN > 64 → dò lại;
  - khung dở dang → E1, kể cả qua mốc tràn đồng hồ;
  - khung đứng sau header giả được cứu lại.
- **Địa chỉ và cấu hình:**
  - lọc địa chỉ; broadcast thực thi nhưng im lặng, kể cả khi lỗi;
  - vòng ghi/đọc cấu hình và kiểm tra giá trị;
  - dữ liệu flash hỏng → dùng mặc định;
  - chuỗi USB;
  - RESET trả lời trước rồi mới khởi động lại.
- **Work mode và độ tin cậy:**
  - chặn theo work mode;
  - **sink lỗi → E6, không bao giờ OK**;
  - sink trả giá trị lạ → E6;
  - từng loại báo cáo được giao hay không một cách độc lập;
  - **kiểm tra thuộc tính trên 3000 lệnh ngẫu nhiên** chia khúc ngẫu nhiên, lỗi ngẫu nhiên: `00` ⇔ báo cáo đã được giao,
    đúng thứ tự, mỗi lệnh đúng một trả lời.
- **Con trỏ tuyệt đối và GET_INFO:** đổi thang 0..4095 → 0..32767; X/Y ngoài dải → E5; build không có con trỏ tuyệt
  đối; các byte GET_INFO của bridge.
- **Chu kỳ báo cáo (GET_INFO byte 6–7):** mặc định 0; đặt giá trị; thứ tự little-endian (0x1234 → `34 12`); không phụ
  thuộc byte 1; về 0 khi mất kết nối; hơn 700 giá trị đọc lại đúng mà các byte khác không đổi.
- **REL_RUN (đồng hồ giả, micro giây):**
  - đúng lịch, kể cả nối đuôi và qua mốc quay vòng của đồng hồ 32 bit;
  - bước trễ gửi ngay chứ không bỏ, và run trễ trả **E7**;
  - ngưỡng trễ: đúng ngưỡng là `00`, hơn 1 µs là E7; ngưỡng đổi được; báo cáo đầu tiên trễ cũng tính;
  - trễ đo lúc link **nhận** báo cáo, không phải lúc USB xác nhận;
  - E6 thắng E7; dừng ở bước lỗi đầu tiên;
  - đơn vị 0,25 ms (22,5 ms, 11,25 ms nối đuôi, 0), giới hạn 2000 ms theo thời gian thật, bit dự trữ → E5;
  - E3 khi không có đồng hồ.
- **Descriptor HID:** parser HID tự viết kiểm **mọi** tổ hợp collection, có và không có report id. Kiểm kích thước
  báo cáo, dải logic của trục, bảng usage media, và việc từ chối khi bộ đệm không đủ.
- **Report Map BLE:** mọi tổ hợp collection: có Report ID khi và chỉ khi có nhiều collection; Report Reference
  khớp map; không hai đặc tính nào trùng id. Ghim từng byte của map chỉ bàn phím, chỉ tuyệt đối, chỉ tương đối, có
  và không có Report ID.
- **Giá trị nghỉ:** con trỏ tuyệt đối là (16384, 16384) không nút, không bao giờ (0,0); các báo cáo khác bằng 0.
- **Tag danh tính:** cùng tag khi và chỉ khi cùng tập collection; chỉ chữ in hoa; nối vào tên/serial, cắt phần
  trước chứ không cắt tag.
- **Xác nhận USB (`hid_confirm`):** xác nhận muộn của báo cáo cũ không bao giờ tính cho báo cáo mới; xác nhận tới
  trước khi lệnh xếp hàng trả về; huỷ vé; reset cấu hình; quay vòng bộ đếm; mô hình ngẫu nhiên 20 000 báo cáo của
  endpoint TinyUSB (busy xoá trước callback, bỏ cuộc E6, reset).
- Fuzz 20 000 luồng ngẫu nhiên.
- Mọi khung trả lời trong mọi test đều qua một bộ kiểm header/LEN/checksum độc lập.

**E2E** (`test_host/e2e_host_driver.py` + `test_host/sim_bridge.c`): 120 phép kiểm, tất cả qua.

- `sim_bridge` chạy lõi C sau một pseudo-terminal.
- Script dùng `from ihc.hid.ch9329 import CH9329Backend` và `ihc.hid.scan.probe` **không sửa đổi** để chạy:
  - info, bàn phím, media, ACPI, chuột tương đối và tuyệt đối;
  - đọc/ghi cấu hình và bị từ chối;
  - chuyển sang profile chỉ bàn phím bằng RESET;
  - E4, E1, E3, rác trước header;
  - mất liên kết giữa chừng → E6, không giao gì;
  - bộ đệm đầy một lần → E6 rồi gửi lại được;
  - báo cáo chưa subscribe → chỉ loại đó E6;
  - chờ bộ đệm 120 ms vẫn trong timeout 500 ms;
  - REL_RUN đúng số bước, đúng thời gian, dừng ở bước lỗi thứ 3;
  - REL_RUN theo 0,25 ms (12 bước × 22,5 ms), bit dự trữ → E5;
  - REL_RUN với link chậm (`delay 30`, nhịp 15 ms): đủ mọi bước nhưng trả **E7**; driver chưa sửa báo lỗi status
    0xE7; hết chậm thì lại `00`;
  - loạt 200 lệnh không chờ ack có 7 lỗi xen giữa → đúng 7 E6 tới host, 193 báo cáo giao đúng thứ tự;
  - địa chỉ 0x05/0x06/broadcast;
  - chu kỳ báo cáo, đọc thẳng từ payload thô của GET_INFO (byte 6–7): mặc định 15 ms, đổi sang 30 ms / 1 ms / 0x1234,
    về 0 khi không có iPhone.
- Phía mô phỏng BLE được điều khiển qua stdin của `sim_bridge` (`ready`, `reject`, `fail`, `failat`, `delay`, `leds`,
  `period`).

## Phải kiểm chứng trên phần cứng

### T1: iPhone có theo con trỏ tuyệt đối qua USB không (phần của firmware)

Theo [docs/phase0-checklist.md](../../docs/phase0-checklist.md) (T1) và
[research §7.2](../../docs/research/absolute-pointer.md). Chạy theo thứ tự A → B → C → D, trên iOS 26.x rồi làm lại
trên iOS 27.0. **Luôn chạy B, kể cả khi A đạt.** A và C dùng CH9329; B và D dùng firmware này, bản USB:

| Run | HID | `SDKCONFIG_DEFAULTS` | Interface USB (theo thứ tự) | Tag / PID | GET_INFO byte 4 | Để biết |
|---|---|---|---|---|---|---|
| A | CH9329 work mode 0x00, lệnh `0x04` | – | – | – | – | rẻ nhất, có ngay |
| **B** | ESP32-S3 USB, **chỉ con trỏ tuyệt đối** | `sdkconfig.defaults;sdkconfig.defaults.usb;sdkconfig.defaults.abs_only` | bàn phím → extras → con trỏ tuyệt đối (interface riêng, không report id, 0..32767) | `-A` / 0x4028 | `0x1D` | cấu hình tham chiếu, giống Aiden và gần glassbox. B đạt mà A không thì lỗi ở descriptor của CH9329, không phải iOS |
| C | CH9329 work mode 0x02 (chỉ chuột), rút/cắm lại | – | – | – | – | tách ảnh hưởng của bàn phím gộp |
| **D** | ESP32-S3 USB, **tương đối + tuyệt đối** (mặc định) | `sdkconfig.defaults;sdkconfig.defaults.usb` | bàn phím → extras → chuột tương đối → con trỏ tuyệt đối | `-RA` / 0x4028 | `0x1F` | chuột tương đối có cùng tồn tại được không (cần cho chế độ dự phòng và bánh xe) |

- Lệnh build: [Chọn con trỏ lúc build](#chọn-con-trỏ-lúc-build-bridge_pointers). Nạp B rồi D trên cùng một bo là
  được: serial khác tag (`…-A`, `…-RA`), nên iOS không dùng lại descriptor của lần trước (check 8).
- Kiểm tra đúng bản đang chạy: GET_INFO byte 4 (`hidtest --port … info`), `0x1D` = bàn phím, consumer, system,
  con trỏ tuyệt đối; `0x1F` = thêm chuột tương đối.

Các kiểm tra cho mỗi run (trang hiệu chỉnh ghi `pointerdown`/`click` với `clientX/Y` và `pointerType`):

1. Con trỏ là **chấm tròn** (AssistiveTouch). Mũi tên nghĩa là thiết bị bị nhận khác hoặc AssistiveTouch tắt: dừng
   lại, sửa rồi mới đo tiếp.
2. **Ánh xạ:** lưới 3×3 và 4 điểm gần góc; fit affine, ghi phần dư. Logical 0 và max rơi vào đâu (thanh trạng thái,
   vùng home indicator), có bị kẹp không. Host gửi lưới 0..4095 của lệnh `0x04`; firmware đổi sang 0..32767 (làm
   tròn, 4095 → 32767).
3. **Settle:** di chuyển, chờ {0, 30, 60, 100, 150, 250} ms rồi click, mỗi mức 10 lần. Ghi mức đầu tiên mà 100% rơi
   trong 2 pt. Mặc định cho tới khi đo được: 250 ms (glassbox).
4. **Click qua báo cáo tuyệt đối:** 50 tap tại một điểm, nhấn ≥ 60 ms, nhả một lần, rồi lại với nhả ba lần. Đếm số
   lần kẹt (trang hiện đang kéo). Nhấn và nhả phải lặp lại đúng X/Y hiện tại (host đã làm).
5. **Báo cáo (0,0):** gửi `abs 0 0` không nút, con trỏ phải về góc trên trái (tắt Hot Corners). Đây là lần duy
   nhất có (0,0): firmware không bao giờ tự phát nó, và giá trị đọc được lúc mới kết nối là giữa màn hình.
6. **Tracking Speed** ở mức thấp nhất và cao nhất: lặp lại 5 điểm, mong không đổi. Ghi lại dù kết quả thế nào.
7. **Trộn (chỉ D):** một bước tương đối ngay sau một bước tuyệt đối có tiếp tục từ vị trí tuyệt đối không (nếu có,
   dùng được chế độ lai).
8. **Cache descriptor:** giữa hai run khác descriptor trên cùng bo, serial hoặc PID phải khác. Firmware tự làm
   (tag trong serial; PID mới cho thứ tự interface mới). Không có điều này thì kết quả không đáng tin.
9. **AssistiveTouch > Devices:** mấy mục hiện ra (CH9329 một mục; D có hai interface con trỏ, có thể hai mục?). Tên
   hiện ra có tag (`HID Bridge XXXX-A`). Gán nút 2/3 → Home/App Switcher có áp dụng khi bấm qua báo cáo tuyệt đối
   không.
10. Tuỳ chọn: xoay ngang, thử 3 điểm (chỉ để tham khảo).

Đạt: như checklist (sai số ≤ 2 pt sau fit), cộng thêm: biết settle, 0 lần kẹt khi nhả ba lần, kết quả như nhau ở
hai mức Tracking Speed.

### T9: dòng Lightning qua BLE (phần của firmware)

- Làm check 1–6 của T1 qua BLE với bản BLE **chỉ tuyệt đối** (`sdkconfig.defaults;sdkconfig.defaults.abs_only`,
  tag `-A`) và bản mặc định (`-RA`). **Giữa hai bản: Forget trên iPhone, giữ BOOT 3 s, ghép lại.**
- Map một collection không có Report ID: work mode 0x02 trên bản chỉ tuyệt đối cho map chỉ có con trỏ tuyệt đối,
  Report Reference id 0 (tag `-MA`). Bản firmware trước (commit `49884dd`) cho cùng map có Report ID 5, để so sánh.
- Ghi connection interval (GET_INFO byte 6–7). Settle gồm thêm tới một interval (15–30 ms).

### Danh sách còn lại

1. **Ghép và kết nối lại** trên iPhone Lightning thật: Just Works, sau khi iPhone/ESP32 khởi động lại, sau khi tắt/bật
   Bluetooth, sau khi mất nguồn.
2. **iOS có subscribe mọi báo cáo input không**, kể cả chuột khi AssistiveTouch tắt. Nếu không, GET_INFO byte 1 sẽ là 0
   dù bàn phím vẫn chạy.
3. **Thông số kết nối iOS cấp thực tế:** log `connection parameters: … interval …`, và GET_INFO byte 6–7 phải khớp
   (`interval × 5`). Đo độ trễ bằng `hidtest bench`. Thử `BRIDGE_CONN_ITVL_MIN=9` (11,25 ms).
4. **Con trỏ** (chi tiết ở T1 và T9 ở trên):
   - chuột tương đối với AssistiveTouch;
   - **con trỏ tuyệt đối qua BLE (chưa ai chứng minh)** và qua USB;
   - hai con trỏ cùng lúc có ổn không; chọn `BRIDGE_POINTERS` theo kết quả;
   - con trỏ có trượt khi đổi vị trí tuyệt đối không, cần chờ bao lâu trước khi click (Aiden: ~80 ms, glassbox:
     ≥ 250 ms);
   - lúc kết nối hay enumerate lại, con trỏ không được nhảy lên góc trên trái (giá trị đọc được là giữa màn hình).
5. **Phím media và System Control** trên iOS: phím nào có tác dụng.
6. **Profile chỉ bàn phím:**
   - BLE: iOS có đọc lại Report Map sau *Service Changed* không, hay phải Forget + ghép lại;
   - BLE: map không có Report ID (Report Reference id 0) có chạy không;
   - USB: đổi PID có đủ không; bàn phím ảo có quay lại sau mỗi lần enumerate lại không (thứ tự interface mới, T5);
   - bug phím tắt có biến mất không.
7. **Khi iPhone khoá màn hình/ngủ:** BLE có giữ kết nối không; USB có suspend bus và remote wakeup có chạy không. Nếu
   không, lệnh HID nhận E6.
8. **Phát hiện tắc:** mang iPhone ra khỏi tầm, xem sau bao lâu lệnh nhận E6 và GET_INFO về 0.
9. **Reset của DevKit:** mở cổng serial có làm board tự reset qua DTR/RTS không. Nếu có, liên kết BLE bị ngắt mỗi lần
   host mở cổng; dùng adapter USB-UART không nối DTR/RTS.
10. **Chữ của ROM khi khởi động:** ROM in ở 115200 baud ra UART0 sau mỗi lần reset. Parser của host bỏ qua rác, nhưng
    nên xác nhận. Chỉ tắt được bằng eFuse, bridge không đụng tới.
11. **REL_RUN:** độ chính xác nhịp trên iPhone. Trên BLE, nhịp bị lượng tử hoá theo connection interval.
    - Bao nhiêu run trả E7 khi dùng bình thường (log `runs … late … (max … us)`); ngưỡng 2 ms có hợp lý không.
    - Nhịp theo 0,25 ms (ví dụ 22,5 ms trên link 7,5 ms) có đều hơn nhịp mili giây nguyên không.
12. **USB:**
    - cấp nguồn khi cắm cả hai cổng của DevKit;
    - iPhone/hub nhận thiết bị 100 mA;
    - VID/PID phát triển 0x303A:0x40xx (0x4028, 0x4004, 0x4002): xin PID riêng trước khi triển khai.
13. **Thông lượng:** loạt lệnh không chờ ack ở 115200 baud, xem có tràn bộ đệm UART không (log thống kê mỗi phút).
14. **Xoá bond bằng BOOT:** giữ 3 s khi đang chạy thì log ghi `all bonds deleted` và iPhone (sau Forget) ghép lại
    được; giữ BOOT lúc cắm điện thì bo vào chế độ nạp của ROM (đúng như mong đợi, không phải lỗi).

## Cấu trúc thư mục

```
components/ch9329_proto/   lõi giao thức C thuần (không phụ thuộc ESP-IDF): parser, dispatcher, cấu hình,
                           descriptor HID và tag danh tính, khớp xác nhận USB (hid_confirm). Cùng mã nguồn
                           được build cho ESP32 và cho test host
main/
  app_main.c               khởi động, task bridge (lõi CH9329 ↔ HID link), nút BOOT, thống kê
  transport_uart.c         UART0: task đọc đóng dấu thời gian lúc byte đến
  hid_link.h               giao diện chung cho hai đầu ra
  ble_hid.c                HID over GATT trên NimBLE (BRIDGE_OUTPUT_BLE)
  usb_hid.c                HID USB trên TinyUSB (BRIDGE_OUTPUT_USB)
  persist_nvs.c            lưu cấu hình 50 byte + chuỗi trong NVS
  bridge_config.h          bố trí task, kiểm tra Kconfig lúc biên dịch
  Kconfig.projbuild        menu "CH9329 HID bridge"
  idf_component.yml        phụ thuộc espressif/esp_tinyusb
sdkconfig.defaults         biến thể BLE (console ra USB Serial/JTAG, NimBLE, bonding NVS, FreeRTOS 1000 Hz)
sdkconfig.defaults.usb     lớp thêm cho biến thể USB
sdkconfig.defaults.abs_only lớp thêm: chỉ con trỏ tuyệt đối (T1 run B, T9)
partitions.csv             NVS 64 KB, app 1,5 MB (flash 4 MB)
test_host/                 unit test, bộ mô phỏng pty, e2e với driver Python
```

## Nguồn tham khảo và giấy phép

- Cách dùng API tham khảo các ví dụ ESP-IDF (Apache-2.0/CC0): `bluetooth/nimble/bleprph`, `blehr`,
  `peripherals/uart/uart_events`, `peripherals/usb/device/tusb_hid`. Phần quyền truy cập đặc tính HID tham khảo dịch vụ
  HID của NimBLE trong ESP-IDF (Apache-2.0). Không chép mã.
- Descriptor HID viết theo HID 1.11 và HID Usage Tables. Cấu trúc con trỏ tuyệt đối (Mouse > Pointer > Physical,
  X/Y 16 bit 0..32767) là cấu trúc PiKVM (GPL) và Aiden (AGPL) dùng. Đã **đọc để hiểu**, không chép mã.
- Giao thức CH9329: tài liệu WCH "CH9329芯片串口通信协议" V1.0. Ghi chú đối chiếu ở `docs/ch9329-protocol.md`.
