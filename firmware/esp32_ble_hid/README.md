# ESP32-S3 HID bridge: nói giao thức serial của CH9329

Firmware cho **ESP32-S3** (ESP-IDF v5.4). Host gửi khung lệnh CH9329 qua cổng serial. ESP32 biến chúng thành báo cáo
HID bàn phím / chuột / phím media rồi gửi sang iPhone qua **Bluetooth LE** (biến thể mặc định) hoặc qua **USB**
(biến thể TinyUSB).

Driver host `ihc/hid/ch9329.py` dùng được **nguyên trạng**: cùng khung `57 AB …`, cùng mã lệnh, cùng mã lỗi, cùng
timeout 500 ms.

> **Trạng thái:** cả hai biến thể **đã build (biên dịch + link) thành công, 0 warning** với ESP-IDF v5.4 trong môi
> trường phát triển. Firmware **chưa được nạp và chưa chạy trên phần cứng thật**. Mọi hành vi phía iPhone vẫn phải
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
  ```

- **Kết quả:** cả hai biến thể **0 warning, 0 error**. Code của bridge được biên dịch với `-Werror -Wshadow
  -Wsign-compare -Wunused-parameter`; lõi giao thức thêm `-Wextra -Wconversion -Wsign-conversion`.
  - Ảnh BLE **557 936 byte**. Kiểm tra bằng `nm`: có NimBLE, không có TinyUSB.
  - Ảnh USB **291 296 byte**. Có TinyUSB, không có Bluetooth.
  - Kích thước trên là của lần build lại sau khi thêm chu kỳ báo cáo (GET_INFO byte 6–7), cùng bộ công cụ và cùng
    lệnh, vẫn 0 warning.
  - Phân vùng app là 1,5 MB (`partitions.csv`).
- **Chưa làm:**
  - Chưa build qua đường Component Manager thật (registry bị chặn).
  - **Chưa nạp, chưa chạy trên phần cứng.**

## Ghép với iPhone (biến thể BLE)

1. Nạp firmware và cắm DevKit vào host. Log (cổng "USB") hiện `advertising as "HID Bridge XXXX"`. XXXX là 2 byte cuối
   địa chỉ Bluetooth.
2. Trên iPhone: **Settings > Bluetooth**. Chọn **HID Bridge XXXX** ở mục *Other Devices*. Nếu iOS hỏi, bấm
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
   - Giữ nút **BOOT** 3 giây (khi đang chạy) để xoá mọi bond trong ESP32.
   - Trên iPhone cũ: Settings > Bluetooth > (i) > **Forget This Device**.
   - Mỗi ESP32 chỉ nên ghép với một iPhone. Trong giá nhiều máy, ghép từng cặp một.
7. **Đổi tên Bluetooth:** `SET_USB_STRING` loại 1 (product), rồi `RESET`. iOS có thể nhớ tên cũ cho tới khi
   Forget + ghép lại.

## Tương thích với CH9329

| Lệnh / hành vi | CH9329 | Bridge |
|---|---|---|
| `0x01 GET_INFO` | version 0x30…, byte 1 = USB đã enumerate | version **0x40** ("ihc bridge v1.0"). Byte 1 = **1 chỉ khi báo cáo thật sự tới được iPhone** (xem dưới). Byte 2 = LED do iPhone ghi. Byte 3–7 = thông tin bridge |
| `0x02` bàn phím | có | có. **00 chỉ khi báo cáo đã giao** (xem Độ tin cậy) |
| `0x03` media: `02 b1 b2 b3` | có | consumer control, report id 3, 24 bit theo đúng thứ tự bit của CH9329 |
| `0x03` ACPI: `01 bits` | có | System Control (Power/Sleep/Wake), report id 4. iOS xử lý thế nào: chưa rõ |
| `0x04` chuột tuyệt đối | có | con trỏ tuyệt đối riêng (report id 5 / interface USB riêng). X/Y 0..4095 được đổi sang 0..32767. **X/Y > 4095 → E5**. Build không có con trỏ tuyệt đối → nhận rồi bỏ (hoặc E5, tuỳ `BRIDGE_ABS_MOUSE_REJECT`) |
| `0x05` chuột tương đối | có | có. Giá trị −128 được gửi thành −127, vì descriptor là −127..127 |
| `0x06` custom HID | có | **E3** |
| `0x08/0x09` cấu hình 50 byte | có | có, lưu NVS. Khi ghi, kiểm tra: work mode 0x00–0x03, serial mode phải 0x00, baud ∈ {9600, 19200, 38400, 57600, 115200}, địa chỉ ≠ 0xFF. Sai → E5 |
| Cấu hình có hiệu lực | lần cấp nguồn sau | lần **khởi động** sau, **kể cả sau `RESET`**, vì bridge khởi động lại thật |
| VID/PID trong cấu hình | dùng cho USB | chỉ lưu và đọc lại. USB và BLE PnP ID dùng VID/PID trong Kconfig, để không bao giờ giả danh ID của WCH |
| `0x0A/0x0B` chuỗi USB | chuỗi USB | vendor → nhà sản xuất (DIS/USB), product → **tên Bluetooth**/chuỗi USB product, serial → số serial. Byte cờ 36 được lưu nhưng bỏ qua: chuỗi khác rỗng là được dùng |
| `0x0C` mặc định | có | có (cấu hình + chuỗi). **Không** xoá bond Bluetooth |
| `0x0F` RESET | có | trả lời trước, rồi ngắt BLE/USB gọn gàng và khởi động lại |
| `0x30` | E3 | **SEND_MS_REL_RUN** (mở rộng, xem dưới) |
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
| 5 | tính năng: bit0 có `SEND_MS_REL_RUN` |
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
57 AB ADDR 30 05 | dx(int8) dy(int8) count(1..255) interval_ms(0..255) buttons(0..7) | SUM
```

