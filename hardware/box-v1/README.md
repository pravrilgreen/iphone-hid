# Box v1 (phương án B): thiết kế mạch nguyên lý

- **Ngày:** 2026-09-26. **Trạng thái:** sơ đồ nguyên lý mức chân, chưa layout. Kiến trúc và linh kiện lấy từ
  [custom-box.md](../../docs/research/custom-box.md) §4–§9 và §13.
- **Phạm vi:** một PCB 4 lớp khoảng 90×60 mm, gồm LT7911D (USB-C DP Alt Mode → MIPI CSI-2), CH32V305RBT6 (HID
  USB High-Speed tới iPhone), module Luckfox Core1106 (RV1106G3), nguồn USB-C PD, RJ45 100M, USB-C tới PC, header
  debug, LED trạng thái và nút.
- **Nhãn** (như custom-box.md): **[Chắc]** = đọc từ nguồn gốc (datasheet, sơ đồ của hãng, mã nguồn). **[Có thể]** =
  lời hãng, nguồn gián tiếp (trích đoạn tìm kiếm, thư viện KiCad), hoặc giá trị tính toán cần đối chiếu datasheet đầy
  đủ. **[Chưa biết]** = không có dữ liệu công khai, phải đo hoặc hỏi hãng. Tên net, số hiệu linh kiện và tên chân
  viết bằng tiếng Anh.
- **Nguồn sự thật duy nhất:** [`netlist.py`](netlist.py). Mọi file khác (KiCad, SVG, BOM, netlist, các bảng chân
  trong README này) được sinh từ nó. Sửa mạch thì sửa `netlist.py`, rồi chạy lại `generate.py` và `check.py`.
- **Tham khảo và giấy phép:** chỉ dùng datasheet và sơ đồ tham chiếu của hãng (WCH, Luckfox, Rockchip), thư viện
  ký hiệu chính thức của KiCad (để đối chiếu sơ đồ chân) và driver Linux của Rockchip BSP (chỉ đọc tên thuộc tính
  device tree, không chép mã). Không chép sơ đồ hay file từ các dự án phần cứng mở có giấy phép GPL/AGPL (JetKVM,
  Luckfox PicoKVM, Aiden, dự án LT7911D trên OSHWHub); các dự án này không được dùng làm nguồn chân.

---

## 0. Tóm tắt

1. **Mạch đã khép kín ở mức chân** cho mọi linh kiện có datasheet công khai: CH32V305RBT6, CH224K, Core1106,
   các bộ nguồn, ESD, đầu nối. `check.py` báo PASS: mọi chân nằm trên net hoặc được đánh dấu không nối, mọi net có
   ít nhất hai chân, mọi chân nguồn của IC có tụ lọc, không trùng tên net. Kết nối đọc ngược từ file KiCad khớp
   `netlist.py` ở cả 746 chân (xem §13).
2. **LT7911D là điểm mù lớn nhất.** Datasheet chịu NDA. Chân 1–23 có số từ trích đoạn công khai của product brief
   **[Có thể]**; 25 chân còn lại chỉ vẽ theo chức năng với số giả dạng `?XTALI` **[Chưa biết]**. Footprint, ánh xạ
   lane DP, thạch anh, nguồn lõi và khả năng làm nguồn cấp điện cho iPhone đều phải lấy từ Lontium trước khi
   layout (§10.1).
3. **Nguồn:** CH224K xin **9 V** từ sạc PD (khác sơ đồ khối ghi 12 V, lý do ở §3.1). Hai buck LMR33630 tạo 5.2 V/3 A
   cho iPhone và 5.0 V/3 A cho hệ thống; hai buck TLV62569 tạo 3.3 V và 1.2 V. iPhone được cấp VBUS qua hai
   P-MOSFET đấu ngược, có shunt đo dòng.
4. **Hai phương án sạc cho iPhone** chọn bằng linh kiện, không phải vẽ lại mạch:
   - **A (mặc định):** box tự cấp 5.2 V cho iPhone; CH32V305 bật công tắc VBUS.
   - **B (khi LT7911D hỗ trợ pass-through hai CC):** CC của sạc đi vào LT7911D, VBUS của sạc đi thẳng tới iPhone,
     LT7911D bật công tắc.
5. **Cần sạc PD ≥ 30 W** (9 V/3 A) để vừa sạc iPhone 15 W vừa chạy box (ước tính §3.5).

---

## 1. Tệp trong thư mục

| Tệp | Nội dung |
|---|---|
| `netlist.py` | Nguồn sự thật: linh kiện (ref, giá trị, mã hàng, LCSC, footprint), mọi chân, net, độ tin cậy, nguồn trích dẫn |
| `layout.py` | Bố trí ký hiệu trên từng trang, dùng chung cho KiCad và SVG |
| `generate.py` | Sinh `kicad/`, `svg/`, `bom.csv`, `netlist.json`, `kicad/box-v1.net` và các bảng chân trong README |
| `check.py` | Kiểm tra luật thiết kế và file sinh ra; `--selftest` cài lỗi giả để chứng minh từng luật bắt được lỗi |
| `kicad/box-v1.kicad_pro`, `kicad/*.kicad_sch` | Dự án KiCad 8 (định dạng 20231120): trang gốc + 5 trang con |
| `kicad/box-v1.net` | Netlist KiCad (s-expression, phiên bản E) |
| `svg/*.svg` | 5 trang sơ đồ xem được trên GitHub |
| `bom.csv` | BOM gom nhóm: reference, qty, value, part_number, lcsc, footprint, notes |
| `netlist.json` | Linh kiện, chân và net dạng JSON |

Chạy lại sau mỗi lần sửa (chỉ cần Python 3 chuẩn):

```sh
cd hardware/box-v1
python3 generate.py            # sinh lại mọi file
python3 check.py --selftest    # PASS/FAIL, mã thoát khác 0 khi có lỗi
```

---

## 2. Sơ đồ khối

![Box độc lập](../../docs/images/box-architecture.png)

```
Sạc PD ─J101─ CH224K (9 V) ─ F101 ─ VIN ─┬─ U102 LMR33630 ─ 5V2_PHONE ─ JP101 ─ Q101/Q102 ─ R126 ─ PHONE_VBUS ─┐
                                         └─ U103 LMR33630 ─ 5V_SYS ─┬─ Core1106 (VCC5V0_SYS)                    │
                                                                   ├─ U104 TLV62569 ─ 3V3 ─ CH32V305, LT7911D IO  │
                                                                   └─ U105 TLV62569 ─ 1V2 ─ LT7911D (lõi, PHY)    │
iPhone ═J201 (USB-C 24P)═╦═ 4 cặp SS + SBU1/2 (DP Alt Mode) ─ U201 LT7911D ═ CSI-2 4 lane ═ U401 Core1106   │
                         ╠═ CC1/CC2 ─ LT7911D (PD, Alt Mode) + ADC CH32                                     │
                         ╠═ D+/D- ─ U301 CH32V305 (USB HS, HID)                                              │
                         ╚═ VBUS ─────────────────────────────────────────────────────────────────────────────┘
U301 CH32V305 ─ SPI0 + UART4 + IRQ/NRST/BOOT0 ─ U401 Core1106 ─┬─ FEPHY ─ J402 RJ45 (magjack)
                                                               ├─ USB OTG ─ J401 USB-C tới PC
                                                               └─ Wi-Fi 6 trên module (bản có Wi-Fi)
```

Các trang sơ đồ (sinh tự động, cùng nội dung với file KiCad):

| Trang | SVG | KiCad |
|---|---|---|
| 1. Nguồn | [svg/power.svg](svg/power.svg) | `kicad/power.kicad_sch` |
| 2. USB-C iPhone + LT7911D | [svg/iphone.svg](svg/iphone.svg) | `kicad/iphone.kicad_sch` |
| 3. CH32V305 | [svg/mcu.svg](svg/mcu.svg) | `kicad/mcu.kicad_sch` |
| 4. Core1106 + Ethernet + USB-C PC | [svg/soc.svg](svg/soc.svg) | `kicad/soc.kicad_sch` |
| 5. Debug, LED, nút | [svg/debug.svg](svg/debug.svg) | `kicad/debug.kicad_sch` |

Quy ước vẽ: không có dây; mỗi đầu chân mang nhãn net (global label) hoặc ký hiệu nguồn (rail), hoặc dấu × (không
nối). Hai chân cùng tên nhãn là cùng một net, kể cả khác trang. Trong SVG, tên/số chân màu cam là **[Chưa biết]**,
màu xanh ngọc là **[Có thể]**; linh kiện DNP vẽ nét đứt.

---

## 3. Cây nguồn

### 3.1 Đầu vào USB-C PD (J101, U101 CH224K)

- CH224K là PD sink, cấu hình bằng một điện trở từ CFG1 xuống GND: 6.8 kΩ = 9 V, 24 kΩ = 12 V, 56 kΩ = 15 V, bỏ
  trống = 20 V. Ở chế độ điện trở, CFG2/CFG3 phải để trống. **[Chắc]**, datasheet CH224 §5.2.1.
- Mạch theo sơ đồ tham chiếu §6.2 của WCH: VDD nối VIN qua 1 kΩ và tụ 1 µF; chân VBUS đo áp qua 10 kΩ. **[Chắc]**
- Chỉ dùng PD: DP và DM của CH224K nối tắt với nhau, D+/D- của J101 để trống (datasheet §5.5). Như vậy không có
  giao thức QC/AFC nào đẩy áp lên ngoài ý muốn. **[Chắc]**
- **Chọn 9 V thay vì 12 V như sơ đồ khối.** Mọi sạc PD từ 18 W trở lên đều có mức 9 V cố định; 12 V không nằm trong
  bộ mức bắt buộc của PD 3.0 nên nhiều sạc không có. **[Có thể]** Muốn 12 V thì thay R103 bằng 24 kΩ; mạch phía sau
  chịu được tới 20 V (bộ buck LMR33630 36 V, tụ vào 50 V).
- Điện trở R101 1 kΩ dùng 1206: ở 15 V tiêu tán ~0.14 W. Nếu dùng 20 V thì tính lại.
- **PG** (open-drain, mức thấp = đã nhận đúng áp) kéo lên 3V3 qua R108 và đưa vào CH32V305 (PB12). Firmware MCU chỉ
  bật VBUS cho iPhone khi PG thấp.
- Bảo vệ đầu vào: F101 cầu chì nhanh 5 A/32 V (1206), D101 TVS SMAJ24A, C101 47 µF 35 V để dập dao động khi cắm
  nóng cáp dài.

### 3.2 Các rail

| Rail | Điện áp | Nguồn tạo | Tải chính | Dòng thiết kế | Ghi chú |
|---|---|---|---|---|---|
| VBUS_IN / VIN | 9 V (5–20 V) | Sạc PD qua J101, F101 | U102, U103, CH224K | ~2.8 A ở 9 V | VIN là sau cầu chì |
| 5V2_PHONE | 5.22 V | U102 LMR33630ADDA, 400 kHz | iPhone qua công tắc | 3 A | R109/R110 = 100k/23.7k; bật khi VIN > ~7.2 V |
| 5V_SYS | 5.02 V | U103 LMR33630ADDA | Core1106, U104, U105 | 1.5 A (định mức 3 A) | R113/R114 = 100k/24.9k; bật khi VIN > ~4.3 V |
| 3V3 | 3.315 V | U104 TLV62569DBV, 1.5 MHz | CH32V305, LT7911D 3.3 V, pull-up, LED, INA180 | ≤ 0.4 A (định mức 2 A) | R118/R119 = 100k/22.1k |
| 1V2 | 1.200 V | U105 TLV62569DBV | LT7911D 1.2 V | **[Chưa biết]**, dự phòng 2 A | R121/R122 = 100k/100k; bật sau 3V3 |
| 3V3_LT | 3.3 V | FB201 từ 3V3 | chân 3.3 V của LT7911D | | ferrite 600 Ω@100 MHz |
| 1V2_LT_A | 1.2 V | FB202 từ 1V2 | VCC12A_RX, VCC12_PI, VCC12_RXPLL | | tách nhiễu cho PLL/analog |
| VDDA_MCU | 3.3 V | FB301 từ 3V3 | VDDA của CH32V305 | | datasheet: VDDA phải bằng VIO |
| CH224_VDD | 3.3 V | shunt nội CH224K | CH224K | vài mA | |
| PHONE_VBUS | 0 hoặc 5.2 V | công tắc Q101/Q102 | iPhone | 3 A | xả về 0 V qua R210 |
| VCC_1V8_MOD, VCC_3V3_MOD | 1.8 / 3.3 V | PMIC trên Core1106 | kéo lên phím RECOVERY; TP | ≤ 300 mA mỗi rail | theo bảng công suất của Luckfox |

Giá trị VREF (LMR33630: 1.0 V; TLV62569: 0.6 V) và ngưỡng EN (~1.2 V) là **[Có thể]**: lấy từ thư viện/ghi nhớ,
chưa đọc được datasheet TI trong môi trường này. `check.py` (luật R10) tính lại điện áp ra, ngưỡng UVLO và dải ADC
từ giá trị điện trở trong `netlist.py`; đổi VREF trong `check.py` nếu datasheet khác.

Linh kiện buck (mỗi LMR33630): 2 × 10 µF 50 V X7R 1206 + 100 nF 50 V sát VIN/GND; tụ bootstrap 100 nF; tụ VCC 1 µF;
cuộn 10 µH (Isat ≥ 5 A cho U102, ≥ 4 A cho U103, DCR ≤ 25 mΩ, cỡ 10×10 mm); 3 × 22 µF 10 V X5R 1206 ở đầu ra.
Với 9 V → 5.2 V, 400 kHz, 10 µH thì gợn dòng ~0.55 A (18 % của 3 A). Mỗi TLV62569: 10 µF + 100 nF vào, cuộn 2.2 µH
(Isat ≥ 3 A, 4×4 mm), 2 × 22 µF 6.3 V ra.

### 3.3 Trình tự bật nguồn

1. VIN lên → U103 (5V_SYS) chạy khi VIN > ~4.3 V; U102 (5V2_PHONE) chỉ chạy khi VIN > ~7.2 V, nên với sạc không có
   PD (5 V) box vẫn chạy nhưng không sạc iPhone.
2. 5V_SYS → Core1106 tự trình tự nguồn bằng PMIC EA3036C trên module **[Chắc]** (sơ đồ Core1106); U104 (3V3) bật
   ngay (EN kéo lên 5V_SYS).
3. U105 (1V2) bật sau 3V3 khoảng 4 ms: EN lấy từ 3V3 qua RC 100 kΩ/100 nF. Thứ tự 3.3 V trước 1.2 V là giả định
   an toàn; yêu cầu thật của LT7911D **[Chưa biết]**.
4. LT7911D: RST_N kéo lên 3V3 qua 10 kΩ với tụ 1 µF (τ = 10 ms), nên chip chạy ngay khi có nguồn để lo PD/CC. RV1106
   điều khiển reset qua GPIO0_A3 (pad 61), chân này mặc định có pull-up nên không giữ LT7911D trong reset lúc SoC
   khởi động. **[Chắc]** về mức mặc định (hậu tố `_u` trong bảng chân Luckfox).
5. CH32V305 có mạch POR nội; NRST có RC 4.7 kΩ/100 nF.
6. Đường CSI: LT7911D có thể xuất LP-11 trước khi RV1106 chạy xong. Cả hai lên từ cùng 5V_SYS trong vài ms; driver
   chỉ mở stream sau khi probe. Rủi ro dòng ngược qua chân CSI khi module chưa có nguồn là thấp nhưng **[Chưa biết]**.

### 3.4 Đường sạc cho iPhone (pass-through)

Công tắc: Q101 và Q102 (P-MOSFET SO-8, AO4407A: −30 V, ~11 mΩ ở Vgs −10 V **[Có thể]**) đấu chung cực nguồn (PSW_S), hai cực máng hướng
ra hai phía. Khi tắt, hai diode thân chặn cả hai chiều, nên iPhone không cấp ngược vào box và box không đẩy áp
ra khi chưa được phép. Q103 (2N7002) kéo cổng xuống qua R124 10 kΩ; R123 100 kΩ giữ cổng ở mức tắt; C128 47 nF làm
khởi động mềm ~0.5 ms; D102 (zener 10 V) kẹp Vgs khi dùng phương án B với VIN tới 20 V. Với 5.2 V thì Vgs ≈ −4.7 V.

