# Thử nhanh: chuột và hình trên Orange Pi 5 Plus

Hai bài thử đầu tiên, không cài gì lên board, không cần internet:

1. **Chuột** (khoảng 10 phút): cắm iPhone vào thì gửi được sự kiện chuột. Chưa cần HDMI, chưa cần server.
2. **Hình** (khoảng 10 phút): board thu được màn hình iPhone qua cổng HDMI IN, rồi xem trực tiếp trên trình duyệt.

## Chuẩn bị

- **File cài đặt:** tải `ihc-box-<version>-linux-aarch64.run` ở
  [bản release mới nhất](https://github.com/pravrilgreen/iphone-hid/releases/latest), rồi chép vào Orange Pi (USB,
  hoặc `scp` qua mạng LAN).
- **iPhone:** bật **Settings > Accessibility > Touch > AssistiveTouch**. Không bật thì không có con trỏ.
- **Cắm dây: iPhone phải là host, Pi là thiết bị.** Với hub, thiết bị nào nhận **dây liền của hub** thì thiết bị
  đó là host, nên dây liền của hub cắm vào **iPhone**:

  ```
  iPhone 15 ─ dây USB-C liền của hub ─ HUB ─ cổng USB-A của hub ─ dây USB-A→USB-C (có dữ liệu) ─ Type-C USB3/DP của Pi
                                        └─ sạc PD cắm vào cổng USB-C cái trên thân hub
  ```

  - Cổng Type-C USB3/DP của Pi nằm cạnh 2 cổng USB 3, không phải cổng nguồn.
  - Không có hub: iPhone ─ adapter USB-C đực → USB-A cái (OTG) ─ dây USB-A→USB-C ─ Type-C USB3/DP của Pi.
  - **Sai chiều** là khi Pi làm host: dây liền của hub cắm vào Pi, hoặc cổng USB-A của Pi nối vào iPhone, hoặc dây
    C–C nối thẳng. Khi đó iPhone chỉ hỏi "Trust This Computer" và không bao giờ nhận chuột. Kiểm tra bằng
    `lsusb | grep -i apple`: thấy "Apple ... iPhone" là đang sai chiều; nối đúng thì lệnh này không in gì.

## Thử chuột

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

Con trỏ không nhúc nhích thì thử lại với hai cách xếp khác. `A` chỉ có chuột tuyệt đối, không có chuột tương đối.
`AR` có cả hai, chuột tuyệt đối đứng trước. Mỗi cách iPhone thấy là một thiết bị mới, nên sau mỗi lệnh `up` chờ
`ihc gadget status` báo `configured` rồi mới thử:

```bash
sudo ~/ihc/bin/ihc gadget up --replace --profile A --owner $USER
~/ihc/bin/ihc-hidtest --gadget "info; abstest"
sudo ~/ihc/bin/ihc gadget up --replace --profile AR --owner $USER
~/ihc/bin/ihc-hidtest --gadget "info; abstest"
sudo ~/ihc/bin/ihc gadget up --replace --owner $USER        # trả về mặc định (RA)
```

Không cách nào chạy cũng không sao: server mặc định dùng chuột tương đối, như bài thử trên. Ghi lại phiên bản iOS
(**Settings > General > About**) cùng kết quả.

Thử xong thì gỡ: `sudo ~/ihc/bin/ihc gadget down`. Log của `ihc-hidtest` nằm trong `~/ihc-test-logs/`.

### Nếu chuột không được

Dừng ở bước hỏng và gửi lại nguyên văn kết quả của bước đó.

| Bước | Hiện tượng | Nguyên nhân thường gặp |
|---|---|---|
| 1 | không in gì | image khoá cổng Type-C ở vai host |
| 2 | báo "already used by the gadget ..." | image đang chạy gadget riêng (thường là ADB) chiếm cổng; `ihc gadget status` cho biết tên |
| 3 | đứng ở `not attached` | nối ngược chiều (dây liền của hub phải cắm vào iPhone), dây không có dữ liệu, hoặc sai cổng Type-C. Gửi kết quả của `lsusb`, `cat /sys/class/typec/port*/data_role /sys/class/usb_role/*/role` và `sudo dmesg \| tail -40` (chạy ngay sau khi cắm dây) |
| 4 | báo `NOT connected` | iPhone đang khoá, hoặc chưa bấm Allow |
| 4 | báo connected nhưng con trỏ không nhúc nhích | gửi nguyên văn output |

## Thử hình qua HDMI IN

Giữ nguyên dây USB của bài chuột, cắm thêm một dây HDMI:

```
iPhone 15 ─ dây USB-C liền của hub ─ HUB ─ cổng USB-A của hub ─ dây USB-A→USB-C ─ Type-C USB3/DP của Pi   (chuột, như trên)
                                      ├─ cổng HDMI của hub ─ dây HDMI ─ cổng HDMI IN của Pi                (hình, mới)
                                      └─ sạc PD ─ cổng USB-C cái trên thân hub                              (nên có)
```

- Pi có 3 cổng HDMI: 2 cổng **ra** màn hình và 1 cổng **HDMI IN**. Chỉ cổng có chữ **HDMI IN** in bên cạnh mới thu
  được hình; cắm vào cổng ra thì không thấy gì.
- Hub phải xuất HDMI bằng DisplayPort Alt Mode (UGREEN Revodok 105 đúng loại này), không phải DisplayLink.
- Không phải bật gì trên iPhone: mở khoá là iPhone tự phản chiếu màn hình ra HDMI.
- Đọc `/dev/video*` cần quyền nhóm `video`. Nếu báo `Permission denied`: `sudo usermod -aG video $USER`, đăng xuất
  rồi đăng nhập lại.

