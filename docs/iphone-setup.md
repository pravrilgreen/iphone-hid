# Cài đặt iPhone (làm tay một lần cho mỗi máy)

Chỉ dùng iPhone có cổng USB-C: iPhone 15 trở lên, trừ 16e và 17e (hai máy này không xuất hình qua USB-C).

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
   khi cắm HID lần đầu. Với hộp Orange Pi, tên USB của thiết bị là `ihc keyboard + mouse`. Chuột tương đối và chuột
   tuyệt đối là hai interface riêng, nên iOS có thể hiện nhiều mục: gán nút cho mọi mục hiện ra. Đổi profile
   gadget (serial USB khác) có thể làm iOS coi là thiết bị mới, khi đó phải gán lại.
3. **Settings > Accessibility > Pointer Control:**
   - **Automatically Hide Pointer: OFF.** Con trỏ tự ẩn thì không nhìn thấy được.
   - **Pointer Size:** tăng lên khoảng 2/3 thanh trượt.
   - **Color:** tuỳ chọn, chọn màu viền dễ nhìn để người vận hành thấy con trỏ trên hình (phần mềm không
     nhận diện hình ảnh nên màu không ảnh hưởng gì tới điều khiển).
   - **Increase Contrast: ON** nếu có.
   - **Tracking Speed:** chọn một mức, ghi lại, và **không đổi nữa**. Hiệu chỉnh con trỏ chỉ đúng với mức đã đo.
4. **Settings > Display & Brightness:**
   - **Auto-Lock: Never.** Bắt buộc. Máy ngủ thì cổng USB bị tắt (suspend), máy ngừng nhận lệnh chuột và phím, và
     hình qua USB-C cũng tắt.
   - Không có lựa chọn Never? Nguyên nhân là **Low Power Mode** đang bật (nó ép Auto-Lock về 30 giây), hoặc máy có
     profile quản lý (MDM) hay Screen Time đặt thời gian khoá.
   - Giảm độ sáng xuống mức thấp vừa đủ nhìn trên hình thu. Màn OLED sáng liên tục với hình tĩnh dễ bị lưu ảnh.
   - Display Zoom: **Default**.
5. **Settings > Privacy & Security > Wired Accessories** (iOS 26+):
   - dàn máy chuyên dụng chọn **Always Allow**, để bàn phím và chuột vẫn chạy nếu máy lỡ khoá hoặc khởi động lại
     (kém an toàn hơn: phụ kiện lạ cũng được nhận);
   - máy dùng chung với người chọn **Automatically Allow When Unlocked**, và chấp nhận là máy khoá quá 1 giờ thì phải
     có người mở khoá;
   - cắm thử và chấp nhận phụ kiện một lần trong lúc máy đang mở khoá.
   - Kiểm tra lại mục này sau mỗi lần cập nhật iOS (iOS 26.2 đã tự đổi nó về Always Allow).
6. **Passcode:** máy chuyên cho tự động hoá thì nên **tắt passcode**, nếu các app đang dùng cho phép. Không có
   passcode thì USB Restricted Mode và việc tự khởi động lại sau 72 giờ khoá không áp dụng, và box mở được màn khoá
   chỉ bằng nút Home. Phải giữ passcode thì bắt buộc chọn Always Allow ở mục 5.
7. **Settings > General > Keyboard > Hardware Keyboard:**
   - layout **U.S.**;
   - tắt Auto-Capitalization, Auto-Correction và phím tắt "." (hai lần dấu cách).
8. **Khoá xoay dọc (Portrait Orientation Lock): ON** trong Control Center. Hiệu chỉnh và toạ độ đều tính theo
   màn hình dọc.
9. **Safari:** để thanh địa chỉ **ở dưới** (mặc định từ iOS 15). Để ở trên thì lúc tìm trang hiệu chỉnh, một
   cú click có thể trúng thanh địa chỉ và bật bàn phím; hiệu chỉnh sẽ dừng an toàn nhưng phải chạy lại.

## Nên làm

- **Tắt thông báo** hoặc bật Focus để popup không che màn hình khi đang chạy automation.
- **Tắt tự cập nhật iOS**, tránh máy tự khởi động lại giữa chừng.
- **Bật Optimized/Limit charging** (Battery > Charging > giới hạn 80%). Máy cắm sạc 24/7 sẽ nhẹ pin hơn.
- **Tắt Low Power Mode** (nó ép Auto-Lock về 30 giây). Giữ máy luôn được sạc qua hub để iOS không tự bật lại
  chế độ này khi pin yếu.

## Kiểm tra nhanh sau khi cài

Cắm HID (hộp: cổng USB-A của hub → cáp USB-A → USB-C → cổng Type-C của board, gadget đã bật) và chạy trên board:

```bash
python3 tools/hidtest.py --gadget "info; move 200 0; move 0 200; home"
```

Với cáp CH9329 (phương án dự phòng), thay `--gadget` bằng `--port <cổng>`. Nếu server đang chạy như service, dừng
nó trước (`sudo systemctl stop ihc`) để hai tiến trình không cùng gửi lệnh, rồi bật lại sau khi kiểm tra.

Kết quả đúng:
- `info` báo `USB connected`;
- con trỏ tròn xuất hiện và đi sang phải rồi xuống dưới;
- `home` đưa về màn hình chính.