| | Phương án A (mặc định) | Phương án B |
|---|---|---|
| CC của sạc (J101) | vào CH224K (R104/R105 = 0 Ω) | vào cổng PD thứ hai của LT7911D (R106/R107 = 0 Ω, bỏ R104/R105, bỏ U101) |
| Nguồn cho công tắc | 5V2_PHONE (JP101 nối 1-2) | VIN (JP101 nối 2-3), áp do LT7911D thương lượng |
| Ai bật công tắc | CH32V305 PB1 (R127 = 0 Ω) | GPIO của LT7911D (R128 = 0 Ω, bỏ R127) |
| TVS ở VBUS iPhone | D201 SMF6.0A | đổi sang SMF22A |
| Điều kiện | LT7911D làm được vai Source (Rp) + UFP_D + DR_Swap với iPhone | LT7911D có firmware pass-through hai CC |

Logic bật VBUS trong firmware MCU (phương án A): PD_PG thấp **và** VBUS phía iPhone < 0.8 V (không có ai khác đang
cấp) **và** áp CC1/CC2 cho thấy có thiết bị Rd. Ngắt khi dòng (PHONE_ISENSE) vượt ngưỡng hoặc khi rút cáp. MCU đọc:
CC1_SENSE/CC2_SENSE (qua 100 kΩ, không tải đường CC), PHONE_VBUS_SENSE (chia 100k/15k, 20 V → 2.6 V),
PHONE_ISENSE (INA180A2, gain 50, shunt 10 mΩ → 0.5 V/A, tối đa 6.6 A trước khi bão hoà ở 3.3 V).

Theo chuẩn Type-C, bên cấp nguồn phải đưa VBUS về vSafe0V (< 0.8 V) sau khi rút cáp; R210 10 kΩ cùng ~20 µF tổng
đưa 5.2 V về 0.8 V trong ~0.4 s (giới hạn 650 ms). **[Có thể]**, theo USB Type-C spec.

### 3.5 Ước tính công suất

| Tải | Công suất | Độ tin cậy |
|---|---|---|
| iPhone, 5.2 V × 3 A | 15.6 W | [Có thể]: iPhone lấy tới 3 A khi nguồn quảng bá 3 A |
| Core1106 | 2.4 W đo được (467 mA), thiết kế 5 W (1 A) | [Chắc], bảng công suất Luckfox |
| 3V3 (CH32V305, LT7911D 3.3 V, LED) | ≤ 1.3 W | [Chưa biết] cho LT7911D |
| 1V2 (LT7911D) | ≤ 1 W | [Chưa biết] |
| Tổng từ VIN (hiệu suất buck ~92 %) | ~25 W → ~2.8 A ở 9 V | ước tính |

Kết luận: cần sạc PD có mức 9 V/3 A (loại 30 W). Với sạc 20 W, firmware nên giới hạn dòng iPhone (tắt công tắc
khi PHONE_ISENSE cao hoặc khi VIN_SENSE sụt).

### 3.6 Nuôi box từ PC (JP102, chỉ để phát triển)

JP102 hở mặc định. Hàn JP102 thì PC_VBUS → D104 (SS34) → 5V_SYS, được ~4.6–4.7 V, sát mức tối thiểu 4.6 V của
Core1106. **Không cắm sạc vào J101 khi JP102 đã hàn:** khi đó 5V_SYS có thể đi ngược qua diode thân của U103 lên
VIN và ra chân VBUS của J101. iPhone không được sạc ở chế độ này (U102 tắt).

---

## 4. Từng khối và bảng chân

Mỗi bảng dưới đây sinh từ `netlist.py`. Cột "Nối tới" liệt kê các chân khác trên cùng net (`REF.chân`). NC = không
nối (có dấu no-connect trong KiCad). Cột cuối là độ tin cậy của **số chân và chức năng**.

### 4.1 Nguồn

Nguồn trích dẫn: CH224K theo datasheet WCH (bản V1F trên GitHub); LMR33630, TLV62569, INA180A2, AO4407A/2N7002 theo
thư viện ký hiệu KiCad 8.0.9 (thư viện này chép sơ đồ chân từ datasheet hãng) **[Có thể]**; đầu nối USB-C theo bảng
chân chuẩn Type-C **[Chắc]**. Chân CC1/CC2 của CH224K: bảng chân trong datasheet ghi "6, 7 = CC1, CC2" nhưng hình §6.2
và ký hiệu KiCad ghi CC1 = 7, CC2 = 6; hai chân đối xứng nên cách nào cũng chạy, sơ đồ theo hình §6.2.