- Bridge phát `count` báo cáo chuột tương đối `{buttons, dx, dy, 0}`. Báo cáo thứ i đi lúc `t0 + i·interval_ms`,
  **tự định giờ trên chip**. Nhờ vậy nhịp gửi không bị jitter của hệ điều hành host làm lệch, mà iOS tăng tốc con trỏ
  theo tốc độ, nên nhịp chính xác thì vị trí chính xác.
- **Chỉ một câu trả lời**, sau báo cáo cuối:
  - `B0 00`: mọi báo cáo đã giao.
  - `F0 E6`: dừng ở báo cáo đầu tiên không giao được. Các bước sau không gửi, và host nên coi vị trí con trỏ là chưa
    biết.
  - `F0 E5`: count = 0, buttons > 7, hoặc `(count−1)·interval > 2000 ms`.
- Báo cáo đi trễ (link chậm) được gửi ngay, không bị bỏ. Lịch là tuyệt đối nên không cộng dồn độ trễ.
- Trên BLE, báo cáo chỉ lên sóng theo từng connection interval (15 ms). Chọn `interval_ms` là bội số của interval đó
  thì nhịp mới đều trên iPhone. Interval hiện tại đọc ở GET_INFO byte 6–7.
- Driver hiện tại chưa có hàm cho lệnh này: dùng `transact_raw()`. Lưu ý thời gian trả lời có thể tới 2 s, lâu hơn
  timeout 500 ms mặc định.

### Profile (work mode) và bug phím tắt của iOS

| Work mode | Profile | Collection | PID USB mặc định |
|---|---|---|---|
| 0x00, 0x03 | composite | bàn phím, consumer, system + con trỏ theo `BRIDGE_POINTERS` | 0x4008 |
| 0x01 | chỉ bàn phím | bàn phím | 0x4004 |
| 0x02 | chỉ chuột | con trỏ theo `BRIDGE_POINTERS` | 0x4002 |

- Đã có báo cáo rằng khi AssistiveTouch bật và iOS thấy cả bàn phím lẫn chuột, phím tắt Cmd/Shift/Option đôi khi bị
  chuyển sang SpringBoard. Profile **chỉ bàn phím** loại bỏ mọi con trỏ.
- Cách đổi: `hidtest cfg set work_mode=0x01` (hoặc `SET_PARA_CFG`) rồi **`RESET`**. **Khác CH9329**, bridge áp dụng
  ngay khi `RESET` vì nó khởi động lại. Không cần rút nguồn.
- **USB:** mỗi profile có PID riêng, nên iPhone coi đó là thiết bị khác và đọc lại descriptor.
- **BLE:** bảng GATT giữ nguyên, chỉ Report Map đổi. Bridge báo *Service Changed* cho iPhone đã ghép. iOS có đọc lại
  Report Map hay không là **điểm phải kiểm chứng**. Nếu không, phải Forget + ghép lại.
- `BRIDGE_POINTERS` (Kconfig) chọn con trỏ nào có mặt: cả hai (mặc định), chỉ tương đối, hoặc chỉ tuyệt đối.
  - Aiden điều khiển iPhone bằng **chuột tuyệt đối qua USB**, iOS cho con trỏ trượt tới đích (họ chờ ~80 ms trước khi
    click).
  - Qua **BLE**, con trỏ tuyệt đối **chưa được chứng minh**.
  - Nếu iOS xử lý sai khi có hai con trỏ cùng lúc, build lại với một loại.

## Độ tin cậy: không bao giờ mất lệnh âm thầm

Nguyên tắc: **`00` chỉ được trả khi báo cáo đã được giao**. Mọi trường hợp khác host đều thấy, qua E6, E5, E1, E4 hoặc
qua việc không có trả lời trong 500 ms. Không có đường nào để một báo cáo bị bỏ mà vẫn nhận `00`.

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
  - hoặc máy chủ không đọc trong thời hạn.
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

