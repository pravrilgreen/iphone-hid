# Cài đặt iPhone (làm tay một lần cho mỗi máy)

Tên menu có thể khác đôi chút giữa các bản iOS. Ghi lại phiên bản iOS và mọi khác biệt vào log test.

## Bắt buộc

1. **Settings > Accessibility > Touch > AssistiveTouch: ON.** Trên iPhone, con trỏ chuột chỉ hiện khi bật
   AssistiveTouch.
   - Tắt **Always Show Menu** nếu nút nổi che nội dung. Nếu iOS không cho tắt, kéo nút nổi vào mép màn hình
     và ghi lại vị trí.
2. **AssistiveTouch > Pointer Devices > Devices > [tên thiết bị] > Customize Additional Buttons:**
   - nút phải (Button 2) = **Home**;
   - nút giữa (Button 3) = **App Switcher**.

   Phần mềm dùng đúng hai gán này cho lệnh `home` và `app_switcher`. Thiết bị chỉ hiện trong danh sách sau
   khi cắm HID lần đầu.
3. **Settings > Accessibility > Pointer Control:**
   - **Automatically Hide Pointer: OFF.** Con trỏ tự ẩn thì không nhìn thấy được.
   - **Pointer Size:** tăng lên khoảng 2/3 thanh trượt.
   - **Color:** chọn màu viền dễ phân biệt, mặc định phần mềm tìm màu **xanh lá**. Đổi màu khác thì phải
     cấu hình `PointerStyle.border_rgb`.
   - **Increase Contrast: ON** nếu có.
   - **Tracking Speed:** chọn một mức, ghi lại, và **không đổi nữa**. Hiệu chỉnh con trỏ chỉ đúng với mức đã đo.
4. **Settings > Display & Brightness:**
   - **Auto-Lock: Never.** Máy khoá thì phụ kiện có dây ngừng hoạt động.
   - Display Zoom: **Default**.
5. **Settings > Privacy & Security > Wired Accessories** (iOS 26+):
   - chọn **Automatically Allow When Unlocked** hoặc **Ask for New Accessories**;
   - cắm thử và chấp nhận phụ kiện một lần trong lúc máy đang mở khoá.
6. **Settings > General > Keyboard > Hardware Keyboard:**
   - layout **U.S.**;
   - tắt Auto-Capitalization, Auto-Correction và phím tắt "." (hai lần dấu cách).

## Nên làm

- **Tắt thông báo** hoặc bật Focus để popup không che màn hình khi đang chạy automation.
- **Tắt tự cập nhật iOS**, tránh máy tự khởi động lại giữa chừng.
- **Bật Optimized/Limit charging** (iPhone 15+: Battery > Charging > giới hạn 80%). Máy cắm sạc 24/7 sẽ nhẹ pin
  hơn.
- **Tắt Low Power Mode** (nó ép Auto-Lock về 30 giây).
- **Dòng Lightning:** vào Settings > Bluetooth để ghép với ESP32 (xem `firmware/esp32_ble_hid/README.md`).

## Kiểm tra nhanh sau khi cài

Cắm HID và chạy:

```bash
python tools/hidtest.py --port <cổng> "info; move 200 0; move 0 200; home"
```

Kết quả đúng:
- `info` báo `USB connected`;
- con trỏ tròn xuất hiện và đi sang phải rồi xuống dưới;
- `home` đưa về màn hình chính.