<!-- BEGIN GENERATED: pins-power -->
**J101 USB-C PD IN** (TYPE-C-31-M-12; footprint `Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| A1 | GND | GND | rail (156 chân) |  | [Chắc] |
| A4 | VBUS | VBUS_IN | F101.1 |  | [Chắc] |
| A5 | CC1 | PD_CC1 | R104.1, R106.1 |  | [Chắc] |
| A6 | D+ | NC | - |  | [Chắc] |
| A7 | D- | NC | - |  | [Chắc] |
| A8 | SBU1 | NC | - |  | [Chắc] |
| A9 | VBUS | VBUS_IN | F101.1 |  | [Chắc] |
| A12 | GND | GND | rail (156 chân) |  | [Chắc] |
| B1 | GND | GND | rail (156 chân) |  | [Chắc] |
| B4 | VBUS | VBUS_IN | F101.1 |  | [Chắc] |
| B5 | CC2 | PD_CC2 | R105.1, R107.1 |  | [Chắc] |
| B6 | D+ | NC | - |  | [Chắc] |
| B7 | D- | NC | - |  | [Chắc] |
| B8 | SBU2 | NC | - |  | [Chắc] |
| B9 | VBUS | VBUS_IN | F101.1 |  | [Chắc] |
| B12 | GND | GND | rail (156 chân) |  | [Chắc] |
| S1 | SHIELD | GND | rail (156 chân) |  | [Chắc] |

**JP101 PSW_SRC** (-; footprint `Jumper:SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm`; nguồn: -)

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | A | 5V2_PHONE | L101.2, C108.1, C109.1, C110.1, R109.1, TP502.1 |  | [Chắc] |
| 2 | C | PSW_IN | Q101.5, Q101.6, Q101.7, Q101.8 |  | [Chắc] |
| 3 | B | VIN | rail (17 chân) |  | [Chắc] |

**JP102 PC_PWR** (-; footprint `Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm`; nguồn: -)

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | A | PC_VBUS | rail (7 chân) |  | [Chắc] |
| 2 | B | PCPWR_A | D104.2 |  | [Chắc] |

**Q101 AO4407A** (AO4407A; footprint `Package_SO:SOIC-8_3.9x4.9mm_P1.27mm`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | S | PSW_S | Q102.1, Q102.2, Q102.3, R123.1, C128.1, D102.1 |  | [Chắc] |
| 2 | S | PSW_S | Q102.1, Q102.2, Q102.3, R123.1, C128.1, D102.1 |  | [Chắc] |
| 3 | S | PSW_S | Q102.1, Q102.2, Q102.3, R123.1, C128.1, D102.1 |  | [Chắc] |
| 4 | G | PSW_G | Q102.4, R123.2, C128.2, D102.2, R124.1 |  | [Chắc] |
| 5 | D | PSW_IN | JP101.2 |  | [Chắc] |
| 6 | D | PSW_IN | JP101.2 |  | [Chắc] |
| 7 | D | PSW_IN | JP101.2 |  | [Chắc] |
| 8 | D | PSW_IN | JP101.2 |  | [Chắc] |

**Q102 AO4407A** (AO4407A; footprint `Package_SO:SOIC-8_3.9x4.9mm_P1.27mm`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | S | PSW_S | Q101.1, Q101.2, Q101.3, R123.1, C128.1, D102.1 |  | [Chắc] |
| 2 | S | PSW_S | Q101.1, Q101.2, Q101.3, R123.1, C128.1, D102.1 |  | [Chắc] |
| 3 | S | PSW_S | Q101.1, Q101.2, Q101.3, R123.1, C128.1, D102.1 |  | [Chắc] |
| 4 | G | PSW_G | Q101.4, R123.2, C128.2, D102.2, R124.1 |  | [Chắc] |
| 5 | D | PSW_OUT | R126.1, U106.3 |  | [Chắc] |
| 6 | D | PSW_OUT | R126.1, U106.3 |  | [Chắc] |
| 7 | D | PSW_OUT | R126.1, U106.3 |  | [Chắc] |
| 8 | D | PSW_OUT | R126.1, U106.3 |  | [Chắc] |

**Q103 2N7002** (2N7002; footprint `Package_TO_SOT_SMD:SOT-23`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | G | PHONE_VBUS_EN | R125.1, R127.2, R128.2 |  | [Chắc] |
| 2 | S | GND | rail (160 chân) |  | [Chắc] |
| 3 | D | PSW_GD | R124.2 |  | [Chắc] |

**U101 CH224K** (CH224K; footprint `Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm`; nguồn: [CH224](https://raw.githubusercontent.com/makespacemadrid/cheap-wled-controller/main/datasheet/ch224k.pdf))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | VDD | CH224_VDD | R101.2, C102.1 | shunt 3.3 V nội, cấp qua 1 kΩ từ VIN | [Chắc] |
| 2 | CFG2 | NC | - | chế độ điện trở: CFG2/CFG3 phải để trống (§5.2.1) | [Chắc] |
| 3 | CFG3 | NC | - | như CFG2 | [Chắc] |
| 4 | DP | CH224_DPDM |  | nối tắt DP-DM: chỉ dùng PD (§5.5) | [Chắc] |
| 5 | DM | CH224_DPDM |  | nối tắt DP-DM | [Chắc] |
| 6 | CC2 | CH224_CC2 | R105.2 | qua R105 0R tới J101.B5 | [Chắc] |
| 7 | CC1 | CH224_CC1 | R104.2 | qua R104 0R tới J101.A5 | [Chắc] |
| 8 | VBUS | CH224_VSNS | R102.2 | đo áp qua 10 kΩ | [Chắc] |
| 9 | CFG1 | CH224_CFG1 | R103.1 | 6.8 kΩ xuống GND = xin 9 V | [Chắc] |
| 10 | PG | PD_PG | R108.1, U301.33 | open-drain, mức thấp = đã có điện áp xin | [Chắc] |
| 11 | GND | GND | rail (160 chân) | EPAD (datasheet gọi là chân 0) | [Chắc] |

**U102 LMR33630ADDA** (LMR33630ADDAR; footprint `Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | GND | GND | rail (159 chân) |  | [Chắc] |
| 2 | VIN | VIN | rail (17 chân) |  | [Chắc] |
| 3 | EN | U102_EN | R111.2, R112.1 | chia áp UVLO 100k/20k | [Chắc] |
| 4 | PG | NC | - | không dùng | [Chắc] |
| 5 | FB | U102_FB | R109.2, R110.1 |  | [Chắc] |
| 6 | VCC | U102_VCC | C106.1 | LDO nội, tụ 1 µF | [Chắc] |
| 7 | BOOT | U102_BOOT | C107.1 |  | [Chắc] |
| 8 | SW | U102_SW | C107.2, L101.1 |  | [Chắc] |
| 9 | EP | GND | rail (159 chân) | pad nhiệt = GND | [Chắc] |

**U103 LMR33630ADDA** (LMR33630ADDAR; footprint `Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | GND | GND | rail (159 chân) |  | [Chắc] |
| 2 | VIN | VIN | rail (17 chân) |  | [Chắc] |
| 3 | EN | U103_EN | R115.2, R116.1 | chia áp UVLO 100k/39k | [Chắc] |
| 4 | PG | NC | - | không dùng | [Chắc] |
| 5 | FB | U103_FB | R113.2, R114.1 |  | [Chắc] |
| 6 | VCC | U103_VCC | C114.1 | LDO nội, tụ 1 µF | [Chắc] |
| 7 | BOOT | U103_BOOT | C115.1 |  | [Chắc] |
| 8 | SW | U103_SW | C115.2, L102.1 |  | [Chắc] |
| 9 | EP | GND | rail (159 chân) | pad nhiệt = GND | [Chắc] |

**U104 TLV62569DBV** (TLV62569DBVR; footprint `Package_TO_SOT_SMD:SOT-23-5`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | EN | U104_EN | R117.2 |  | [Chắc] |
| 2 | GND | GND | rail (160 chân) |  | [Chắc] |
| 3 | SW | U104_SW | L103.1 |  | [Chắc] |
| 4 | VIN | 5V_SYS | rail (18 chân) |  | [Chắc] |
| 5 | FB | U104_FB | R118.2, R119.1 |  | [Chắc] |

**U105 TLV62569DBV** (TLV62569DBVR; footprint `Package_TO_SOT_SMD:SOT-23-5`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | EN | U105_EN | R120.2, C125.1 |  | [Chắc] |
| 2 | GND | GND | rail (160 chân) |  | [Chắc] |
| 3 | SW | U105_SW | L104.1 |  | [Chắc] |
| 4 | VIN | 5V_SYS | rail (18 chân) |  | [Chắc] |
| 5 | FB | U105_FB | R121.2, R122.1 |  | [Chắc] |

**U106 INA180A2** (INA180A2IDBVR; footprint `Package_TO_SOT_SMD:SOT-23-5`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | OUT | PHONE_ISENSE | C130.1, U301.17 |  | [Chắc] |
| 2 | GND | GND | rail (160 chân) |  | [Chắc] |
| 3 | IN+ | PSW_OUT | Q102.5, Q102.6, Q102.7, Q102.8, R126.1 | Kelvin sát R126 phía MOSFET | [Chắc] |
| 4 | IN- | PHONE_VBUS | rail (11 chân) | Kelvin sát R126 phía iPhone | [Chắc] |
| 5 | V+ | 3V3 | rail (30 chân) |  | [Chắc] |

Linh kiện thụ động và test point của khối:

| Ref | Giá trị | Chân 1 | Chân 2 | Footprint | Vai trò |
|---|---|---|---|---|---|
| C101 | 47uF 35V | VIN | GND | `CP_Elec_6.3x7.7` | Tụ khối VIN, giảm dao động khi cắm nóng cáp dài |
| C102 | 1uF 25V | CH224_VDD | GND | `C_0603_1608Metric` | Tụ VDD CH224K |
| C103 | 10uF 50V X7R | VIN | GND | `C_1206_3216Metric` | Tụ vào U102 |
| C104 | 10uF 50V X7R | VIN | GND | `C_1206_3216Metric` | Tụ vào U102 |
| C105 | 100nF 50V | VIN | GND | `C_0603_1608Metric` | Tụ cao tần sát chân VIN/GND U102 |
| C106 | 1uF 16V | U102_VCC | GND | `C_0402_1005Metric` | Tụ LDO nội U102 |
| C107 | 100nF 16V | U102_BOOT | U102_SW | `C_0402_1005Metric` | Tụ bootstrap U102 |
| C108 | 22uF 10V X5R | 5V2_PHONE | GND | `C_1206_3216Metric` | Tụ ra U102 |
| C109 | 22uF 10V X5R | 5V2_PHONE | GND | `C_1206_3216Metric` | Tụ ra U102 |
| C110 | 22uF 10V X5R | 5V2_PHONE | GND | `C_1206_3216Metric` | Tụ ra U102 |
| C111 | 10uF 50V X7R | VIN | GND | `C_1206_3216Metric` | Tụ vào U103 |
| C112 | 10uF 50V X7R | VIN | GND | `C_1206_3216Metric` | Tụ vào U103 |
| C113 | 100nF 50V | VIN | GND | `C_0603_1608Metric` | Tụ cao tần sát chân VIN/GND U103 |
| C114 | 1uF 16V | U103_VCC | GND | `C_0402_1005Metric` | Tụ LDO nội U103 |
| C115 | 100nF 16V | U103_BOOT | U103_SW | `C_0402_1005Metric` | Tụ bootstrap U103 |
| C116 | 22uF 10V X5R | 5V_SYS | GND | `C_1206_3216Metric` | Tụ ra U103 |
| C117 | 22uF 10V X5R | 5V_SYS | GND | `C_1206_3216Metric` | Tụ ra U103 |
| C118 | 22uF 10V X5R | 5V_SYS | GND | `C_1206_3216Metric` | Tụ ra U103 |
| C119 | 10uF 10V | 5V_SYS | GND | `C_0603_1608Metric` | Tụ vào U104 |
| C120 | 100nF | 5V_SYS | GND | `C_0402_1005Metric` | Tụ cao tần vào U104 |
| C121 | 22uF 6.3V X5R | 3V3 | GND | `C_0805_2012Metric` | Tụ ra U104 |
| C122 | 22uF 6.3V X5R | 3V3 | GND | `C_0805_2012Metric` | Tụ ra U104 |
| C123 | 10uF 10V | 5V_SYS | GND | `C_0603_1608Metric` | Tụ vào U105 |
| C124 | 100nF | 5V_SYS | GND | `C_0402_1005Metric` | Tụ cao tần vào U105 |
| C125 | 100nF | U105_EN | GND | `C_0402_1005Metric` | Tụ trễ EN U105 (τ = 10 ms) |
| C126 | 22uF 6.3V X5R | 1V2 | GND | `C_0805_2012Metric` | Tụ ra U105 |
| C127 | 22uF 6.3V X5R | 1V2 | GND | `C_0805_2012Metric` | Tụ ra U105 |
| C128 | 47nF 50V | PSW_S | PSW_G | `C_0402_1005Metric` | Khởi động mềm (~0.5 ms), giới hạn dòng nạp tụ iPhone |
| C129 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ nguồn U106 |
| C130 | 1nF | PHONE_ISENSE | GND | `C_0402_1005Metric` | Lọc đầu ra U106 trước ADC |
| D101 | SMAJ24A | VIN | GND | `D_SMA` | TVS 24 V standoff trên VIN |
| D102 | BZT52C10 | PSW_S | PSW_G | `D_SOD-123` | Zener 10 V kẹp Vgs khi dùng phương án B (VIN tới 20 V) |
| D104 | SS34 | 5V_SYS | PCPWR_A | `D_SMA` | Schottky 3 A 40 V: PC_VBUS -> 5V_SYS (chế độ phát triển) |
| F101 | 5A 32V | VBUS_IN | VIN | `Fuse_1206_3216Metric` | Cầu chì nhanh 5 A / 32 V cho đầu vào [Có thể] |
| L101 | 10uH | U102_SW | 5V2_PHONE | `L_Bourns_SRP1038C_10.0x10.0mm` | Cuộn cảm buck U102, Isat ≥ 5 A [Có thể] |
| L102 | 10uH | U103_SW | 5V_SYS | `L_Bourns_SRP1038C_10.0x10.0mm` | Cuộn cảm buck U103, Isat ≥ 4 A [Có thể] |
| L103 | 2.2uH | U104_SW | 3V3 | `L_Bourns-SRN4018` | Cuộn cảm U104, Isat ≥ 3 A, 4x4 mm [Có thể] |
| L104 | 2.2uH | U105_SW | 1V2 | `L_Bourns-SRN4018` | Cuộn cảm U105, Isat ≥ 3 A, 4x4 mm [Có thể] |
| R101 | 1k | VIN | CH224_VDD | `R_1206_3216Metric` | Điện trở cấp VDD CH224K (datasheet §6.2) |
| R102 | 10k | VIN | CH224_VSNS | `R_0402_1005Metric` | Nối tiếp chân VBUS của CH224K (§6.2) |
| R103 | 6.8k 1% | CH224_CFG1 | GND | `R_0402_1005Metric` | Chọn điện áp xin: 6.8k=9V (mặc định), 24k=12V, 56k=15V, bỏ trống=20V |
| R104 | 0R | PD_CC1 | CH224_CC1 | `R_0402_1005Metric` | Phương án A: CC của sạc vào CH224K |
| R105 | 0R | PD_CC2 | CH224_CC2 | `R_0402_1005Metric` | Phương án A: CC của sạc vào CH224K |
| R106 **DNP** | 0R | PD_CC1 | LT_PDCC1 | `R_0402_1005Metric` | Phương án B: CC của sạc vào cổng PD thứ hai của LT7911D [Chưa biết] |
| R107 **DNP** | 0R | PD_CC2 | LT_PDCC2 | `R_0402_1005Metric` | Phương án B (như R106) [Chưa biết] |
| R108 | 10k | PD_PG | 3V3 | `R_0402_1005Metric` | Kéo lên cho PG (open-drain) tới MCU PB12 |
| R109 | 100k 1% | 5V2_PHONE | U102_FB | `R_0402_1005Metric` | Hồi tiếp trên U102 |
| R110 | 23.7k 1% | U102_FB | GND | `R_0402_1005Metric` | Hồi tiếp dưới U102: Vout = 1.0 V x (1 + 100k/Rfbb) |
| R111 | 100k | VIN | U102_EN | `R_0402_1005Metric` | Chia áp EN U102 (trên) |
| R112 | 20k | U102_EN | GND | `R_0402_1005Metric` | Chia áp EN U102 (dưới) |
| R113 | 100k 1% | 5V_SYS | U103_FB | `R_0402_1005Metric` | Hồi tiếp trên U103 |
| R114 | 24.9k 1% | U103_FB | GND | `R_0402_1005Metric` | Hồi tiếp dưới U103: Vout = 1.0 V x (1 + 100k/Rfbb) |
| R115 | 100k | VIN | U103_EN | `R_0402_1005Metric` | Chia áp EN U103 (trên) |
| R116 | 39k | U103_EN | GND | `R_0402_1005Metric` | Chia áp EN U103 (dưới) |
| R117 | 100k | 5V_SYS | U104_EN | `R_0402_1005Metric` | EN U104: bật ngay khi có 5V_SYS |
| R118 | 100k 1% | 3V3 | U104_FB | `R_0402_1005Metric` | Hồi tiếp trên U104 |
| R119 | 22.1k 1% | U104_FB | GND | `R_0402_1005Metric` | Hồi tiếp dưới U104: Vout = 0.6 V x (1 + 100k/Rbot) |
| R120 | 100k | 3V3 | U105_EN | `R_0402_1005Metric` | EN U105 lấy từ 3V3 qua RC: 1V2 lên sau 3V3 ~4 ms |
| R121 | 100k 1% | 1V2 | U105_FB | `R_0402_1005Metric` | Hồi tiếp trên U105 |
| R122 | 100k 1% | U105_FB | GND | `R_0402_1005Metric` | Hồi tiếp dưới U105: Vout = 0.6 V x (1 + 100k/Rbot) |
| R123 | 100k | PSW_S | PSW_G | `R_0402_1005Metric` | Kéo cổng lên nguồn chung: MOSFET tắt mặc định |
| R124 | 10k | PSW_G | PSW_GD | `R_0402_1005Metric` | Điện trở cổng: Vgs = -Vin x 100k/110k |
| R125 | 100k | PHONE_VBUS_EN | GND | `R_0402_1005Metric` | Mặc định tắt VBUS iPhone |
| R126 | 10m 1% | PSW_OUT | PHONE_VBUS | `R_1206_3216Metric` | Shunt đo dòng sạc iPhone (Kelvin) |
| R127 | 0R | MCU_VBUS_EN | PHONE_VBUS_EN | `R_0402_1005Metric` | Phương án A: CH32V305 bật VBUS iPhone |
| R128 **DNP** | 0R | LT_VBUS_EN | PHONE_VBUS_EN | `R_0402_1005Metric` | Phương án B: GPIO của LT7911D bật VBUS iPhone [Chưa biết] |
<!-- END GENERATED: pins-power -->

### 4.2 USB-C iPhone + LT7911D

**Những gì chắc chắn về LT7911D** (driver `lt7911d.c`, `lt7911d.h` và DTS `rk3588s-evb1-lp4x-v10-camera.dtsi` của
Rockchip BSP develop-5.10) **[Chắc]**:

- I2C slave địa chỉ 7-bit **0x2B**; thanh ghi 16-bit truy cập qua trang (ghi 0xFF = byte cao); chip ID **0x0516**
  ở 0xA000/0xA001.
- `reset-gpios` mức thấp tích cực; `power-gpios` và `plugin-det-gpios` là tuỳ chọn; ngắt từ chip là cạnh lên.
- Driver bắt buộc có clock `xvclk` trong device tree (probe lỗi nếu thiếu), dù LT7911D có thạch anh riêng. Trên
  RV1106 khai một clock CRU (ví dụ MIPI_CLK0_OUT) mà không cần nối chân; pad 19 của Core1106 để trống.
- CSI-2 4 lane (`data-lanes = <1 2 3 4>`), link frequency 400 MHz (800 Mbit/s mỗi lane), định dạng UYVY 8-bit.

**Những gì [Có thể]:** tên và số chân 1–23 (VCC12D_RX, D0P/D0N, …, UCC1 = 14, UCC2 = 15, AUXP/AUXN = 17/18,
RST_N = 20, CSCL/CSDA = 21/22, RX_HPD = 23) lấy từ trích đoạn tìm kiếm của product brief và datasheet trên LCSC
(C5310990); không tải được file gốc (proxy chặn lontiumsemi.com và datasheet.lcsc.com). Nguồn nuôi 1.2 V + 3.3 V.

**Những gì [Chưa biết] và cách vẽ tạm:**

- Các chân MIPI TX, thạch anh, GPIO ngắt, chân lõi `VDD`, nguồn MIPI TX, I2S/SPDIF, EPAD: vẽ với số giả `?..`.
  File KiCad vì vậy không khớp được với footprint QFN-64 nào; đó là chủ ý, để không ai layout trước khi có datasheet.
- Ánh xạ cặp SS của ổ USB-C vào lane D0–D3: vẽ tạm D0 ← RX2 (A11/A10), D1 ← TX2 (B2/B3), D2 ← RX1 (B11/B10),
  D3 ← TX1 (A2/A3). LT7911D có chức năng đảo lane nên thứ tự có thể đặt trong firmware, nhưng việc chip tự xử lý
  lật đầu cắm (orientation) hay cần theo cách nối của sơ đồ Lontium thì chưa biết. Nối P với P, N với N.
- AUX: SBU1 → C216 100 nF → AUXP, SBU2 → C217 100 nF → AUXN. Chiều AUX khi lật đầu cắm và điện trở phân cực phía
  sink (R206/R207 1 MΩ, DNP) theo sơ đồ Lontium.
- `SLEEP_33`: chức năng chưa rõ; nối TP201, có chỗ cho R204 (DNP) kéo xuống.
- Thạch anh X201 vẽ 25 MHz với tụ 18 pF, chỉ là giả định.

Bảo vệ ở ổ cắm: U202/U203 (TPD4E05U06, 0.5 pF, kiểu đi xuyên) cho 4 cặp SS; U204 cho CC1/CC2/SBU1/SBU2; U205
(USBLC6-2SC6) cho D+/D-. D201 TVS cho VBUS. R211/R212 (5.1 kΩ, DNP) là Rd tạm để thử HID khi chưa hàn LT7911D
(§9, bước 3).

<!-- BEGIN GENERATED: pins-iphone -->
**J201 USB-C iPhone** (Molex 105450-0101; footprint `Connector_USB:USB_C_Receptacle_Molex_105450-0101`; nguồn: [USBC_SPEC](https://www.usb.org/document-library/usb-type-cr-cable-and-connector-specification-release-24))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| A1 | GND | GND | rail (156 chân) |  | [Chắc] |
| A2 | TX1+ | SS_TX1_P | U201.11, U202.1 |  | [Chắc] |
| A3 | TX1- | SS_TX1_N | U201.12, U202.2 |  | [Chắc] |
| A4 | VBUS | PHONE_VBUS | rail (8 chân) |  | [Chắc] |
| A5 | CC1 | PHONE_CC1 | U201.14, U204.1, R211.1, R304.1 |  | [Chắc] |
| A6 | D+ | PHONE_USB_DP | U205.1, U205.6, U301.59 |  | [Chắc] |
| A7 | D- | PHONE_USB_DN | U205.3, U205.4, U301.58 |  | [Chắc] |
| A8 | SBU1 | PHONE_SBU1 | C216.1, U204.4 |  | [Chắc] |
| A9 | VBUS | PHONE_VBUS | rail (8 chân) |  | [Chắc] |
| A10 | RX2- | SS_RX2_N | U201.3, U203.5 |  | [Chắc] |
| A11 | RX2+ | SS_RX2_P | U201.2, U203.4 |  | [Chắc] |
| A12 | GND | GND | rail (156 chân) |  | [Chắc] |
| B1 | GND | GND | rail (156 chân) |  | [Chắc] |
| B2 | TX2+ | SS_TX2_P | U201.5, U203.1 |  | [Chắc] |
| B3 | TX2- | SS_TX2_N | U201.6, U203.2 |  | [Chắc] |
| B4 | VBUS | PHONE_VBUS | rail (8 chân) |  | [Chắc] |
| B5 | CC2 | PHONE_CC2 | U201.15, U204.2, R212.1, R305.1 |  | [Chắc] |
| B6 | D+ | PHONE_USB_DP | U205.1, U205.6, U301.59 |  | [Chắc] |
| B7 | D- | PHONE_USB_DN | U205.3, U205.4, U301.58 |  | [Chắc] |
| B8 | SBU2 | PHONE_SBU2 | C217.1, U204.5 |  | [Chắc] |
| B9 | VBUS | PHONE_VBUS | rail (8 chân) |  | [Chắc] |
| B10 | RX1- | SS_RX1_N | U201.9, U202.5 |  | [Chắc] |
| B11 | RX1+ | SS_RX1_P | U201.8, U202.4 |  | [Chắc] |
| B12 | GND | GND | rail (156 chân) |  | [Chắc] |
| S1 | SHIELD | GND | rail (156 chân) |  | [Chắc] |

**U201 LT7911D** (LT7911D; footprint `box-v1:LT7911D_QFN-64-1EP_7.5x7.5mm_P0.4mm`; nguồn: [LT7911D_BRIEF](https://www.lontiumsemi.com/UploadFiles/2022-10/LT7911D_Brief_R1.3.pdf))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | VCC12D_RX | 1V2 | rail (10 chân) | 1.2 V digital DP RX | [Có thể] |
| 2 | D0P | SS_RX2_P | J201.A11, U203.4 | DP lane 0+: ánh xạ cặp SS tạm thời, theo sơ đồ tham chiếu Lontium | [Có thể] |
| 3 | D0N | SS_RX2_N | J201.A10, U203.5 | DP lane 0- | [Có thể] |
| 4 | VCC12A_RX | 1V2_LT_A | C204.1, C206.1, C207.1, C213.1, FB202.2, TP510.1 | 1.2 V analog DP RX | [Có thể] |
| 5 | D1P | SS_TX2_P | J201.B2, U203.1 | DP lane 1+ | [Có thể] |
| 6 | D1N | SS_TX2_N | J201.B3, U203.2 | DP lane 1- | [Có thể] |
| 7 | VCC33_RX | 3V3_LT | rail (7 chân) | 3.3 V DP RX | [Có thể] |
| 8 | D2P | SS_RX1_P | J201.B11, U202.4 | DP lane 2+ | [Có thể] |
| 9 | D2N | SS_RX1_N | J201.B10, U202.5 | DP lane 2- | [Có thể] |
| 10 | VCC12_PI | 1V2_LT_A | C204.1, C206.1, C207.1, C213.1, FB202.2, TP510.1 | 1.2 V phase interpolator | [Có thể] |
| 11 | D3P | SS_TX1_P | J201.A2, U202.1 | DP lane 3+ | [Có thể] |
| 12 | D3N | SS_TX1_N | J201.A3, U202.2 | DP lane 3- | [Có thể] |
| 13 | VCC12_RXPLL | 1V2_LT_A | C204.1, C206.1, C207.1, C213.1, FB202.2, TP510.1 | 1.2 V RX PLL | [Có thể] |
| 14 | UCC1 | PHONE_CC1 | J201.A5, U204.1, R211.1, R304.1 | Type-C CC1 phía iPhone (PD + Alt Mode) | [Có thể] |
| 15 | UCC2 | PHONE_CC2 | J201.B5, U204.2, R212.1, R305.1 | Type-C CC2 phía iPhone | [Có thể] |
| 16 | VCC33_IO | 3V3_LT | rail (7 chân) | 3.3 V IO (I2C, GPIO 3.3 V) | [Có thể] |
| 17 | AUXP | LT_AUX_P | C216.2, R206.1 | DP AUX+ (qua tụ 100 nF từ SBU1) | [Có thể] |
| 18 | AUXN | LT_AUX_N | C217.2, R207.1 | DP AUX- (qua tụ 100 nF từ SBU2) | [Có thể] |
| 19 | SLEEP_33 | LT_SLEEP | R204.1, TP201.1 | chức năng chưa rõ: để TP + R204 DNP | [Có thể] |
| 20 | RST_N | LT_RST_N | R203.1, C215.1, U401.61 | reset mức thấp | [Có thể] |
| 21 | CSCL | LT_SCL | R201.1, U401.65 | I2C slave 0x2B (7-bit) | [Có thể] |
| 22 | CSDA | LT_SDA | R202.1, U401.66 | I2C slave | [Có thể] |
| 23 | RX_HPD | LT_RX_HPD | TP202.1 | HPD phía DP; ở Type-C HPD đi qua bản tin PD: để TP | [Có thể] |
| ?EPAD | EPAD | GND | rail (160 chân) | pad nhiệt = GND (giả định) | [Chưa biết] |
| ?IIS_D0 | IIS_D0 | NC | - | không dùng | [Chưa biết] |
| ?IIS_MCLK | IIS_MCLK | NC | - | không dùng | [Chưa biết] |
| ?IIS_SCLK | IIS_SCLK | NC | - | không dùng | [Chưa biết] |
| ?IIS_WS | IIS_WS | NC | - | âm thanh: không dùng | [Chưa biết] |
| ?INT | GPIO_INT | LT_INT | R205.1, U401.67 | GPIO ngắt tới SoC (firmware quyết định chân nào) | [Chưa biết] |
| ?PDCC1 | PD_CC1 | LT_PDCC1 | R106.2 | phương án B: CC phía sạc (nếu chip có cổng PD thứ hai) | [Chưa biết] |
| ?PDCC2 | PD_CC2 | LT_PDCC2 | R107.2 | phương án B | [Chưa biết] |
| ?SPDIF | VSYNC_OUT/SPDIF | NC | - | không dùng | [Chưa biết] |
| ?TX0N | TXA_D0N | CSI_D0_N | U401.11 |  | [Chưa biết] |
| ?TX0P | TXA_D0P | CSI_D0_P | U401.12 | CSI lane 0+ | [Chưa biết] |
| ?TX1N | TXA_D1N | CSI_D1_N | U401.7 |  | [Chưa biết] |
| ?TX1P | TXA_D1P | CSI_D1_P | U401.8 | CSI lane 1+ | [Chưa biết] |
| ?TX2N | TXA_D2N | CSI_D2_N | U401.5 |  | [Chưa biết] |
| ?TX2P | TXA_D2P | CSI_D2_P | U401.6 | CSI lane 2+ | [Chưa biết] |
| ?TX3N | TXA_D3N | CSI_D3_N | U401.3 |  | [Chưa biết] |
| ?TX3P | TXA_D3P | CSI_D3_P | U401.4 | CSI lane 3+ | [Chưa biết] |
| ?TXCN | TXA_CLKN | CSI_CLK_N | U401.9 | clock- | [Chưa biết] |
| ?TXCP | TXA_CLKP | CSI_CLK_P | U401.10 | MIPI port dùng cho CSI: clock+ | [Chưa biết] |
| ?VBUSEN | GPIO_VBUS_EN | LT_VBUS_EN | R128.1 | phương án B: điều khiển công tắc VBUS | [Chưa biết] |
| ?VCC12_TX | VCC12_TX | 1V2 | rail (10 chân) | 1.2 V MIPI TX (có thể nhiều chân) | [Chưa biết] |
| ?VCC33_TX | VCC33_TX | 3V3_LT | rail (7 chân) | 3.3 V MIPI TX (có thể nhiều chân) | [Chưa biết] |
| ?VDD | VDD | 1V2 | rail (10 chân) | chân lõi: điện áp chưa rõ (giả định 1.2 V) | [Chưa biết] |
| ?XTALI | XTALI | LT_XI | X201.1, C201.1 | thạch anh (tần số chưa rõ, giả định 25 MHz) | [Chưa biết] |
| ?XTALO | XTALO | LT_XO | X201.3, C202.1 | thạch anh | [Chưa biết] |

**U202 TPD4E05U06DQA** (TPD4E05U06DQAR; footprint `Package_SON:USON-10_2.5x1.0mm_P0.5mm`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | D1+ | SS_TX1_P | J201.A2, U201.11 |  | [Chắc] |
| 2 | D1- | SS_TX1_N | J201.A3, U201.12 |  | [Chắc] |
| 3 | GND | GND | rail (159 chân) |  | [Chắc] |
| 4 | D2+ | SS_RX1_P | J201.B11, U201.8 |  | [Chắc] |
| 5 | D2- | SS_RX1_N | J201.B10, U201.9 |  | [Chắc] |
| 6 | NC | NC | - | pad đi xuyên | [Chắc] |
| 7 | NC | NC | - |  | [Chắc] |
| 8 | GND | GND | rail (159 chân) |  | [Chắc] |
| 9 | NC | NC | - |  | [Chắc] |
| 10 | NC | NC | - |  | [Chắc] |

**U203 TPD4E05U06DQA** (TPD4E05U06DQAR; footprint `Package_SON:USON-10_2.5x1.0mm_P0.5mm`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | D1+ | SS_TX2_P | J201.B2, U201.5 |  | [Chắc] |
| 2 | D1- | SS_TX2_N | J201.B3, U201.6 |  | [Chắc] |
| 3 | GND | GND | rail (159 chân) |  | [Chắc] |
| 4 | D2+ | SS_RX2_P | J201.A11, U201.2 |  | [Chắc] |
| 5 | D2- | SS_RX2_N | J201.A10, U201.3 |  | [Chắc] |
| 6 | NC | NC | - | pad đi xuyên | [Chắc] |
| 7 | NC | NC | - |  | [Chắc] |
| 8 | GND | GND | rail (159 chân) |  | [Chắc] |
| 9 | NC | NC | - |  | [Chắc] |
| 10 | NC | NC | - |  | [Chắc] |

**U204 TPD4E05U06DQA** (TPD4E05U06DQAR; footprint `Package_SON:USON-10_2.5x1.0mm_P0.5mm`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | D1+ | PHONE_CC1 | J201.A5, U201.14, R211.1, R304.1 |  | [Chắc] |
| 2 | D1- | PHONE_CC2 | J201.B5, U201.15, R212.1, R305.1 |  | [Chắc] |
| 3 | GND | GND | rail (159 chân) |  | [Chắc] |
| 4 | D2+ | PHONE_SBU1 | J201.A8, C216.1 |  | [Chắc] |
| 5 | D2- | PHONE_SBU2 | J201.B8, C217.1 |  | [Chắc] |
| 6 | NC | NC | - | pad đi xuyên | [Chắc] |
| 7 | NC | NC | - |  | [Chắc] |
| 8 | GND | GND | rail (159 chân) |  | [Chắc] |
| 9 | NC | NC | - |  | [Chắc] |
| 10 | NC | NC | - |  | [Chắc] |

**U205 USBLC6-2SC6** (USBLC6-2SC6; footprint `Package_TO_SOT_SMD:SOT-23-6`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | I/O1 | PHONE_USB_DP | J201.A6, J201.B6, U301.59 |  | [Chắc] |
| 2 | GND | GND | rail (160 chân) |  | [Chắc] |
| 3 | I/O2 | PHONE_USB_DN | J201.A7, J201.B7, U301.58 |  | [Chắc] |
| 4 | I/O2 | PHONE_USB_DN | J201.A7, J201.B7, U301.58 |  | [Chắc] |
| 5 | VBUS | 3V3 | rail (30 chân) | nối 3V3 (tham chiếu kẹp) | [Chắc] |
| 6 | I/O1 | PHONE_USB_DP | J201.A6, J201.B6, U301.59 |  | [Chắc] |

**X201 25MHz** (-; footprint `Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm`; nguồn: -)

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | ~ | LT_XI | U201.?XTALI, C201.1 |  | [Chắc] |
| 2 | GND | GND | rail (159 chân) |  | [Chắc] |
| 3 | ~ | LT_XO | U201.?XTALO, C202.1 |  | [Chắc] |
| 4 | GND | GND | rail (159 chân) |  | [Chắc] |

Linh kiện thụ động và test point của khối:

| Ref | Giá trị | Chân 1 | Chân 2 | Footprint | Vai trò |
|---|---|---|---|---|---|
| C201 | 18pF C0G | LT_XI | GND | `C_0402_1005Metric` | Tụ tải thạch anh (chưa rõ, theo sơ đồ Lontium) [Chưa biết] |
| C202 | 18pF C0G | LT_XO | GND | `C_0402_1005Metric` | Tụ tải thạch anh [Chưa biết] |
| C203 | 100nF | 1V2 | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D pin 1 |
| C204 | 100nF | 1V2_LT_A | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D pin 4 |
| C205 | 100nF | 3V3_LT | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D pin 7 |
| C206 | 100nF | 1V2_LT_A | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D pin 10 |
| C207 | 100nF | 1V2_LT_A | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D pin 13 |
| C208 | 100nF | 3V3_LT | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D pin 16 |
| C209 | 100nF | 1V2 | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D VDD |
| C210 | 100nF | 3V3_LT | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D VCC33_TX |
| C211 | 100nF | 1V2 | GND | `C_0402_1005Metric` | Tụ lọc sát LT7911D VCC12_TX |
| C212 | 10uF 6.3V | 1V2 | GND | `C_0603_1608Metric` | Tụ khối 1V2 tại LT7911D |
| C213 | 10uF 6.3V | 1V2_LT_A | GND | `C_0603_1608Metric` | Tụ khối 1V2_LT_A |
| C214 | 10uF 6.3V | 3V3_LT | GND | `C_0603_1608Metric` | Tụ khối 3V3_LT |
| C215 | 1uF | LT_RST_N | GND | `C_0402_1005Metric` | RC reset khi bật nguồn (τ = 10 ms) |
| C216 | 100nF | PHONE_SBU1 | LT_AUX_P | `C_0402_1005Metric` | Tụ AC AUX (chưa rõ chiều/phân cực, theo sơ đồ Lontium) [Chưa biết] |
| C217 | 100nF | PHONE_SBU2 | LT_AUX_N | `C_0402_1005Metric` | Tụ AC AUX [Chưa biết] |
| C218 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ tại chân VBUS của U205 |
| C219 | 10uF 25V | PHONE_VBUS | GND | `C_0805_2012Metric` | Tụ VBUS phía iPhone (nguồn Type-C ≤ 120 µF) |
| C220 | 100nF 25V | PHONE_VBUS | GND | `C_0402_1005Metric` | Tụ cao tần VBUS iPhone |
| D201 | SMF6.0A | PHONE_VBUS | GND | `D_SOD-123F` | TVS VBUS iPhone (phương án B, VIN tới 20 V: đổi sang SMF22A) |
| FB201 | 600R@100MHz | 3V3 | 3V3_LT | `L_0603_1608Metric` | Hạt ferrite tách 3.3 V của LT7911D |
| FB202 | 600R@100MHz | 1V2 | 1V2_LT_A | `L_0603_1608Metric` | Hạt ferrite tách 1.2 V analog/PLL |
| R201 | 2.2k | LT_SCL | 3V3 | `R_0402_1005Metric` | Kéo lên I2C (bus I2C2_M0 của RV1106, 3.3 V) |
| R202 | 2.2k | LT_SDA | 3V3 | `R_0402_1005Metric` | Kéo lên I2C |
| R203 | 10k | LT_RST_N | 3V3 | `R_0402_1005Metric` | Kéo lên RST_N: LT7911D chạy ngay khi có nguồn |
| R204 **DNP** | 10k | LT_SLEEP | GND | `R_0402_1005Metric` | Tuỳ chọn mức cho SLEEP_33 (chưa rõ) [Chưa biết] |
| R205 | 100k | LT_INT | GND | `R_0402_1005Metric` | Giữ LT_INT ở mức thấp khi LT7911D đang reset |
| R206 **DNP** | 1M | LT_AUX_P | GND | `R_0402_1005Metric` | Phân cực AUX phía sink (chưa rõ) [Chưa biết] |
| R207 **DNP** | 1M | LT_AUX_N | 3V3_LT | `R_0402_1005Metric` | Phân cực AUX phía sink (chưa rõ) [Chưa biết] |
| R210 | 10k | PHONE_VBUS | GND | `R_0603_1608Metric` | Xả VBUS về vSafe0V (< 0.8 V trong ~0.4 s) |
| R211 **DNP** | 5.1k | PHONE_CC1 | GND | `R_0402_1005Metric` | Rd tạm cho bring-up chế độ chỉ HID (chưa hàn LT7911D): iPhone thành nguồn + host |
| R212 **DNP** | 5.1k | PHONE_CC2 | GND | `R_0402_1005Metric` | Rd tạm cho bring-up chế độ chỉ HID |
| TP201 | LT_SLEEP | LT_SLEEP | - | `TestPoint_Pad_D1.5mm` | Đo/ép SLEEP_33 |
| TP202 | LT_RX_HPD | LT_RX_HPD | - | `TestPoint_Pad_D1.5mm` | Đo RX_HPD |
<!-- END GENERATED: pins-iphone -->

### 4.3 CH32V305RBT6

Số chân theo cột LQFP64M của bảng 3-1 và hình 3.1.2 (CH32V305RBT6) trong datasheet WCH V3.9 **[Chắc]**.

- **USB HS tới iPhone:** PB6 = USBHS_DM (pad 58), PB7 = USBHS_DP (pad 59), PHY nội, nối thẳng qua U205, không
  điện trở nối tiếp.
- **USB FS thứ hai:** PA11/PA12 = OTG_FS_DM/DP (pad 44/45) ra header J502 (ISP qua USB, thử nghiệm).
- **Link tới RV1106:** SPI1 slave (PA4 NSS, PA5 SCK, PA6 MISO, PA7 MOSI) ↔ SPI0_M0 của RV1106; USART1 (PA9 TX,
  PA10 RX) ↔ UART4_M0. USART1 cũng là cổng bootloader ISP, nên RV1106 nạp được firmware MCU bằng cách kéo BOOT0 lên
  (GPIO1_D2) rồi reset (GPIO1_D3). PB0 = MCU_IRQ báo sự kiện cho RV1106.
- **ADC:** PA0/PA1 = áp CC1/CC2 phía iPhone, PA2 = VBUS iPhone, PA3 = dòng sạc. VDDA lọc bằng FB301.
- **LED:** PC6/PC7/PC8 = TIM8_CH1/2/3 (PWM) cho LED đỏ/vàng/xanh.
- **Debug:** PA13 SWDIO, PA14 SWCLK (WCH-LinkE), PB10/PB11 = USART3 log.
- **Nguồn:** VDD_4 (19), VDD_2 (48), VIO_1 (32), VIO_3 (64), VBAT (1) nối 3V3, mỗi chân 100 nF (hình 4-1-1);
  VDDA (13) 100 nF + 1 µF; thêm 10 µF khối. VSSA (12), VSS_1/2/3/4 (31, 47, 63, 18) nối GND.

<!-- BEGIN GENERATED: pins-mcu -->
**U301 CH32V305RBT6** (CH32V305RBT6; footprint `Package_QFP:LQFP-64_10x10mm_P0.5mm`; nguồn: [CH32DS](https://raw.githubusercontent.com/ch32-riscv-ug/CH32V307/main/datasheet_en/CH32V20x_30xDS0.PDF))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | VBAT | 3V3 | rail (26 chân) | không dùng RTC dự phòng | [Chắc] |
| 2 | PC13 | NC | - |  | [Chắc] |
| 3 | PC14/OSC32_IN | NC | - |  | [Chắc] |
| 4 | PC15/OSC32_OUT | NC | - |  | [Chắc] |
| 5 | OSC_IN/PD0 | HSE_IN | X301.1, C301.1 | HSE 8 MHz (USBHS PLL cần 4 MHz = HSE/2) | [Chắc] |
| 6 | OSC_OUT/PD1 | HSE_OUT | X301.3, C302.1 |  | [Chắc] |
| 7 | NRST | MCU_NRST | R301.1, C311.1, R413.2, J501.5 | RC 10k/100nF + RV1106 + SWD | [Chắc] |
| 8 | PC0 | NC | - |  | [Chắc] |
| 9 | PC1 | NC | - |  | [Chắc] |
| 10 | PC2 | NC | - |  | [Chắc] |
| 11 | PC3 | NC | - |  | [Chắc] |
| 12 | VSSA | GND | rail (156 chân) |  | [Chắc] |
| 13 | VDDA | VDDA_MCU | C308.1, C309.1, FB301.2 | phải bằng VIO (§2.5.3) | [Chắc] |
| 14 | PA0/ADC0 | CC1_SENSE | R304.2, C312.1 | ADC: áp CC1 phía iPhone (qua 100k) | [Chắc] |
| 15 | PA1/ADC1 | CC2_SENSE | R305.2, C313.1 | ADC: áp CC2 | [Chắc] |
| 16 | PA2/ADC2 | PHONE_VBUS_SENSE | R306.2, R307.1, C314.1 | ADC: VBUS iPhone / 7.67 | [Chắc] |
| 17 | PA3/ADC3 | PHONE_ISENSE | U106.1, C130.1 | ADC: dòng sạc 0.5 V/A | [Chắc] |
| 18 | VSS_4 | GND | rail (156 chân) |  | [Chắc] |
| 19 | VDD_4 | 3V3 | rail (26 chân) |  | [Chắc] |
| 20 | PA4/SPI1_NSS | SPI_CS | U401.101 | SPI slave từ RV1106 | [Chắc] |
| 21 | PA5/SPI1_SCK | SPI_SCK | U401.100 |  | [Chắc] |
| 22 | PA6/SPI1_MISO | SPI_MISO | U401.98 |  | [Chắc] |
| 23 | PA7/SPI1_MOSI | SPI_MOSI | U401.99 |  | [Chắc] |
| 24 | PC4 | NC | - |  | [Chắc] |
| 25 | PC5 | NC | - |  | [Chắc] |
| 26 | PB0 | MCU_IRQ | U401.92 | báo sự kiện cho RV1106 | [Chắc] |
| 27 | PB1 | MCU_VBUS_EN | R127.1 | bật VBUS iPhone (qua R127) | [Chắc] |
| 28 | PB2/BOOT1 | MCU_BOOT1 | R303.1 | 10k xuống GND | [Chắc] |
| 29 | PB10/USART3_TX | MCU_DBG_TX | J501.6 | log debug của MCU | [Chắc] |
| 30 | PB11/USART3_RX | MCU_DBG_RX | J501.7 |  | [Chắc] |
| 31 | VSS_1 | GND | rail (156 chân) |  | [Chắc] |
| 32 | VIO_1 | 3V3 | rail (26 chân) |  | [Chắc] |
| 33 | PB12 | PD_PG | U101.10, R108.1 | PG của CH224K (thấp = PD đã thương lượng) | [Chắc] |
| 34 | PB13 | NC | - |  | [Chắc] |
| 35 | PB14 | NC | - |  | [Chắc] |
| 36 | PB15 | NC | - |  | [Chắc] |
| 37 | PC6/TIM8_CH1 | LED_R | R501.1 | LED đỏ (PWM) | [Chắc] |
| 38 | PC7/TIM8_CH2 | LED_Y | R502.1 | LED vàng (PWM) | [Chắc] |
| 39 | PC8/TIM8_CH3 | LED_G | R503.1 | LED xanh (PWM) | [Chắc] |
| 40 | PC9 | NC | - |  | [Chắc] |
| 41 | PA8 | NC | - |  | [Chắc] |
| 42 | PA9/USART1_TX | MCU_UART_TX | U401.70 | UART link + ISP bootloader | [Chắc] |
| 43 | PA10/USART1_RX | MCU_UART_RX | U401.71 | UART link + ISP bootloader | [Chắc] |
| 44 | PA11/OTG_FS_DM | MCU_FS_DN | J502.2 |  | [Chắc] |
| 45 | PA12/OTG_FS_DP | MCU_FS_DP | J502.1 | USB FS thứ hai -> header J502 | [Chắc] |
| 46 | PA13/SWDIO | SWDIO | J501.2 | WCH-LinkE | [Chắc] |
| 47 | VSS_2 | GND | rail (156 chân) |  | [Chắc] |
| 48 | VDD_2 | 3V3 | rail (26 chân) |  | [Chắc] |
| 49 | PA14/SWCLK | SWCLK | J501.3 | WCH-LinkE | [Chắc] |
| 50 | PA15 | NC | - |  | [Chắc] |
| 51 | PC10 | NC | - |  | [Chắc] |
| 52 | PC11 | NC | - |  | [Chắc] |
| 53 | PC12 | NC | - |  | [Chắc] |
| 54 | PD2 | NC | - |  | [Chắc] |
| 55 | PB3 | NC | - |  | [Chắc] |
| 56 | PB4 | NC | - |  | [Chắc] |
| 57 | PB5 | NC | - |  | [Chắc] |
| 58 | PB6/USBHS_DM | PHONE_USB_DN | J201.A7, J201.B7, U205.3, U205.4 |  | [Chắc] |
| 59 | PB7/USBHS_DP | PHONE_USB_DP | J201.A6, J201.B6, U205.1, U205.6 | USB 2.0 HS tới iPhone (PHY nội) | [Chắc] |
| 60 | BOOT0 | MCU_BOOT0 | R302.1, U401.91 | 10k xuống GND; RV1106 kéo lên để vào bootloader ISP | [Chắc] |
| 61 | PB8 | NC | - |  | [Chắc] |
| 62 | PB9 | NC | - |  | [Chắc] |
| 63 | VSS_3 | GND | rail (156 chân) |  | [Chắc] |
| 64 | VIO_3 | 3V3 | rail (26 chân) |  | [Chắc] |

**X301 8MHz** (-; footprint `Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm`; nguồn: [CH32EVT](https://github.com/openwch/ch32v307))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | ~ | HSE_IN | U301.5, C301.1 |  | [Chắc] |
| 2 | GND | GND | rail (159 chân) |  | [Chắc] |
| 3 | ~ | HSE_OUT | U301.6, C302.1 |  | [Chắc] |
| 4 | GND | GND | rail (159 chân) |  | [Chắc] |

Linh kiện thụ động và test point của khối:

| Ref | Giá trị | Chân 1 | Chân 2 | Footprint | Vai trò |
|---|---|---|---|---|---|
| C301 | 18pF C0G | HSE_IN | GND | `C_0402_1005Metric` | Tụ tải HSE: 2 x (12 pF - ~3 pF ký sinh) |
| C302 | 18pF C0G | HSE_OUT | GND | `C_0402_1005Metric` | Tụ tải HSE |
| C303 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ lọc VBAT pin 1 (datasheet Figure 4-1-1) |
| C304 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ lọc VDD_4 pin 19 (datasheet Figure 4-1-1) |
| C305 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ lọc VIO_1 pin 32 (datasheet Figure 4-1-1) |
| C306 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ lọc VDD_2 pin 48 (datasheet Figure 4-1-1) |
| C307 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ lọc VIO_3 pin 64 (datasheet Figure 4-1-1) |
| C308 | 100nF | VDDA_MCU | GND | `C_0402_1005Metric` | Tụ lọc VDDA pin 13 (datasheet Figure 4-1-1) |
| C309 | 1uF | VDDA_MCU | GND | `C_0402_1005Metric` | Tụ VDDA thêm cho ADC |
| C310 | 10uF 6.3V | 3V3 | GND | `C_0603_1608Metric` | Tụ khối 3V3 tại MCU |
| C311 | 100nF | MCU_NRST | GND | `C_0402_1005Metric` | Tụ NRST |
| C312 | 1nF | CC1_SENSE | GND | `C_0402_1005Metric` | Lọc ADC CC1 |
| C313 | 1nF | CC2_SENSE | GND | `C_0402_1005Metric` | Lọc ADC CC2 |
| C314 | 10nF | PHONE_VBUS_SENSE | GND | `C_0402_1005Metric` | Lọc ADC VBUS |
| FB301 | 600R@100MHz | 3V3 | VDDA_MCU | `L_0603_1608Metric` | Ferrite tách VDDA (VDDA = VIO về DC) |
| R301 | 4.7k | MCU_NRST | 3V3 | `R_0402_1005Metric` | Kéo lên NRST, đủ mạnh để thắng pull-down mặc định của GPIO1_D3 (RV1106) qua R413 |
| R302 | 10k | MCU_BOOT0 | GND | `R_0402_1005Metric` | BOOT0 = 0: chạy từ flash |
| R303 | 10k | MCU_BOOT1 | GND | `R_0402_1005Metric` | BOOT1 = 0: BOOT0 = 1 thì vào system memory (ISP) |
| R304 | 100k | PHONE_CC1 | CC1_SENSE | `R_0402_1005Metric` | Đo áp CC1, trở kháng cao để không tải CC |
| R305 | 100k | PHONE_CC2 | CC2_SENSE | `R_0402_1005Metric` | Đo áp CC2 |
| R306 | 100k | PHONE_VBUS | PHONE_VBUS_SENSE | `R_0402_1005Metric` | Chia áp VBUS iPhone (20 V -> 2.6 V) |
| R307 | 15k | PHONE_VBUS_SENSE | GND | `R_0402_1005Metric` | Chia áp VBUS iPhone |
<!-- END GENERATED: pins-mcu -->

### 4.4 Luckfox Core1106 + Ethernet + USB-C tới PC

Tên pad, miền điện áp IO và ghi chú lấy từ `Core1106-PinOut.xls` và sơ đồ `Core1106.pdf` của Luckfox **[Chắc]**.
Module 30×30 mm, 112 pad bước 1.0 mm, pad 0.7×1.5 mm nằm giữa mép module, pad 1 ở góc trên trái, đánh số ngược
chiều kim đồng hồ (theo footprint KiCad `Core1106-SMT` của Luckfox).

- **CSI:** pad 3–12 (D3N … D0P, CK0N/CK0P) cho 4 lane; RV1106 gộp hai D-PHY 2 lane thành một cổng 4 lane
  (`csi2_dphy0` "full mode" trong `rv1106.dtsi`) **[Chắc]**; clock lane ở chế độ này là CK0 **[Có thể]**. CK1 (pad
  1/2) để trống. Pad 1–20 thuộc miền 1.8 V; không nối tín hiệu 3.3 V vào đây.
- **USB:** pad 22/23 = USB_N/USB_P ra J401; pad 24 USB_VBUSDET = VBUS PC chia 10k/18k (như Luckfox Pico Ultra).
  Box là thiết bị USB (Rd 5.1 kΩ trên CC).
- **Ethernet:** pad 85–88 = FEPHY_RXN/RXP/TXN/TXP; PHY 100M trong RV1106, điện trở REXT 6.04 kΩ đã có trên module.
  Nối theo mẫu Luckfox: 0 Ω nối tiếp, center tap mỗi bên 10 nF xuống GND, chân 8 (nút Bob-Smith) 1 nF 100 V xuống
  GND, vỏ nối GND. LED của J402 để trống vì Core1106 không đưa chân LED của PHY ra.
- **I2C tới LT7911D:** pad 65/66 = I2C2_SCL_M0/I2C2_SDA_M0, miền 3.3 V, kéo lên 2.2 kΩ. Ngắt LT_INT vào pad 67
  (GPIO1_A2); reset LT_RST_N từ pad 61 (GPIO0_A3, mặc định pull-up).
- **Link tới MCU:** SPI0_M0 (pad 98 MISO, 99 MOSI, 100 CLK, 101 CS0), UART4_M0 (pad 70 RX, 71 TX), GPIO1_D1 (92)
  nhận MCU_IRQ, GPIO1_D2 (91) lái BOOT0, GPIO1_D3 (90) lái NRST qua R413 1 kΩ. GPIO1_D3 mặc định có pull-down
  yếu, nên NRST kéo lên bằng 4.7 kΩ (R301) để MCU không bị giữ reset khi SoC khởi động; device tree nên đặt chân
  này là output-high hoặc open-drain sớm.
- **Console:** UART2_M1 (pad 72 TX, 73 RX), fiq-debugger `serial-id = 2`, 115200 baud **[Chắc]** (DTS Luckfox).
- **Pad bị ngắt tuỳ bản module:** bản eMMC ngắt pad 37–46; bản Wi-Fi dùng SDMMC (pad 48–54) và UART BT (63–64,
  68–69). Tất cả để trống. Chọn bản **RV1106G3 + eMMC 8 GB + Wi-Fi 6/BT 5.2** để có Wi-Fi mà không cần module SDIO
  riêng; có bản Wi-Fi là **[Có thể]** (sơ đồ Core1106 có khối Wi-Fi và anten ANT1; tin tức hãng).
- **ADC:** SARADC_IN0 (pad 26) là phím RECOVERY, luôn kéo lên 1.8 V (10 kΩ tới VCC_1V8 của module, 1 nF, 100 Ω tới
  phím), đúng theo ghi chú "SARADC_IN0 must always be pulled-up" của Luckfox **[Chắc]**. SARADC_IN1 (pad 27) đo
  VIN qua 100k/8.2k (20 V → 1.52 V, dải ADC 1.8 V).
- **Nguồn:** VCC5V0_SYS (pad 79–81) 4.6–5.2 V, khuyến nghị cấp 1 A **[Chắc]**; VCC_1V8/VCC_3V3 (77/78) là đầu ra,
  tối đa 300 mA mỗi rail. VCC3V3_RTC (76) để trống (module tự cấp từ VCC_3V3 qua diode).

Đoạn device tree gợi ý cho LT7911D trên RV1106 (chỉ tên thuộc tính theo driver BSP, giá trị theo mạch này):

```dts
&i2c2 {                                   /* i2c2m0: pad 65 SCL, pad 66 SDA */
	status = "okay";
	clock-frequency = <400000>;
	lt7911d@2b {
		compatible = "lontium,lt7911d";
		reg = <0x2b>;
		clocks = <&cru MCLK_REF_MIPI0>;   /* như camera của Luckfox; chỉ để thoả driver */
		clock-names = "xvclk";
		interrupt-parent = <&gpio1>;
		interrupts = <RK_PA2 IRQ_TYPE_EDGE_RISING>;       /* pad 67 */
		reset-gpios = <&gpio0 RK_PA3 GPIO_ACTIVE_LOW>;    /* pad 61 */
		rockchip,camera-module-index = <0>;
		rockchip,camera-module-facing = "back";
		rockchip,camera-module-name = "LT7911D";
		rockchip,camera-module-lens-name = "NC";
		port { lt7911d_out: endpoint { remote-endpoint = <&csi_dphy_input0>; data-lanes = <1 2 3 4>; }; };
	};
};
```

<!-- BEGIN GENERATED: pins-soc -->
**J401 USB-C PC** (TYPE-C-31-M-12; footprint `Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| A1 | GND | GND | rail (156 chân) |  | [Chắc] |
| A4 | VBUS | PC_VBUS | JP102.1, C404.1, D401.1, R403.1 |  | [Chắc] |
| A5 | CC1 | PC_CC1 | R401.1 |  | [Chắc] |
| A6 | D+ | PC_USB_DP | U401.23, U402.1, U402.6 |  | [Chắc] |
| A7 | D- | PC_USB_DN | U401.22, U402.3, U402.4 |  | [Chắc] |
| A8 | SBU1 | NC | - |  | [Chắc] |
| A9 | VBUS | PC_VBUS | JP102.1, C404.1, D401.1, R403.1 |  | [Chắc] |
| A12 | GND | GND | rail (156 chân) |  | [Chắc] |
| B1 | GND | GND | rail (156 chân) |  | [Chắc] |
| B4 | VBUS | PC_VBUS | JP102.1, C404.1, D401.1, R403.1 |  | [Chắc] |
| B5 | CC2 | PC_CC2 | R402.1 |  | [Chắc] |
| B6 | D+ | PC_USB_DP | U401.23, U402.1, U402.6 |  | [Chắc] |
| B7 | D- | PC_USB_DN | U401.22, U402.3, U402.4 |  | [Chắc] |
| B8 | SBU2 | NC | - |  | [Chắc] |
| B9 | VBUS | PC_VBUS | JP102.1, C404.1, D401.1, R403.1 |  | [Chắc] |
| B12 | GND | GND | rail (156 chân) |  | [Chắc] |
| S1 | SHIELD | GND | rail (156 chân) |  | [Chắc] |

**J402 RJ45 10/100** (HR911105A; footprint `Connector_RJ:RJ45_Hanrun_HR911105A_Horizontal`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | TD+ | ETH_TXP_J | R405.2 |  | [Chắc] |
| 2 | TD- | ETH_TXN_J | R406.2 |  | [Chắc] |
| 3 | RD+ | ETH_RXP_J | R407.2 |  | [Chắc] |
| 4 | TCT | ETH_TCT | C406.1 |  | [Chắc] |
| 5 | RCT | ETH_RCT | C407.1 |  | [Chắc] |
| 6 | RD- | ETH_RXN_J | R408.2 |  | [Chắc] |
| 7 | NC | NC | - |  | [Chắc] |
| 8 | BS | ETH_BS | C408.1 | nút Bob-Smith (1 nF/2 kV trong jack) [Có thể] | [Có thể] |
| 9 | LED1 | NC | - | LED: Core1106 không đưa chân LED của PHY ra | [Chưa biết] |
| 10 | LED1 | NC | - |  | [Chưa biết] |
| 11 | LED2 | NC | - |  | [Chưa biết] |
| 12 | LED2 | NC | - |  | [Chưa biết] |
| SH | SHIELD | GND | rail (160 chân) |  | [Chắc] |

**U401 Luckfox Core1106** (Luckfox Core1106 (RV1106G3, 256 MB, eMMC 8 GB, Wi-Fi 6/BT 5.2); footprint `box-v1:Luckfox_Core1106_Castellated_30x30mm_P1.0mm`; nguồn: [CORE1106_XLS](https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106-PinOut.xls))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | MIPI_CSI_RX_CK1N/GPI3_B2 | NC | - | CK1 chỉ dùng ở chế độ 2x2 lane | [Chắc] |
| 2 | MIPI_CSI_RX_CK1P/GPI3_B3 | NC | - | CK1 chỉ dùng ở chế độ 2x2 lane | [Chắc] |
| 3 | MIPI_CSI_RX_D3N/GPI3_B0 | CSI_D3_N | U201.?TX3N | 4-lane: D0-D3 + CK0 | [Chắc] |
| 4 | MIPI_CSI_RX_D3P/GPI3_B1 | CSI_D3_P | U201.?TX3P |  | [Chắc] |
| 5 | MIPI_CSI_RX_D2N/GPI3_B4 | CSI_D2_N | U201.?TX2N |  | [Chắc] |
| 6 | MIPI_CSI_RX_D2P/GPI3_B5 | CSI_D2_P | U201.?TX2P |  | [Chắc] |
| 7 | MIPI_CSI_RX_D1N/GPI3_B6 | CSI_D1_N | U201.?TX1N |  | [Chắc] |
| 8 | MIPI_CSI_RX_D1P/GPI3_B7 | CSI_D1_P | U201.?TX1P |  | [Chắc] |
| 9 | MIPI_CSI_RX_CK0N/GPI3_C0 | CSI_CLK_N | U201.?TXCN | clock lane CK0 khi gộp 2 D-PHY thành 4 lane [Có thể] | [Chắc] |
| 10 | MIPI_CSI_RX_CK0P/GPI3_C1 | CSI_CLK_P | U201.?TXCP |  | [Chắc] |
| 11 | MIPI_CSI_RX_D0N/GPI3_C2 | CSI_D0_N | U201.?TX0N |  | [Chắc] |
| 12 | MIPI_CSI_RX_D0P/GPI3_C3 | CSI_D0_P | U201.?TX0P |  | [Chắc] |
| 13 | PWM1_M2/GPIO3_D3 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 14 | I2C3_SDA_M2/GPIO3_D2 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 15 | I2C3_SCL_M2/GPIO3_D1 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 16 | I2C4_SCL_M2/GPIO3_C7 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 17 | I2C4_SDA_M2/GPIO3_D0 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 18 | VI_CIF_VSYNC/GPIO3_C5 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 19 | MIPI_CLK0_OUT/GPIO3_C4 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 20 | MIPI_CLK1_OUT/GPIO3_C6 (1V8) | NC | - | miền 1.8 V, không dùng | [Chắc] |
| 21 | GND | GND | rail (146 chân) |  | [Chắc] |
| 22 | USB_N | PC_USB_DN | J401.A7, J401.B7, U402.3, U402.4 | USB 2.0 OTG -> PC | [Chắc] |
| 23 | USB_P | PC_USB_DP | J401.A6, J401.B6, U402.1, U402.6 |  | [Chắc] |
| 24 | USB_VBUSDET | PC_VBUS_DET | R403.2, R404.1, C405.1 | VBUS PC qua 10k/18k (như Luckfox Pico Ultra) | [Chắc] |
| 25 | GND | GND | rail (146 chân) |  | [Chắc] |
| 26 | SARADC_IN0/GPIO4_C0 | SOC_RECOVERY | R411.1, C410.1, R412.1 | phím RECOVERY, luôn kéo lên 1.8 V | [Chắc] |
| 27 | SARADC_IN1/GPIO4_C1 | VIN_SENSE | R409.2, R410.1, C409.1 | đo VIN (ADC 1.8 V): VIN x 8.2/108.2 | [Chắc] |
| 28 | GND | GND | rail (146 chân) |  | [Chắc] |
| 29 | GND | GND | rail (146 chân) |  | [Chắc] |
| 30 | CODEC_LINEOUT | NC | - | codec không dùng | [Chắc] |
| 31 | CODEC_MICBIAS | NC | - | codec không dùng | [Chắc] |
| 32 | CODEC_MIC0N | NC | - | codec không dùng | [Chắc] |
| 33 | CODEC_MIC0P | NC | - | codec không dùng | [Chắc] |
| 34 | CODEC_MIC1N | NC | - | codec không dùng | [Chắc] |
| 35 | CODEC_MIC1P | NC | - | codec không dùng | [Chắc] |
| 36 | GND | GND | rail (146 chân) |  | [Chắc] |
| 37 | EMMC_D0/GPIO4_A4 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 38 | EMMC_D1/GPIO4_A3 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 39 | EMMC_D2/GPIO4_A2 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 40 | EMMC_D3/GPIO4_A6 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 41 | EMMC_D4/GPIO4_A5 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 42 | EMMC_D5/GPIO4_A7 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 43 | EMMC_D6/GPIO4_A1 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 44 | EMMC_D7/GPIO4_A0 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 45 | EMMC_CMD/GPIO4_B0 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 46 | EMMC_CLK/GPIO4_B1 | NC | - | bản eMMC: pad bị ngắt trên module | [Chắc] |
| 47 | GND | GND | rail (146 chân) |  | [Chắc] |
| 48 | SDMMC_DET/GPIO3_A1 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 49 | SDMMC_D0/GPIO3_A3 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 50 | SDMMC_D1/GPIO3_A2 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 51 | SDMMC_D2/GPIO3_A7 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 52 | SDMMC_D3/GPIO3_A6 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 53 | SDMMC_CMD/GPIO3_A5 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 54 | SDMMC_CLK/GPIO3_A4 | NC | - | bản Wi-Fi: SDMMC nối vào Wi-Fi trên module | [Chắc] |
| 55 | GND | GND | rail (146 chân) |  | [Chắc] |
| 56 | GND | GND | rail (146 chân) |  | [Chắc] |
| 57 | GND | GND | rail (146 chân) |  | [Chắc] |
| 58 | UART0_RX_M0/GPIO0_A0 | NC | - | không dùng | [Chắc] |
| 59 | UART0_TX_M0/GPIO0_A1 | NC | - | không dùng | [Chắc] |
| 60 | PWM3_IR_M0/GPIO0_A2 | NC | - | không dùng | [Chắc] |
| 61 | PWR_CTRL_M1/GPIO0_A3 | LT_RST_N | U201.20, R203.1, C215.1 | reset LT7911D (reset-gpios, active low); GPIO0_A3 mặc định kéo lên: LT7911D chạy ngay khi cấp nguồn | [Chắc] |
| 62 | PWR_CTRL_M0/GPIO0_A4 | NC | - | không dùng | [Chắc] |
| 63 | I2C1_SCL_M0/GPIO0_A5 | NC | - | bản Wi-Fi: dùng cho BT UART | [Chắc] |
| 64 | I2C1_SDA_M0/GPIO0_A6 | NC | - | bản Wi-Fi: dùng cho BT UART | [Chắc] |
| 65 | I2C2_SCL_M0/UART3_TX_M0/GPIO1_A0 | LT_SCL | U201.21, R201.1 | I2C2_M0 -> LT7911D (3.3 V) | [Chắc] |
| 66 | I2C2_SDA_M0/UART3_RX_M0/GPIO1_A1 | LT_SDA | U201.22, R202.1 |  | [Chắc] |
| 67 | PWM0_M0/GPIO1_A2 | LT_INT | U201.?INT, R205.1 | ngắt từ LT7911D (IRQ cạnh lên) | [Chắc] |
| 68 | UART1_TX_M0/GPIO1_A3 | NC | - | bản Wi-Fi: dùng cho BT UART | [Chắc] |
| 69 | UART1_RX_M0/GPIO1_A4 | NC | - | bản Wi-Fi: dùng cho BT UART | [Chắc] |
| 70 | UART4_RX_M0/GPIO1_B0 | MCU_UART_TX | U301.42 | UART4_M0 RX <- CH32 USART1 TX | [Chắc] |
| 71 | UART4_TX_M0/GPIO1_B1 | MCU_UART_RX | U301.43 | UART4_M0 TX -> CH32 USART1 RX | [Chắc] |
| 72 | UART2_TX_M1/GPIO1_B2 | SOC_CON_TX | J503.2 | console UART2_M1 (fiq-debugger) -> J503 | [Chắc] |
| 73 | UART2_RX_M1/GPIO1_B3 | SOC_CON_RX | J503.3 | console RX | [Chắc] |
| 74 | NPOR | SOC_NPOR | SW502.1 | reset RV1106 (nút SW502) | [Chắc] |
| 75 | GND | GND | rail (146 chân) |  | [Chắc] |
| 76 | VCC3V3_RTC | NC | - | RTC lấy từ VCC_3V3 qua diode trên module; để trống | [Chắc] |
| 77 | VCC_1V8 | VCC_1V8_MOD | R411.2 | 1.8 V ra từ module | [Chắc] |
| 78 | VCC_3V3 | VCC_3V3_MOD | TP401.1 | 3.3 V ra, chỉ TP | [Chắc] |
| 79 | VCC5V0_SYS | 5V_SYS | rail (16 chân) | 4.6-5.2 V, ≤ 1 A | [Chắc] |
| 80 | VCC5V0_SYS | 5V_SYS | rail (16 chân) |  | [Chắc] |
| 81 | VCC5V0_SYS | 5V_SYS | rail (16 chân) |  | [Chắc] |
| 82 | GND | GND | rail (146 chân) |  | [Chắc] |
| 83 | GND | GND | rail (146 chân) |  | [Chắc] |
| 84 | GND | GND | rail (146 chân) |  | [Chắc] |
| 85 | FEPHY_RXN | ETH_RX_N | R408.1 | PHY 100M trong RV1106 | [Chắc] |
| 86 | FEPHY_RXP | ETH_RX_P | R407.1 |  | [Chắc] |
| 87 | FEPHY_TXN | ETH_TX_N | R406.1 |  | [Chắc] |
| 88 | FEPHY_TXP | ETH_TX_P | R405.1 |  | [Chắc] |
| 89 | GND | GND | rail (146 chân) |  | [Chắc] |
| 90 | GPIO1_D3 | SOC_MCU_RST | R413.1 | reset CH32 qua R413 1k; GPIO1_D3 mặc định kéo xuống yếu, R301 4.7k thắng | [Chắc] |
| 91 | GPIO1_D2 | MCU_BOOT0 | U301.60, R302.1 | kéo lên để CH32 vào bootloader USART1 | [Chắc] |
| 92 | GPIO1_D1 | MCU_IRQ | U301.26 | ngắt từ CH32 | [Chắc] |
| 93 | GPIO1_D0 | NC | - | không dùng | [Chắc] |
| 94 | GPIO1_C7 | NC | - | không dùng | [Chắc] |
| 95 | GPIO1_C6 | NC | - | không dùng | [Chắc] |
| 96 | GPIO1_C5 | NC | - | không dùng | [Chắc] |
| 97 | GPIO1_C4 | NC | - | không dùng | [Chắc] |
| 98 | SPI0_MISO_M0/GPIO1_C3 | SPI_MISO | U301.22 | SPI0_M0 master <- CH32 SPI1 | [Chắc] |
| 99 | SPI0_MOSI_M0/GPIO1_C2 | SPI_MOSI | U301.23 |  | [Chắc] |
| 100 | SPI0_CLK_M0/GPIO1_C1 | SPI_SCK | U301.21 |  | [Chắc] |
| 101 | SPI0_CS0_M0/GPIO1_C0 | SPI_CS | U301.20 |  | [Chắc] |
| 102 | GPIO2_A0 | NC | - | không dùng | [Chắc] |
| 103 | GPIO2_A1 | NC | - | không dùng | [Chắc] |
| 104 | GPIO2_A2 | NC | - | không dùng | [Chắc] |
| 105 | GPIO2_A3 | NC | - | không dùng | [Chắc] |
| 106 | GPIO2_A4 | NC | - | không dùng | [Chắc] |
| 107 | GPIO2_A5 | NC | - | không dùng | [Chắc] |
| 108 | GPIO2_A6 | NC | - | không dùng | [Chắc] |
| 109 | GPIO2_A7 | NC | - | không dùng | [Chắc] |
| 110 | GPIO2_B0 | NC | - | không dùng | [Chắc] |
| 111 | GPIO2_B1 | NC | - | không dùng | [Chắc] |
| 112 | GND | GND | rail (146 chân) |  | [Chắc] |

**U402 USBLC6-2SC6** (USBLC6-2SC6; footprint `Package_TO_SOT_SMD:SOT-23-6`; nguồn: [KICAD_SYM](https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | I/O1 | PC_USB_DP | U401.23, J401.A6, J401.B6 |  | [Chắc] |
| 2 | GND | GND | rail (160 chân) |  | [Chắc] |
| 3 | I/O2 | PC_USB_DN | U401.22, J401.A7, J401.B7 |  | [Chắc] |
| 4 | I/O2 | PC_USB_DN | U401.22, J401.A7, J401.B7 |  | [Chắc] |
| 5 | VBUS | 3V3 | rail (30 chân) |  | [Chắc] |
| 6 | I/O1 | PC_USB_DP | U401.23, J401.A6, J401.B6 |  | [Chắc] |

Linh kiện thụ động và test point của khối:

| Ref | Giá trị | Chân 1 | Chân 2 | Footprint | Vai trò |
|---|---|---|---|---|---|
| C401 | 22uF 10V | 5V_SYS | GND | `C_0805_2012Metric` | Tụ khối VCC5V0_SYS sát module |
| C402 | 100nF | 5V_SYS | GND | `C_0402_1005Metric` | Tụ cao tần VCC5V0_SYS |
| C403 | 100nF | 3V3 | GND | `C_0402_1005Metric` | Tụ tại chân VBUS của U402 |
| C404 | 1uF 25V | PC_VBUS | GND | `C_0603_1608Metric` | Tụ VBUS phía PC (UFP ≤ 10 µF) |
| C405 | 100nF | PC_VBUS_DET | GND | `C_0402_1005Metric` | Lọc VBUSDET |
| C406 | 10nF 50V | ETH_TCT | GND | `C_0402_1005Metric` | Tụ center tap TX (PHY kiểu voltage-mode) |
| C407 | 10nF 50V | ETH_RCT | GND | `C_0402_1005Metric` | Tụ center tap RX |
| C408 | 1nF 100V | ETH_BS | GND | `C_0603_1608Metric` | Tụ nút Bob-Smith |
| C409 | 10nF | VIN_SENSE | GND | `C_0402_1005Metric` | Lọc ADC VIN |
| C410 | 1nF C0G | SOC_RECOVERY | GND | `C_0402_1005Metric` | Lọc phím RECOVERY |
| D401 | SMF6.0A | PC_VBUS | GND | `D_SOD-123F` | TVS VBUS PC |
| R401 | 5.1k 1% | PC_CC1 | GND | `R_0402_1005Metric` | Rd: box là UFP (sink) phía PC |
| R402 | 5.1k 1% | PC_CC2 | GND | `R_0402_1005Metric` | Rd |
| R403 | 10k | PC_VBUS | PC_VBUS_DET | `R_0402_1005Metric` | Chia áp VBUS -> USB_VBUSDET (5 V -> 3.2 V) |
| R404 | 18k | PC_VBUS_DET | GND | `R_0402_1005Metric` | Chia áp VBUSDET |
| R405 | 0R | ETH_TX_P | ETH_TXP_J | `R_0402_1005Metric` | 0R như thiết kế Luckfox: chỗ để thêm lọc/ESD |
| R406 | 0R | ETH_TX_N | ETH_TXN_J | `R_0402_1005Metric` | 0R như thiết kế Luckfox: chỗ để thêm lọc/ESD |
| R407 | 0R | ETH_RX_P | ETH_RXP_J | `R_0402_1005Metric` | 0R như thiết kế Luckfox: chỗ để thêm lọc/ESD |
| R408 | 0R | ETH_RX_N | ETH_RXN_J | `R_0402_1005Metric` | 0R như thiết kế Luckfox: chỗ để thêm lọc/ESD |
| R409 | 100k 1% | VIN | VIN_SENSE | `R_0402_1005Metric` | Chia áp VIN cho SARADC_IN1 (20 V -> 1.52 V) |
| R410 | 8.2k 1% | VIN_SENSE | GND | `R_0402_1005Metric` | Chia áp VIN |
| R411 | 10k | SOC_RECOVERY | VCC_1V8_MOD | `R_0402_1005Metric` | Kéo lên SARADC_IN0 (bắt buộc) |
| R412 | 100R | SOC_RECOVERY | RECOVERY_KEY | `R_0402_1005Metric` | Nối tiếp phím RECOVERY |
| R413 | 1k | SOC_MCU_RST | MCU_NRST | `R_0402_1005Metric` | Hạn dòng khi RV1106 và WCH-LinkE cùng lái NRST |
| TP401 | VCC_3V3_MOD | VCC_3V3_MOD | - | `TestPoint_Pad_D1.5mm` | Kiểm tra PMIC module đã lên |
<!-- END GENERATED: pins-soc -->

### 4.5 Debug, LED, nút, test point

- **J501** (1×7, 2.54 mm) cho WCH-LinkE: 3V3 (chỉ tham chiếu mức), SWDIO, SWCLK, GND, NRST, TX/RX log của MCU.
- **J502** (1×3): USB FS thứ hai của CH32V305 (D+, D-, GND).
- **J503** (1×3): console RV1106 (GND, TX, RX), 3.3 V.
- **SW501 RECOVERY:** giữ khi cấp nguồn để RV1106 vào chế độ loader, nạp qua USB-C PC. **SW502 RESET:** kéo NPOR.
- **LED** theo custom-box.md §8: đỏ = chưa có iPhone, vàng = có HID nhưng chưa có hình, xanh = sẵn sàng. MCU lái
  (PWM), ~2 mA mỗi LED; LED 0603, Vf ≤ 2.2 V (xanh loại 570 nm AlInGaP; xanh InGaN Vf ~3 V sẽ quá tối ở 3.3 V).

<!-- BEGIN GENERATED: pins-debug -->
**J501 MCU SWD+UART** (PinHeader 1x07 2.54; footprint `Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical`; nguồn: -)

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | 3V3 | 3V3 | rail (30 chân) | tham chiếu mức cho WCH-LinkE, không cấp ngược | [Chắc] |
| 2 | SWDIO | SWDIO | U301.46 |  | [Chắc] |
| 3 | SWCLK | SWCLK | U301.49 |  | [Chắc] |
| 4 | GND | GND | rail (160 chân) |  | [Chắc] |
| 5 | NRST | MCU_NRST | U301.7, R301.1, C311.1, R413.2 |  | [Chắc] |
| 6 | TX | MCU_DBG_TX | U301.29 | TX của MCU | [Chắc] |
| 7 | RX | MCU_DBG_RX | U301.30 | RX của MCU | [Chắc] |

**J502 MCU USB FS** (PinHeader 1x03 2.54; footprint `Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical`; nguồn: -)

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | D+ | MCU_FS_DP | U301.45 |  | [Chắc] |
| 2 | D- | MCU_FS_DN | U301.44 |  | [Chắc] |
| 3 | GND | GND | rail (160 chân) |  | [Chắc] |

**J503 SoC UART** (PinHeader 1x03 2.54; footprint `Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical`; nguồn: [RV1106_DTS](https://github.com/LuckfoxTECH/luckfox-pico/tree/main/sysdrv/source/kernel/arch/arm/boot/dts))

| Chân | Tên | Net | Nối tới | Ghi chú | Độ tin cậy |
|---|---|---|---|---|---|
| 1 | GND | GND | rail (160 chân) |  | [Chắc] |
| 2 | TX | SOC_CON_TX | U401.72 | TX của RV1106 | [Chắc] |
| 3 | RX | SOC_CON_RX | U401.73 | RX của RV1106 | [Chắc] |

Linh kiện thụ động và test point của khối:

| Ref | Giá trị | Chân 1 | Chân 2 | Footprint | Vai trò |
|---|---|---|---|---|---|
| D501 | RED | GND | LED_R_A | `LED_0603_1608Metric` | LED trạng thái RED (đỏ: chưa có iPhone, vàng: có HID chưa có hình, xanh: sẵn sàng) |
| D502 | YELLOW | GND | LED_Y_A | `LED_0603_1608Metric` | LED trạng thái YELLOW (đỏ: chưa có iPhone, vàng: có HID chưa có hình, xanh: sẵn sàng) |
| D503 | GREEN | GND | LED_G_A | `LED_0603_1608Metric` | LED trạng thái GREEN (đỏ: chưa có iPhone, vàng: có HID chưa có hình, xanh: sẵn sàng) |
| R501 | 680R | LED_R | LED_R_A | `R_0402_1005Metric` | Hạn dòng LED RED ~2 mA |
| R502 | 680R | LED_Y | LED_Y_A | `R_0402_1005Metric` | Hạn dòng LED YELLOW ~2 mA |
| R503 | 560R | LED_G | LED_G_A | `R_0402_1005Metric` | Hạn dòng LED GREEN ~2 mA |
| SW501 | RECOVERY | RECOVERY_KEY | GND | `SW_SPST_TL3342` | Giữ khi cấp nguồn: RV1106 vào chế độ loader (rockusb) qua USB-C PC |
| SW502 | RESET | SOC_NPOR | GND | `SW_SPST_TL3342` | Reset RV1106 (NPOR) |
| TP501 | VIN | VIN | - | `TestPoint_Pad_D1.5mm` | Test point VIN |
| TP502 | 5V2_PHONE | 5V2_PHONE | - | `TestPoint_Pad_D1.5mm` | Test point 5V2_PHONE |
| TP503 | 5V_SYS | 5V_SYS | - | `TestPoint_Pad_D1.5mm` | Test point 5V_SYS |
| TP504 | 3V3 | 3V3 | - | `TestPoint_Pad_D1.5mm` | Test point 3V3 |
| TP505 | 1V2 | 1V2 | - | `TestPoint_Pad_D1.5mm` | Test point 1V2 |
| TP506 | PHONE_VBUS | PHONE_VBUS | - | `TestPoint_Pad_D1.5mm` | Test point PHONE_VBUS |
| TP507 | GND | GND | - | `TestPoint_Pad_D1.5mm` | Test point GND |
| TP508 | GND | GND | - | `TestPoint_Pad_D1.5mm` | Test point GND |
| TP509 | 3V3_LT | 3V3_LT | - | `TestPoint_Pad_D1.5mm` | Test point 3V3_LT |
| TP510 | 1V2_LT_A | 1V2_LT_A | - | `TestPoint_Pad_D1.5mm` | Test point 1V2_LT_A |
<!-- END GENERATED: pins-debug -->

---

## 5. Quy tắc đi dây cao tần

Trở kháng tính cho stack-up §11 (JLC04161H-7628, lớp ngoài cách L2 0.21 mm, εr ≈ 4.4) bằng công thức microstrip
IPC-2141; **[Có thể]**, tính lại bằng máy tính trở kháng của nhà sản xuất trước khi đặt bo.

| Nhóm | Net | Trở kháng | Rộng / khe (lớp 1 trên GND L2) | Khớp độ dài |
|---|---|---|---|---|
| DP lane từ iPhone | SS_TX1/RX1/TX2/RX2 _P/_N | 100 Ω vi sai (85–100) | 0.20 / 0.15 mm | trong cặp ≤ 0.1 mm; giữa các lane ≤ 2 mm |
| DP AUX | PHONE_SBU1/2, LT_AUX_P/N | 100 Ω vi sai | 0.20 / 0.15 mm | ≤ 0.5 mm |
| MIPI CSI-2 | CSI_CLK, CSI_D0–D3 _P/_N | 100 Ω vi sai | 0.20 / 0.15 mm | trong cặp ≤ 0.1 mm; data so với clock ≤ 1 mm |
| USB 2.0 HS | PHONE_USB_DP/DN, PC_USB_DP/DN | 90 Ω vi sai | 0.25 / 0.15 mm | trong cặp ≤ 0.15 mm |
| USB FS | MCU_FS_DP/DN | 90 Ω | 0.25 / 0.15 mm | không khắt khe |
| Ethernet | ETH_TX/RX (_J) | 100 Ω vi sai | 0.20 / 0.15 mm | trong cặp ≤ 0.5 mm, càng ngắn càng tốt |
| Nguồn 3 A | VIN, 5V2_PHONE, PSW_*, PHONE_VBUS, 5V_SYS, SW node | | ≥ 1.0 mm hoặc đổ đồng | |

Quy tắc:

1. **DP lane:** ngắn nhất có thể từ J201 tới LT7911D (mục tiêu ≤ 15 mm), đi hết trên lớp 1, không via. Nếu buộc phải
   đổi lớp thì via GND khâu kèm hai bên. Không có tụ AC trên lane phía sink (bên phát là iPhone đã có tụ trong DP)
   **[Có thể]**, xác nhận bằng sơ đồ Lontium. ESD U202/U203 đặt sát ổ cắm, đi thẳng qua pad (flow-through), không
   rẽ nhánh.
2. **MIPI CSI:** LT7911D đặt sát mép trái của Core1106 (pad 1–12). Mục tiêu ≤ 30 mm; 5 cặp cùng lớp, cùng số via.
3. **USB HS iPhone:** J201 → U205 → CH32V305 PB6/PB7, ≤ 30 mm. Nối A6-B6 và A7-B7 ngay tại ổ cắm, nhánh cụt ≤ 2 mm.
   Không đi cặp USB song song sát cặp DP.
4. **Mặt phẳng tham chiếu:** L2 GND liền dưới mọi cặp vi sai; không cắt rãnh dưới cặp. Via GND khâu dọc cặp DP và CSI
   mỗi ~3 mm.
5. **Khoảng cách:** giữa hai cặp vi sai ≥ 3× độ rộng vết (≥ 0.6 mm); giữa cặp và đồng khác ≥ 0.3 mm.
6. **Buck:** vòng VIN–SW–GND nhỏ nhất; tụ vào sát VIN/GND; nút SW không đi dưới cặp vi sai hay thạch anh. Pad nhiệt
   HSOP có via nhiệt xuống GND.
7. **Shunt R126:** đi dây Kelvin riêng từ hai pad của R126 tới IN+/IN- của U106.
8. **Thạch anh:** X301 sát OSC_IN/OSC_OUT; vết ngắn, bao quanh bằng GND, không vết khác đi dưới.

---

## 6. Chân strapping và boot

| Chip | Chân | Mạch | Ý nghĩa |
|---|---|---|---|
| CH32V305 | BOOT0 (60) | 10 kΩ xuống GND + GPIO1_D2 của RV1106 | 0 = chạy flash; 1 + BOOT1 = 0 = bootloader ISP (USART1/USB) **[Chắc]** |
| CH32V305 | PB2/BOOT1 (28) | 10 kΩ xuống GND | cố định 0 |
| CH32V305 | NRST (7) | 4.7 kΩ lên 3V3, 100 nF, RV1106 qua 1 kΩ, J501 | reset |
| RV1106 | SARADC_IN0 (pad 26) | 10 kΩ lên 1.8 V, phím SW501 xuống GND | kéo lên bắt buộc **[Chắc]** (ghi chú Luckfox); giữ phím khi khởi động = chế độ loader (rockusb) **[Có thể]** |
| RV1106 | NPOR (pad 74) | SW502 xuống GND | reset toàn hệ |
| LT7911D | RST_N (20) | 10 kΩ lên 3V3 + 1 µF; GPIO0_A3 | reset, mức thấp **[Có thể]** |
| LT7911D | SLEEP_33 (19) | TP201, R204 DNP | **[Chưa biết]** |
| CH224K | CFG1/CFG2/CFG3 | 6.8 kΩ / trống / trống | xin 9 V **[Chắc]** |

---

## 7. Clock và thạch anh

| Linh kiện | Tần số | Tải | Lý do |
|---|---|---|---|
| X301 (CH32V305 HSE) | 8 MHz, 3225, ±20 ppm, CL 12 pF | C301/C302 18 pF C0G | PLL USB HS lấy HSE/2 = 4 MHz làm tham chiếu (ví dụ USBHS của WCH) **[Chắc]**; tụ = 2 × (12 − ~3) pF |
| X201 (LT7911D) | 25 MHz (giả định) | 18 pF (giả định) | **[Chưa biết]**: hỏi Lontium |
| RV1106 | 24 MHz (thạch anh thụ động 2016) trên module | | có sẵn trên Core1106 **[Chắc]** (sơ đồ module) |
| CSI clock | 400 MHz DDR (800 Mbit/s/lane) | | do LT7911D phát, driver khai 400 MHz **[Chắc]** |

---

## 8. ESD và bảo vệ

- **Cổng iPhone (J201):** TPD4E05U06 × 3 (SS, CC, SBU), USBLC6-2SC6 (D+/D-), TVS SMF6.0A trên VBUS, tụ 10 µF + 100 nF,
  R210 xả VBUS. Công tắc MOSFET đấu ngược chặn dòng hai chiều.
- **Cổng PC (J401):** USBLC6-2SC6, TVS SMF6.0A, tụ 1 µF (UFP ≤ 10 µF).
- **Cổng nguồn (J101):** cầu chì 5 A, TVS SMAJ24A, tụ khối 47 µF. CC nối thẳng CH224K (chịu 8 V tuyệt đối): chưa có
  bảo vệ khi cáp lỗi chập CC với VBUS 20 V **[Chưa biết]**, cân nhắc thêm TVS 6–8 V nếu dùng sạc 20 V.
- **RJ45:** cách ly bằng biến áp trong jack; tụ Bob-Smith 1 nF 100 V (jack có sẵn 1 nF/2 kV bên trong **[Có thể]**).
- Vỏ các đầu nối USB-C nối GND trực tiếp; nếu vỏ box bằng nhôm nối đất thì cân nhắc 1 MΩ // 4.7 nF giữa vỏ và GND.

---

## 9. Bring-up và thứ tự đo

Hàn và thử từng giai đoạn; mỗi giai đoạn dùng test point sẵn có.

1. **Kiểm tra trần:** đo điện trở giữa từng rail và GND trước khi cấp nguồn (TP501 VIN, TP502 5V2_PHONE, TP503 5V_SYS,
   TP504 3V3, TP505 1V2, TP509 3V3_LT, TP510 1V2_LT_A, TP506 PHONE_VBUS; GND ở TP507/TP508). Không rail nào < 100 Ω.
2. **Nguồn:** hàn J101, U101, F101, D101, U103, U104, U105 và linh kiện đi kèm. Cắm sạc PD (hoặc bộ ghi PD như
   POWER-Z KM003C để xem bản tin): VIN = 9 V, PD_PG thấp, 5V_SYS = 5.02 V ± 2 %, 3V3 = 3.31 V, 1V2 lên sau 3V3
   ~4 ms (đo bằng máy hiện sóng hai kênh). Sau đó hàn U102: 5V2_PHONE = 5.22 V, PHONE_VBUS vẫn 0 V.
3. **MCU:** hàn U301, X301, J501, LED. Nạp qua WCH-LinkE, nháy ba LED. Thử HID khi chưa có LT7911D: hàn tạm
   R211/R212 (Rd) để iPhone làm nguồn + host; iPhone cấp 5 V vào PHONE_VBUS (công tắc Q101/Q102 chặn ngược), CH32V305
   enumerate High-Speed. Đo khoảng cách poll (thí nghiệm T1 của custom-box.md). Gỡ R211/R212 trước bước 5.
4. **Core1106:** hàn U401, J401, J402, J503. Console 115200 trên J503; Ethernet lên link 100M; cắm J401 vào PC để
   thấy thiết bị USB (gadget). Nhấn SW501 khi cấp nguồn để vào chế độ loader. Thử link SPI/UART với MCU và nạp MCU qua
   BOOT0 + USART1.
5. **LT7911D:** hàn U201 (máy khò + stencil), X201, ESD, tụ. `i2cdetect` trên bus 2 thấy 0x2B; ghi 0x80EE = 0x01
   (bật I2C nội, như driver làm), rồi đọc 0xA000/0xA001 = 0x16/0x05 (chip ID 0x0516). Cắm iPhone bằng cáp USB-C có lane cao tốc (Thunderbolt/USB 3): theo dõi CC bằng bộ ghi PD, kiểm tra
   iPhone vào DP Alt Mode, rồi bắt khung hình qua V4L2 (thí nghiệm T2/T3).
6. **Sạc iPhone:** bật MCU_VBUS_EN, đo PHONE_VBUS và PHONE_ISENSE (0.5 V/A) khi iPhone vừa sạc vừa phản chiếu
   màn hình (T5). Kiểm tra VBUS về < 0.8 V trong 650 ms sau khi rút cáp.

---

## 10. Câu hỏi mở và rủi ro

### 10.1 Cần hỏi Lontium (hoặc đại lý) trước khi layout

1. Datasheet đầy đủ LT7911D: bảng 64 chân + EPAD, kích thước pad nhiệt, footprint khuyến nghị, profile hàn.
2. Sơ đồ tham chiếu "Type-C DP Alt Mode sink → MIPI CSI-2 4 lane có sạc pass-through": cách nối UCC1/UCC2, cổng CC
   thứ hai phía sạc (có hay không, chân nào), chân điều khiển công tắc VBUS, chân đo VBUS, VCONN.
3. Firmware: LT7911D có làm được vai **Source cấp điện + UFP_D + DR_Swap** với iPhone không, trong khi vẫn là sink
   PD phía sạc? Quảng bá PDO nào cho iPhone (chỉ 5 V/3 A hay có 9 V)? Có bản firmware CSI (không phải DSI) sẵn không?
4. Ánh xạ cặp SS của ổ Type-C vào lane D0–D3, xử lý lật đầu cắm (có mux nội không), phân cực AUX, tụ AC và điện trở
   phân cực AUX phía sink.
5. Tần số thạch anh, tụ tải, hay dùng clock ngoài.
6. Điện áp và dòng của từng chân nguồn (chân `VDD` là 1.2 V hay khác), yêu cầu trình tự nguồn, thời gian reset.
7. Chức năng `SLEEP_33`, `RX_HPD` khi chạy Type-C; GPIO nào là ngắt; địa chỉ I2C có cố định 0x2B; mức I/O của I2C.
8. Cổng MIPI nào xuất CSI, sơ đồ chân MIPI, có đảo lane/đảo cực không, định dạng CSI (YUV422 8-bit), tốc độ tối đa
   mỗi lane, 1080p60 và 4K30.
9. Công cụ sửa EDID và nạp firmware qua I2C (giao thức, giấy phép dùng).

### 10.2 Rủi ro

| ID | Rủi ro | Mức | Giảm thiểu |
|---|---|---|---|
| H1 | Chưa có sơ đồ chân LT7911D | Chặn layout | §10.1; mua board đánh giá (danh sách mua §13 của custom-box.md) |
| H2 | LT7911D không làm được Source + UFP_D với iPhone | Cao | phương án B; nếu cả hai không được: adapter USB-C sang HDMI + cầu HDMI-CSI (dự phòng trong custom-box.md §4) |
| H3 | RV1106 không nhận 1080p60 4 lane từ LT7911D (driver 5.10, lane mapping, băng thông) | Trung bình | thí nghiệm T3 trên Luckfox Pico trước PCB |
| H4 | Nhiệt: ~2 W tổn hao buck + LT7911D + RV1106 trong vỏ nhôm 90×60×20 mm | Trung bình | đổ đồng, pad nhiệt ra vỏ, đo ở T5 |
| H5 | Wi-Fi trên module bị vỏ nhôm chắn | Trung bình | cửa sổ nhựa trên anten hoặc bản module có đầu anten ngoài; không đồng dưới ANT1 |
| H6 | Sạc < 27 W: tụt áp VIN khi iPhone kéo 3 A | Trung bình | firmware giới hạn theo VIN_SENSE/PHONE_ISENSE; khuyến nghị sạc 30 W |
| H7 | Nguồn cung Core1106 bản G3 + eMMC + Wi-Fi | Thấp–TB | bản không Wi-Fi vẫn hợp chân (pad Wi-Fi để trống) |
| H8 | Hàn tay QFN-64 0.4 mm của LT7911D | Trung bình | stencil + máy khò, hoặc đặt JLCPCB lắp riêng con này |
| H9 | Giá trị VREF/ngưỡng EN của LMR33630/TLV62569 lấy gián tiếp | Thấp | đọc datasheet TI, sửa `check.py` nếu khác |
| H10 | File KiCad 8 chưa được mở bằng chính KiCad 8 trong môi trường này | Thấp | đã kiểm bằng KiCad 7.0.11 (bản chuyển đổi) và parser riêng (§13) |

---

## 11. Stack-up 4 lớp, kích thước và bố trí

**Stack-up đề xuất** (JLCPCB JLC04161H-7628, 1.6 mm, đồng ngoài 1 oz, trong 0.5 oz) **[Có thể]**, tham số theo
bảng stack-up của nhà sản xuất:

| Lớp | Vai trò |
|---|---|
| L1 (Top) | linh kiện, mọi cặp vi sai (DP, CSI, USB, ETH), buck |
| prepreg 7628, ~0.21 mm | |
| L2 | GND liền, không cắt |
| core ~1.07 mm | |
| L3 | đổ đồng nguồn: 5V_SYS, 3V3, 1V2 (vùng riêng), tín hiệu chậm |
| prepreg 7628, ~0.21 mm | |
| L4 (Bottom) | tín hiệu chậm, đổ GND, via nhiệt |

Nếu cần vết mảnh hơn để chui giữa pad QFN 0.4 mm, dùng stack-up JLC04161H-3313 (prepreg ~0.1 mm): 100 Ω ≈ 0.11/0.13
mm, 90 Ω ≈ 0.13/0.10 mm (ước tính, tính lại bằng máy tính của hãng).

**Kích thước mục tiêu 90×60 mm**, vỏ nhôm cao ~20 mm (RJ45 HR911105A cao ~13.5 mm).

Bố trí gợi ý (nhìn từ trên):

- **Mép trái:** J201 (USB-C iPhone). Ngay sau là U202–U205, rồi LT7911D (≤ 15 mm từ ổ cắm) và CH32V305 (≤ 30 mm).
- **Giữa:** Core1106 30×30 mm, xoay sao cho mép pad 1–28 (CSI pad 1–12, USB pad 22–23) quay về phía LT7911D.
- **Mép phải:** J402 RJ45 cạnh pad 85–88 (góc trên phải của module).
- **Mép dưới:** J401 (USB-C PC) gần pad 22–23; J101 (USB-C PD) và khối nguồn (U101–U106, Q101–Q103) ở góc dưới phải,
  xa cặp DP/CSI.
- **Anten Wi-Fi:** ANT1 nằm ở một góc của module (xem sơ đồ lắp ráp trang 2 của Core1106.pdf): đặt góc đó ra mép bo,
  cấm đồng mọi lớp trong ~10 mm quanh anten.
- **Header và nút:** J501/J502/J503, SW501/SW502, LED ở mép trên, lộ ra qua vỏ (nút có thể chỉ cần lỗ kim).

Footprint cần làm riêng: `box-v1:LT7911D_QFN-64-1EP_7.5x7.5mm_P0.4mm` (chờ datasheet) và
`box-v1:Luckfox_Core1106_Castellated_30x30mm_P1.0mm` (dựng theo kích thước ở §4.4, đối chiếu file `Core1106-SMT` của
Luckfox; kiểm tra điều khoản dùng file của Luckfox trước khi chép trực tiếp).

---

## 12. BOM

[`bom.csv`](bom.csv) gom linh kiện giống nhau, có cột LCSC khi đã xác nhận được mã (CH32V305RBT6 C5187529, LT7911D
C5310990, CH224K C970725, LMR33630ADDAR C841384, TPD4E05U06DQAR C138714, USBLC6-2SC6 C7519, TYPE-C-31-M-12 C165948,
HR911105A C12074; nguồn: trang sản phẩm LCSC/JLCPCB qua kết quả tìm kiếm, thư viện KiCad) **[Có thể]**. Các mã khác
để trống: chọn khi đặt hàng. Tụ gốm không ghi áp: X7R (hoặc X5R với tụ ≥ 10 µF), ≥ 16 V cho rail ≤ 5.2 V, ≥ 50 V cho
VIN. Dòng "DNP" là linh kiện không lắp ở cấu hình mặc định (R106, R107, R128, R204, R206,
R207, R211, R212).

---

## 13. Kiểm tra (`check.py`)

Luật trên thiết kế (lỗi thì thoát mã 1):

- R1 ref duy nhất, số chân duy nhất trong linh kiện, có giá trị/footprint, IC và đầu nối có mã hàng;
- R2 mọi chân nằm trên net hoặc đánh dấu NC; R3 mọi net ≥ 2 chân;
- R4 mọi chân nguồn vào của IC khai tụ lọc, và tụ đó thật sự nằm giữa net của chân và GND;
- R5 không trùng tên net (không phân biệt hoa thường, bỏ dấu phân cách); R6 hai nửa cặp vi sai đối xứng;
- R7 net nào nuôi chân nguồn phải là rail đã khai; R8 mọi khoá nguồn trích dẫn tồn tại;
- R9 số chân giả `?..` phải mang nhãn [Chưa biết]; R10 điện áp ra các buck, ngưỡng UVLO, dải ADC, CFG1, dòng LED.

Luật trên file sinh ra: G1 mỗi `.kicad_sch` là một biểu thức S cân bằng, token `kicad_sch`, version 20231120; G2
mọi `lib_id` có trong `lib_symbols`, đường dẫn instance đúng cây trang; G3 **đọc ngược kết nối từ hình học** (đầu
chân, global label, power symbol, cờ no-connect) và so từng chân với `netlist.py`; G4 SVG là XML hợp lệ và hiện đủ
ref; G5 `bom.csv` và `netlist.json` khớp `netlist.py`. `--selftest` cài 9 lỗi giả (chân treo, net một chân, thiếu tụ,
tụ sai net, trùng tên net, cặp vi sai lệch, trùng ref, sai điện trở hồi tiếp, chân giả thiếu nhãn) và xác nhận bắt
đủ.

Kiểm tra thêm bằng KiCad (không cần để chạy `check.py`): KiCad 8 chưa cài được trong môi trường dựng, nên
`generate.py --kicad7 DIR` ghi một bản chuyển sang định dạng KiCad 7 (20230121). `kicad-cli` 7.0.11 mở được cả cây 6
trang, xuất PDF/SVG, và netlist nó xuất ra khớp `netlist.py` ở mọi chân (0 sai khác; 115 chân NC nằm ở net
"unconnected"). ERC chỉ có trong `kicad-cli` 8: khi mở bằng KiCad 8, chạy `kicad-cli sch erc kicad/box-v1.kicad_sch`.
Các rail có chân nguồn vào nhưng không có chân nguồn ra đã có `PWR_FLAG` ở trang nguồn.

---

## Nguồn

**Datasheet và tài liệu hãng**
- WCH CH32V303/305/307/317 datasheet V3.9 (bảng 3-1, hình 3.1.2, §2.5.3, hình 4-1-1):
  https://raw.githubusercontent.com/ch32-riscv-ug/CH32V307/main/datasheet_en/CH32V20x_30xDS0.PDF
- WCH openwch/ch32v307, ví dụ USBHS (cấu hình PLL USB HS từ HSE):
  https://github.com/openwch/ch32v307 (`EVT/EXAM/USB/USBHS/DEVICE/CH372Device/User/ch32v30x_usbhs_device.c`, dòng 127–129)
- WCH CH224 datasheet V1F (§4.3, §5.2, §5.5, §6.2), bản sao của CH224DS1.PDF:
  https://raw.githubusercontent.com/makespacemadrid/cheap-wled-controller/main/datasheet/ch224k.pdf
- Lontium LT7911D product brief R1.3 (không tải được, chỉ có trích đoạn): https://www.lontiumsemi.com/UploadFiles/2022-10/LT7911D_Brief_R1.3.pdf
- LT7911D trên LCSC/JLCPCB (C5310990): https://jlcpcb.com/partdetail/LONTIUMSEMICONDUCTOR-LT7911D/C5310990
- Luckfox Core1106: bảng chân, sơ đồ, footprint, bảng công suất:
  https://github.com/LuckfoxTECH/Luckfox-Pico-docs/tree/main/Hardware/Schematic
  (`Core1106-PinOut.xls`, `Core1106.pdf`, `Core1106-Footprint.zip`, `Core1106_Power_Consumption_Reference_V1.0.xlsx`)
- Luckfox Pico Ultra W (USB_DET, phím RECOVERY) và Pico Plus / 86-Panel (Ethernet), cùng thư mục trên.
- Rockchip RV1106 datasheet V1.7 §1.2.8 (MIPI CSI): https://github.com/LuckfoxTECH/Luckfox-Pico-docs/tree/main/Docs/datasheets
- Luckfox SDK, `rv1106.dtsi` và `rv1106-pinctrl.dtsi` (UART2 console, i2c2m0, spi0m0, uart4m0, csi2_dphy0):
  https://github.com/LuckfoxTECH/luckfox-pico/tree/main/sysdrv/source/kernel/arch/arm/boot/dts
- Rockchip BSP, driver `lt7911d.c`/`lt7911d.h` và DTS `rk3588s-evb1-lp4x-v10-camera.dtsi`:
  https://github.com/rockchip-linux/kernel/tree/develop-5.10
- Thư viện ký hiệu KiCad 8.0.9 (sơ đồ chân LMR33630ADDA, TLV62569DBV, INA180A2, USBLC6-2SC6, TPD4E05U06DQA, CH224K,
  USB_C_Receptacle, RJ45_Hanrun_HR911105A_Horizontal, IRF7404, 2N7002): https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9
- USB Type-C Cable and Connector Specification (bảng chân ổ cắm, Rd 5.1 kΩ, vSafe0V):
  https://www.usb.org/document-library/usb-type-cr-cable-and-connector-specification-release-24

**Mã LCSC** (trang sản phẩm, qua kết quả tìm kiếm): https://www.lcsc.com/product-detail/C5187529.html,
https://www.lcsc.com/product-detail/C970725.html, https://www.lcsc.com/product-detail/C841384.html,
https://lcsc.com/product-detail/ESD-Protection-Devices_Texas-Instruments-TPD4E05U06DQAR_C138714.html,
https://www.lcsc.com/product-detail/C7519.html, https://www.lcsc.com/product-detail/C165948.html.