**Unit test** (`test_host/test_proto.c`, `test_hid_desc.c`): 72 test, khoảng 58 000 phép kiểm, tất cả qua. Bao gồm:

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
- **REL_RUN (đồng hồ giả):** đúng lịch; bước trễ gửi ngay chứ không bỏ; dừng ở bước lỗi đầu tiên; kiểm tra tham số;
  E3 khi không có đồng hồ.
- **Descriptor HID:** parser HID tự viết kiểm **mọi** tổ hợp collection, có và không có report id. Kiểm kích thước
  báo cáo, dải logic của trục, bảng usage media, và việc từ chối khi bộ đệm không đủ.
- Fuzz 20 000 luồng ngẫu nhiên.
- Mọi khung trả lời trong mọi test đều qua một bộ kiểm header/LEN/checksum độc lập.

**E2E** (`test_host/e2e_host_driver.py` + `test_host/sim_bridge.c`): 107 phép kiểm, tất cả qua.

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
  - loạt 200 lệnh không chờ ack có 7 lỗi xen giữa → đúng 7 E6 tới host, 193 báo cáo giao đúng thứ tự;
  - địa chỉ 0x05/0x06/broadcast;
  - chu kỳ báo cáo, đọc thẳng từ payload thô của GET_INFO (byte 6–7): mặc định 15 ms, đổi sang 30 ms / 1 ms / 0x1234,
    về 0 khi không có iPhone.
- Phía mô phỏng BLE được điều khiển qua stdin của `sim_bridge` (`ready`, `reject`, `fail`, `failat`, `delay`, `leds`,
  `period`).

## Phải kiểm chứng trên phần cứng

1. **Ghép và kết nối lại** trên iPhone Lightning thật: Just Works, sau khi iPhone/ESP32 khởi động lại, sau khi tắt/bật
   Bluetooth, sau khi mất nguồn.
2. **iOS có subscribe mọi báo cáo input không**, kể cả chuột khi AssistiveTouch tắt. Nếu không, GET_INFO byte 1 sẽ là 0
   dù bàn phím vẫn chạy.
3. **Thông số kết nối iOS cấp thực tế:** log `connection parameters: … interval …`, và GET_INFO byte 6–7 phải khớp
   (`interval × 5`). Đo độ trễ bằng `hidtest bench`. Thử `BRIDGE_CONN_ITVL_MIN=9` (11,25 ms).
4. **Con trỏ:**
   - chuột tương đối với AssistiveTouch;
   - **con trỏ tuyệt đối qua BLE (chưa ai chứng minh)** và qua USB;
   - hai con trỏ cùng lúc có ổn không; chọn `BRIDGE_POINTERS` theo kết quả;
   - con trỏ có trượt khi đổi vị trí tuyệt đối không, cần chờ bao lâu trước khi click (Aiden: ~80 ms).
5. **Phím media và System Control** trên iOS: phím nào có tác dụng.
6. **Profile chỉ bàn phím:**
   - BLE: iOS có đọc lại Report Map sau *Service Changed* không, hay phải Forget + ghép lại;
   - USB: đổi PID có đủ không;
   - bug phím tắt có biến mất không.
7. **Khi iPhone khoá màn hình/ngủ:** BLE có giữ kết nối không; USB có suspend bus và remote wakeup có chạy không. Nếu
   không, lệnh HID nhận E6.
8. **Phát hiện tắc:** mang iPhone ra khỏi tầm, xem sau bao lâu lệnh nhận E6 và GET_INFO về 0.
9. **Reset của DevKit:** mở cổng serial có làm board tự reset qua DTR/RTS không. Nếu có, liên kết BLE bị ngắt mỗi lần
   host mở cổng; dùng adapter USB-UART không nối DTR/RTS.
10. **Chữ của ROM khi khởi động:** ROM in ở 115200 baud ra UART0 sau mỗi lần reset. Parser của host bỏ qua rác, nhưng
    nên xác nhận. Chỉ tắt được bằng eFuse, bridge không đụng tới.
11. **REL_RUN:** độ chính xác nhịp trên iPhone. Trên BLE, nhịp bị lượng tử hoá theo connection interval.
12. **USB:**
    - cấp nguồn khi cắm cả hai cổng của DevKit;
    - iPhone/hub nhận thiết bị 100 mA;
    - VID/PID phát triển 0x303A:0x40xx: xin PID riêng trước khi triển khai.
13. **Thông lượng:** loạt lệnh không chờ ack ở 115200 baud, xem có tràn bộ đệm UART không (log thống kê mỗi phút).

## Cấu trúc thư mục

```
components/ch9329_proto/   lõi giao thức C thuần (không phụ thuộc ESP-IDF): parser, dispatcher, cấu hình,
                           descriptor HID. Cùng mã nguồn được build cho ESP32 và cho test host
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