```bash
~/ihc/bin/ihc-capture-check list                                           # 1. tìm cổng HDMI IN
~/ihc/bin/ihc-capture-check snapshot --device /dev/videoN                  # 2. chụp 1 khung hình
~/ihc/bin/ihc-capture-check probe --device /dev/videoN --seconds 10        # 3. đo tốc độ khung hình
```

1. Tìm dòng có chữ `hdmirx` (vd `/dev/video0  stream_hdmirx`); `/dev/videoN` của nó là cổng HDMI IN, dùng cho bước
   2 và 3. Dòng "the source sends" bên dưới cho biết tín hiệu: `1920x1080p60.00` hay `3840x2160p30.00` là có hình,
   `no signal` là chưa có (iPhone đang khoá, cắm nhầm cổng HDMI, hoặc hub không xuất hình).
2. Lưu ảnh vào `~/ihc-test-logs/snap-<giờ>.jpg`. Mở ảnh đó (trên màn hình của Pi, hoặc chép về máy), phải thấy đúng
   màn hình iPhone. Mỗi lần mở cổng, board báo cho iPhone là màn hình 1080p60, nên iPhone chuyển sang 1920×1080 sau
   vài giây (màn hình iPhone nháy một cái là bình thường).
3. In ra số khung hình mỗi giây và dung lượng mỗi khung. Bài thử đạt nếu được khoảng 20–30 fps ở 1920×1080. Thêm
   `--encoder builtin` (luôn có) hoặc `--encoder mpp` / `--encoder gst` (chỉ khi image có GStreamer) để so sánh.

**Xem trực tiếp và điều khiển trên trình duyệt:**

```bash
~/ihc/bin/ihc serve --auto --token test
```

Mở `http://<địa chỉ IP của Pi>:8000` (trên máy cùng mạng, hoặc trình duyệt ngay trên Pi: `http://localhost:8000`),
nhập token `test`. Phải thấy màn hình iPhone chạy trực tiếp; bấm vào hình là tap lên iPhone. Tap còn lệch là bình
thường: hiệu chỉnh là bài thử sau. Trước khi chạy, tắt `ihc-hidtest` và `ihc-capture-check` nếu còn đang chạy, để
chỉ một chương trình điều khiển iPhone. `Ctrl+C` để dừng.

### Nếu hình không được

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| `list` không có dòng nào chứa `hdmirx` (hoặc báo `no /dev/video* devices`) | image của board chưa bật HDMI IN. Từ bản 0.1.5, `list` tự in lý do: device tree tắt cổng, kernel thiếu driver, hay driver chưa nạp (dòng bắt đầu bằng `=>`). **Armbian** (kernel vendor 6.1) thuộc trường hợp đầu: driver có sẵn nhưng device tree tắt cổng. Bật bằng overlay có sẵn: thêm `rk3588-hdmirx` vào dòng `overlays=` trong `/boot/armbianEnv.txt` rồi khởi động lại (xem bên dưới). Trường hợp khác thì gửi lại toàn bộ kết quả của `list` cùng `sudo dmesg \| grep -i hdmirx` |
| `no signal` | mở khoá iPhone; kiểm tra dây HDMI cắm vào cổng **HDMI IN**; thử cắm lại hub vào iPhone. Cắm thử cổng HDMI của hub vào một màn hình bất kỳ: màn hình cũng không có hình thì lỗi ở hub hoặc dây |
| `Permission denied` | thêm nhóm `video` như trên, hoặc chạy lệnh với `sudo` |
| `snapshot` báo `delivers XXXX, which this reader cannot convert` | gửi nguyên văn dòng đó |
| ảnh chụp sai màu hoặc bị xé ngang | gửi ảnh chụp và kết quả của `list` |

### Bật HDMI IN trên Armbian

Kernel vendor 6.1 của Armbian có sẵn driver HDMI IN, nhưng device tree của Orange Pi 5 Plus để cổng này ở trạng thái
tắt. Armbian có sẵn overlay để bật:

```bash
ls /boot/dtb/rockchip/overlay/ | grep hdmirx              # phải thấy rk3588-hdmirx.dtbo
sudo cp /boot/armbianEnv.txt /boot/armbianEnv.txt.bak     # sao lưu trước khi sửa
sudo nano /boot/armbianEnv.txt
```

Tìm dòng `overlays=`. Nếu chưa có thì thêm dòng `overlays=rk3588-hdmirx`; nếu đã có thì thêm `rk3588-hdmirx` vào
cuối dòng đó, cách bằng dấu cách (chỉ giữ một dòng `overlays=`). Lưu (`Ctrl+O`, `Enter`, `Ctrl+X`) rồi
`sudo reboot`. Khởi động xong, `ls /sys/class/video4linux` phải có `video0`, và `ihc-capture-check list` phải có
dòng `stream_hdmirx`. Nếu overlay lỗi, board vẫn khởi động bình thường nhưng bỏ qua overlay; muốn trả lại như cũ thì
`sudo cp /boot/armbianEnv.txt.bak /boot/armbianEnv.txt`.

## Bước tiếp theo

Chuột và hình chạy rồi thì cài hộp đầy đủ và làm tiếp các bài thử còn lại: hiệu chỉnh, độ trễ, chạy bền.
Xem [gadget.md](gadget.md) và [phase0-checklist.md](phase0-checklist.md).
