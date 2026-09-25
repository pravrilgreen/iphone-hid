# Thử nhanh: iPhone nhận chuột từ Orange Pi 5 Plus

Bài thử đầu tiên, khoảng 10 phút. Mục tiêu duy nhất: xác nhận **cắm iPhone vào thì gửi được sự kiện chuột**.
Không cài gì lên board, không cần HDMI, không cần server, không cần internet.

## Chuẩn bị

- **File cài đặt:** tải `ihc-box-<version>-linux-aarch64.run` ở
  [bản release mới nhất](https://github.com/pravrilgreen/iphone-hid/releases/latest), rồi chép vào Orange Pi (USB,
  hoặc `scp` qua mạng LAN).
- **iPhone:** bật **Settings > Accessibility > Touch > AssistiveTouch**. Không bật thì không có con trỏ.
- **Cắm dây:**
  - sạc → cổng PD của hub;
  - hub → iPhone;
  - **cổng USB-A của hub → dây USB-A sang USB-C (loại có truyền dữ liệu) → cổng Type-C USB3/DP của Orange Pi**. Đó
    là cổng cạnh 2 cổng USB 3, không phải cổng nguồn. Đầu USB-A báo cho board biết đầu kia là máy chủ, nên đừng
    dùng dây C–C.

## Chạy trên Orange Pi

```bash
./ihc-box-*-linux-aarch64.run --extract ~/ihc          # chỉ giải nén, không cài service
ls /sys/class/udc                                        # 1. phải in ra một tên, vd fc000000.usb
sudo ~/ihc/bin/ihc gadget up --owner $USER               # 2. board thành bàn phím + chuột
~/ihc/bin/ihc gadget status                              # 3. mở khoá iPhone (bấm Allow nếu hỏi): chờ "configured (phone connected)"
~/ihc/bin/ihc-hidtest --gadget "info; move 100 0; move 0 100; click"   # 4. gửi chuột
```

**Đạt:** chấm tròn xám trên iPhone đi sang phải, rồi đi xuống, rồi click. Vậy là đã xác nhận gửi được sự kiện chuột.

**Thêm 1 phút, nên làm: chuột tuyệt đối.**

```bash
~/ihc/bin/ihc-hidtest --gadget "abstest"
```

Lệnh này đưa con trỏ tới 4 góc rồi ra giữa màn hình, sau đó hỏi bạn thấy gì. Con trỏ nhảy đúng tới góc nghĩa là
iPhone theo được chuột tuyệt đối: mỗi tap nhanh và chính xác nhất.

Thử xong thì gỡ: `sudo ~/ihc/bin/ihc gadget down`. Log của `ihc-hidtest` nằm trong `~/ihc-test-logs/`.

## Nếu không được

Dừng ở bước hỏng và gửi lại nguyên văn kết quả của bước đó.

| Bước | Hiện tượng | Nguyên nhân thường gặp |
|---|---|---|
| 1 | không in gì | image khoá cổng Type-C ở vai host |
| 2 | báo "already used by the gadget ..." | image đang chạy gadget riêng (thường là ADB) chiếm cổng; `ihc gadget status` cho biết tên |
| 3 | đứng ở `not attached` | sai dây (phải là USB-A sang C, có truyền dữ liệu) hoặc sai cổng Type-C |
| 4 | báo `NOT connected` | iPhone đang khoá, hoặc chưa bấm Allow |
| 4 | báo connected nhưng con trỏ không nhúc nhích | gửi nguyên văn output |

## Bước tiếp theo

Chuột chạy rồi thì cài hộp đầy đủ và làm tiếp các bài thử còn lại: hình qua HDMI IN, hiệu chỉnh, độ trễ, chạy bền.
Xem [gadget.md](gadget.md) và [phase0-checklist.md](phase0-checklist.md).
