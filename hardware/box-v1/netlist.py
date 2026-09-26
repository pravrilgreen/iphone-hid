"""iPhone control box v1 (phương án B) - single source of truth for the circuit.

Standard library only. Every component, every pin and every net of the board is
declared here; `generate.py` turns it into KiCad 8 schematics, SVG sheets, a BOM
and netlists, and `check.py` validates it.

Conventions
-----------
* Reference designators are numbered per sheet: 1xx power, 2xx iPhone port +
  LT7911D, 3xx CH32V305, 4xx Core1106 + Ethernet + PC USB-C, 5xx debug/LEDs.
* A pin is either on a net (`net="NAME"`) or explicitly no-connect (`net=NC`).
* `conf` holds the confidence of the pin *number and function*, with the same
  labels as docs/research/custom-box.md: CHAC = read from the primary source,
  COTHE = vendor claim or indirect source, CHUABIET = not public, must be
  confirmed. Pin numbers that are not public start with "?" (for example
  "?XTALI") so that they can never be mistaken for a real pad number.
* `src` points to an entry of SOURCES (URL + document + section/page).
* `decap` lists the capacitor references that decouple an IC power pin.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NC = "__NC__"  # sentinel: pin explicitly left unconnected

CHAC = "Chắc"
COTHE = "Có thể"
CHUABIET = "Chưa biết"

GND = "GND"

# Pin kinds (mapped to KiCad electrical types by generate.py)
IN, OUT, BIDI, PWR_IN, PWR_OUT, PASSIVE, OC, NCPIN = (
    "input", "output", "bidirectional", "power_in", "power_out", "passive",
    "open_collector", "no_connect")

SOURCES = {
    "CH32DS": ("https://raw.githubusercontent.com/ch32-riscv-ug/CH32V307/main/datasheet_en/CH32V20x_30xDS0.PDF",
               "WCH CH32V303/305/307/317 datasheet V3.9, §3.1.2 Figure 'CH32V305RBT6' (p.24) and "
               "Table 3-1 pin definitions, column LQFP64M (p.27-38); §2.5.3 power scheme (p.13); "
               "Figure 4-1-1 power supply decoupling (p.55)"),
    "CH32EVT": ("https://github.com/openwch/ch32v307",
                "openwch/ch32v307, EVT/EXAM/USB/USBHS/DEVICE/CH372Device/User/ch32v30x_usbhs_device.c "
                "lines 127-129: USBHS PHY PLL from HSE /2 = 4 MHz reference, i.e. HSE = 8 MHz"),
    "CH224": ("https://raw.githubusercontent.com/makespacemadrid/cheap-wled-controller/main/datasheet/ch224k.pdf",
              "WCH CH224 datasheet V1F (mirror of wch.cn CH224DS1.PDF): §4.3 CH224K pin table (p.2), "
              "§5.2.1 CFG1 resistor table (p.3), §5.5 PD-only use (p.4), §6.2 reference circuit "
              "VDD 1 kΩ + 1 µF, VBUS 10 kΩ (p.5)"),
    "CH224KICAD": ("https://gitlab.com/kicad/libraries/kicad-symbols/-/blob/8.0.9/Interface_USB.kicad_sym",
                   "KiCad 8.0.9 symbol CH224K (SSOP-10-1EP, EP = pad 11): CC1 = 7, CC2 = 6 "
                   "(same as datasheet figure §6.2; the datasheet table lists '6, 7 CC1, CC2')"),
    "LT7911D_BSP": ("https://github.com/rockchip-linux/kernel/tree/develop-5.10/drivers/media/i2c",
                    "Rockchip BSP drivers/media/i2c/lt7911d.c + lt7911d.h: I2C slave, chip id 0x0516 at "
                    "regs 0xA000/0xA001, reset-gpios (active low), optional power-gpios / plugin-det-gpios, "
                    "IRQ rising edge, requires an 'xvclk' clock handle; "
                    "arch/arm64/boot/dts/rockchip/rk3588s-evb1-lp4x-v10-camera.dtsi: reg = <0x2b>, "
                    "data-lanes = <1 2 3 4>"),
    "LT7911D_BRIEF": ("https://www.lontiumsemi.com/UploadFiles/2022-10/LT7911D_Brief_R1.3.pdf",
                      "Lontium LT7911D product brief R1.3 (QFN64 7.5x7.5 mm, 1.2 V + 3.3 V supplies). "
                      "Not downloadable from the build environment (proxy 403); pin names/numbers 1-23 "
                      "were taken from search-engine excerpts of this brief and of the LCSC datasheet "
                      "C5310990, so they are [Có thể] only"),
    "CORE1106_XLS": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106-PinOut.xls",
                     "Luckfox Core1106-PinOut.xls: 112 pads, pin name, IO power domain, remarks "
                     "(eMMC / Wi-Fi variants disconnect pads 37-46, 48-54, 63-64, 68-69)"),
    "CORE1106_SCH": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106.pdf",
                     "Luckfox Core1106 schematic p.1: VCC5V0_SYS input -> EA3036C (3V3/0V9/1V8) + MP1605 "
                     "(DDR); VCC_3V3/VCC_1V8 exported on pads 78/77; FEPHY_REXT 6.04k on module; "
                     "'USB must always power supply'"),
    "CORE1106_PWR": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106_Power_Consumption_Reference_V1.0.xlsx",
                     "Core1106 power reference: VCC5V0_SYS 4.6-5.2 V, 1000 mA recommended input, "
                     "measured 467 mA max; VCC_1V8 / VCC_3V3 max 300 mA external load each"),
    "CORE1106_FP": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106-Footprint.zip",
                    "Core1106 footprint (KiCad Core1106-SMT.kicad_mod): 30x30 mm, 112 pads, 1.0 mm pitch, "
                    "pad 0.7x1.5 mm centred on the module edge, pin 1 top-left, counter-clockwise"),
    "PICO_ULTRA": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Luckfox-Pico-Ultra-W.pdf",
                   "Luckfox Pico Ultra W schematic p.1: USB_DET_IN = VBUS via 10k/18k + 100 nF; "
                   "RECOVERY key: SARADC_IN0 10k pull-up to VCC_1V8, 1 nF C0G, 100R to key "
                   "('SARADC_IN0 must always be pulled-up')"),
    "PICO_PLUS_ETH": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Luckfox-Pico-Plus.pdf",
                      "Luckfox Pico Plus / 86-Panel bottom board: RV1106 FEPHY -> 0R -> RJ45 magjack TD/RD, "
                      "centre taps 10 nF (103) to GND, pin 8 (Bob-Smith node) 1 nF/100 V (or 0R) to GND, "
                      "shield to GND"),
    "RV1106_DTS": ("https://github.com/LuckfoxTECH/luckfox-pico/tree/main/sysdrv/source/kernel/arch/arm/boot/dts",
                   "rv1106.dtsi: fiq-debugger serial-id 2, uart2 pinctrl uart2m1_xfer; rv1106-pinctrl.dtsi: "
                   "uart2m1 = GPIO1_B2 (TX)/GPIO1_B3 (RX), i2c2m0 = GPIO1_A0/A1, spi0m0 = GPIO1_C0..C3, "
                   "uart4m0 = GPIO1_B0 (RX)/GPIO1_B1 (TX)"),
    "RV1106_DS": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Docs/datasheets/Rockchip_RV1106_Datasheet_V1.7.pdf",
                  "Rockchip RV1106 datasheet V1.7 §1.2.8: two 2-lane MIPI D-PHY V1.2 RX, 1.5 Gbps/lane, "
                  "combinable into one 4-lane port"),
    "KICAD_SYM": ("https://gitlab.com/kicad/libraries/kicad-symbols/-/tree/8.0.9",
                  "KiCad 8.0.9 official symbol library (pin maps copied from the manufacturers' "
                  "datasheets): LMR33630ADDA, TLV62569DBV, INA180A2, USBLC6-2SC6, TPD4E05U06DQA, "
                  "USB_C_Receptacle, USB_C_Receptacle_USB2.0_16P, RJ45_Hanrun_HR911105A_Horizontal, "
                  "IRF7404 (SO-8 P-MOSFET S=1-3 G=4 D=5-8), 2N7002, Crystal_GND24"),
    "USBC_SPEC": ("https://www.usb.org/document-library/usb-type-cr-cable-and-connector-specification-release-24",
                  "USB Type-C Cable and Connector Specification: receptacle pin map A1-A12 / B1-B12, "
                  "UFP Rd = 5.1 kΩ, vSafe0V, tVBUSON/OFF"),
}


@dataclass
class Pin:
    num: str
    name: str
    kind: str
    net: str
    side: str = "L"          # L, R, T, B on the box symbol
    note: str = ""
    conf: str = CHAC
    decap: tuple = ()
    src: str = ""            # overrides part.src when set


@dataclass
class Part:
    ref: str
    value: str
    mpn: str
    footprint: str
    block: str
    desc: str
    pins: list
    lcsc: str = ""
    src: str = ""
    dnp: bool = False
    note: str = ""
    symbol: str = ""        # KiCad symbol style: 'box' or a passive style (R, C, L, ...)
    conf: str = CHAC        # confidence of the part choice / values

    @property
    def is_ic(self) -> bool:
        return self.ref[0] == "U"

    def pin(self, num: str) -> Pin:
        for p in self.pins:
            if p.num == num:
                return p
        raise KeyError(f"{self.ref}.{num}")


@dataclass
class NetInfo:
    name: str
    cls: str                 # gnd, rail, diff, clock, analog, signal
    desc: str = ""
    pair: str = ""           # partner net of a differential pair
    zdiff: int = 0           # differential impedance target in ohms
    rail_v: str = ""         # nominal voltage for rails


BLOCKS = {
    "power": "Nguồn: PD vào, các rail, công tắc VBUS iPhone",
    "iphone": "USB-C iPhone + LT7911D",
    "mcu": "CH32V305 (HID)",
    "soc": "Core1106 (RV1106) + Ethernet + USB-C PC",
    "debug": "Debug, LED, nút, test point",
}


@dataclass
class Design:
    parts: list = field(default_factory=list)
    nets: dict = field(default_factory=dict)

    def add(self, part: Part) -> Part:
        self.parts.append(part)
        return part

    def part(self, ref: str) -> Part:
        for p in self.parts:
            if p.ref == ref:
                return p
        raise KeyError(ref)

    def net_pins(self):
        """net name -> list of (part, pin)."""
        out: dict = {}
        for part in self.parts:
            for pin in part.pins:
                if pin.net not in (NC, None, ""):
                    out.setdefault(pin.net, []).append((part, pin))
        return out


# ---------------------------------------------------------------------------
# helpers for two-pin parts
# ---------------------------------------------------------------------------
FP = {
    "R0402": "Resistor_SMD:R_0402_1005Metric",
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "R1206": "Resistor_SMD:R_1206_3216Metric",
    "C0402": "Capacitor_SMD:C_0402_1005Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
    "FB0603": "Inductor_SMD:L_0603_1608Metric",
    "LED0603": "LED_SMD:LED_0603_1608Metric",
}


def _two(d, ref, value, n1, n2, fp, block, desc, symbol, mpn="", lcsc="", dnp=False,
         note="", names=("1", "2"), conf=CHAC, src=""):
    return d.add(Part(ref=ref, value=value, mpn=mpn, footprint=fp, block=block, desc=desc,
                      pins=[Pin("1", names[0], PASSIVE, n1, "L"),
                            Pin("2", names[1], PASSIVE, n2, "R")],
                      lcsc=lcsc, dnp=dnp, note=note, symbol=symbol, conf=conf, src=src))


def R(d, ref, value, n1, n2, block, desc, fp="R0402", **kw):
    return _two(d, ref, value, n1, n2, FP.get(fp, fp), block, desc, "R", **kw)


def C(d, ref, value, n1, n2, block, desc, fp="C0402", **kw):
    return _two(d, ref, value, n1, n2, FP.get(fp, fp), block, desc, "C", **kw)


def L(d, ref, value, n1, n2, block, desc, fp, **kw):
    return _two(d, ref, value, n1, n2, fp, block, desc, "L", **kw)


def FB(d, ref, n1, n2, block, desc, **kw):
    return _two(d, ref, "600R@100MHz", n1, n2, FP["FB0603"], block, desc, "FB",
                mpn="BLM18KG601SN1D", **kw)


def D(d, ref, value, k, a, block, desc, fp, symbol="D", **kw):
    """Diode: pin 1 = K, pin 2 = A (KiCad Device:D convention)."""
    return _two(d, ref, value, k, a, fp, block, desc, symbol, names=("K", "A"), **kw)


def LED(d, ref, value, k, a, block, desc, **kw):
    return _two(d, ref, value, k, a, FP["LED0603"], block, desc, "LED", names=("K", "A"), **kw)


def TP(d, ref, net, block, desc):
    return d.add(Part(ref=ref, value=net, mpn="", footprint="TestPoint:TestPoint_Pad_D1.5mm",
                      block=block, desc=desc, pins=[Pin("1", "TP", PASSIVE, net, "L")],
                      symbol="TP"))


def box(d, ref, value, mpn, fp, block, desc, pins, **kw):
    return d.add(Part(ref=ref, value=value, mpn=mpn, footprint=fp, block=block, desc=desc,
                      pins=pins, symbol="box", **kw))


P = Pin

# ---------------------------------------------------------------------------
# net metadata
# ---------------------------------------------------------------------------
RAILS = {
    "GND": "0 V",
    "VBUS_IN": "5-20 V (PD, mặc định 9 V)",
    "VIN": "5-20 V sau cầu chì",
    "5V2_PHONE": "5.2 V / 3 A cho iPhone",
    "5V_SYS": "5.0 V / 3 A hệ thống",
    "3V3": "3.3 V / 2 A",
    "1V2": "1.2 V / 2 A (LT7911D)",
    "3V3_LT": "3.3 V lọc cho LT7911D",
    "1V2_LT_A": "1.2 V lọc (analog/PLL LT7911D)",
    "VDDA_MCU": "3.3 V lọc cho VDDA CH32V305",
    "CH224_VDD": "3.3 V shunt nội của CH224K",
    "PHONE_VBUS": "VBUS cổng iPhone (0 hoặc 5.2 V)",
    "PC_VBUS": "VBUS từ PC (5 V)",
    "VCC_1V8_MOD": "1.8 V ra từ Core1106 (≤ 300 mA)",
    "VCC_3V3_MOD": "3.3 V ra từ Core1106 (≤ 300 mA)",
}

DIFF = [
    # (P net, N net, Zdiff, description)
    ("SS_TX1_P", "SS_TX1_N", 100, "DP lane từ iPhone (cặp TX1 của ổ cắm, A2/A3)"),
    ("SS_RX1_P", "SS_RX1_N", 100, "DP lane từ iPhone (cặp RX1, B11/B10)"),
    ("SS_TX2_P", "SS_TX2_N", 100, "DP lane từ iPhone (cặp TX2, B2/B3)"),
    ("SS_RX2_P", "SS_RX2_N", 100, "DP lane từ iPhone (cặp RX2, A11/A10)"),
    ("LT_AUX_P", "LT_AUX_N", 100, "DP AUX sau tụ AC"),
    ("PHONE_SBU1", "PHONE_SBU2", 100, "SBU1/SBU2 = DP AUX trước tụ"),
    ("CSI_CLK_P", "CSI_CLK_N", 100, "MIPI CSI-2 clock"),
    ("CSI_D0_P", "CSI_D0_N", 100, "MIPI CSI-2 lane 0"),
    ("CSI_D1_P", "CSI_D1_N", 100, "MIPI CSI-2 lane 1"),
    ("CSI_D2_P", "CSI_D2_N", 100, "MIPI CSI-2 lane 2"),
    ("CSI_D3_P", "CSI_D3_N", 100, "MIPI CSI-2 lane 3"),
    ("PHONE_USB_DP", "PHONE_USB_DN", 90, "USB 2.0 HS iPhone <-> CH32V305"),
    ("PC_USB_DP", "PC_USB_DN", 90, "USB 2.0 HS PC <-> RV1106"),
    ("MCU_FS_DP", "MCU_FS_DN", 90, "USB FS CH32V305 -> header"),
    ("ETH_TX_P", "ETH_TX_N", 100, "100BASE-TX TX"),
    ("ETH_RX_P", "ETH_RX_N", 100, "100BASE-TX RX"),
    ("ETH_TXP_J", "ETH_TXN_J", 100, "TX sau 0R, vào biến áp"),
    ("ETH_RXP_J", "ETH_RXN_J", 100, "RX sau 0R, vào biến áp"),
]


def build() -> Design:
    d = Design()
    build_power(d)
    build_iphone(d)
    build_mcu(d)
    build_soc(d)
    build_debug(d)
    _net_info(d)
    return d


def _net_info(d: Design):
    for name, v in RAILS.items():
        d.nets[name] = NetInfo(name, "gnd" if name == GND else "rail", rail_v=v, desc=v)
    for p, n, z, desc in DIFF:
        d.nets[p] = NetInfo(p, "diff", desc, pair=n, zdiff=z)
        d.nets[n] = NetInfo(n, "diff", desc, pair=p, zdiff=z)
    for name in ("HSE_IN", "HSE_OUT", "LT_XI", "LT_XO"):
        d.nets[name] = NetInfo(name, "clock", "thạch anh")
    for name in ("CC1_SENSE", "CC2_SENSE", "PHONE_VBUS_SENSE", "PHONE_ISENSE", "VIN_SENSE",
                 "PC_VBUS_DET", "SOC_RECOVERY"):
        d.nets[name] = NetInfo(name, "analog", "ADC")
    for net in d.net_pins():
        d.nets.setdefault(net, NetInfo(net, "signal"))


# ---------------------------------------------------------------------------
# Sheet 1: power
# ---------------------------------------------------------------------------
def usbc16(ref, block, vbus, cc1, cc2, dp, dn, desc, lcsc="C165948"):
    """HRO TYPE-C-31-M-12 16P receptacle (KiCad USB_C_Receptacle_USB2.0_16P pin map)."""
    def n(x):
        return x if x else NC
    pins = [
        P("A1", "GND", PASSIVE, GND, "L"), P("A4", "VBUS", PASSIVE, vbus, "L"),
        P("A5", "CC1", BIDI, cc1, "L"), P("A6", "D+", BIDI, n(dp), "L"),
        P("A7", "D-", BIDI, n(dn), "L"), P("A8", "SBU1", BIDI, NC, "L"),
        P("A9", "VBUS", PASSIVE, vbus, "L"), P("A12", "GND", PASSIVE, GND, "L"),
        P("B1", "GND", PASSIVE, GND, "R"), P("B4", "VBUS", PASSIVE, vbus, "R"),
        P("B5", "CC2", BIDI, cc2, "R"), P("B6", "D+", BIDI, n(dp), "R"),
        P("B7", "D-", BIDI, n(dn), "R"), P("B8", "SBU2", BIDI, NC, "R"),
        P("B9", "VBUS", PASSIVE, vbus, "R"), P("B12", "GND", PASSIVE, GND, "R"),
        P("S1", "SHIELD", PASSIVE, GND, "R"),
    ]
    return pins


def build_power(d: Design):
    b = "power"
    box(d, "J101", "USB-C PD IN", "TYPE-C-31-M-12",
        "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", b,
        "Cổng USB-C nhận nguồn PD từ sạc (chỉ nguồn + CC)",
        usbc16("J101", b, "VBUS_IN", "PD_CC1", "PD_CC2", None, None, ""),
        lcsc="C165948", src="KICAD_SYM",
        note="D+/D- để trống: CH224K chạy chế độ chỉ PD (datasheet CH224 §5.5)")
    _two(d, "F101", "5A 32V", "VBUS_IN", "VIN", "Fuse:Fuse_1206_3216Metric", b,
         "Cầu chì nhanh 5 A / 32 V cho đầu vào", "F", mpn="Littelfuse 0466005.NR", conf=COTHE)
    D(d, "D101", "SMAJ24A", "VIN", GND, b, "TVS 24 V standoff trên VIN", "Diode_SMD:D_SMA",
      symbol="D_TVS", mpn="SMAJ24A")
    _two(d, "C101", "47uF 35V", "VIN", GND, "Capacitor_SMD:CP_Elec_6.3x7.7", b,
         "Tụ khối VIN, giảm dao động khi cắm nóng cáp dài", "CP",
         mpn="nhôm polymer/điện phân 47 µF 35 V 6.3x7.7")

    # CH224K PD sink (datasheet §6.2 reference, resistor mode)
    box(d, "U101", "CH224K", "CH224K", "Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm", b,
        "Bộ kích PD (sink) cho nguồn vào", [
            P("1", "VDD", PWR_IN, "CH224_VDD", "L", "shunt 3.3 V nội, cấp qua 1 kΩ từ VIN",
              decap=("C102",)),
            P("2", "CFG2", PASSIVE, NC, "L", "chế độ điện trở: CFG2/CFG3 phải để trống (§5.2.1)"),
            P("3", "CFG3", PASSIVE, NC, "L", "như CFG2"),
            P("4", "DP", BIDI, "CH224_DPDM", "L", "nối tắt DP-DM: chỉ dùng PD (§5.5)"),
            P("5", "DM", BIDI, "CH224_DPDM", "L", "nối tắt DP-DM"),
            P("6", "CC2", BIDI, "CH224_CC2", "R", "qua R105 0R tới J101.B5", src="CH224KICAD"),
            P("7", "CC1", BIDI, "CH224_CC1", "R", "qua R104 0R tới J101.A5", src="CH224KICAD"),
            P("8", "VBUS", PASSIVE, "CH224_VSNS", "R", "đo áp qua 10 kΩ"),
            P("9", "CFG1", PASSIVE, "CH224_CFG1", "R", "6.8 kΩ xuống GND = xin 9 V"),
            P("10", "PG", OC, "PD_PG", "R", "open-drain, mức thấp = đã có điện áp xin"),
            P("11", "GND", PWR_IN, GND, "R", "EPAD (datasheet gọi là chân 0)"),
        ], lcsc="C970725", src="CH224")
    R(d, "R101", "1k", "VIN", "CH224_VDD", b, "Điện trở cấp VDD CH224K (datasheet §6.2)",
      fp="R1206", note="1206 (0.25 W): ở 15 V tiêu tán 0.14 W", src="CH224")
    C(d, "C102", "1uF 25V", "CH224_VDD", GND, b, "Tụ VDD CH224K", fp="C0603", src="CH224")
    R(d, "R102", "10k", "VIN", "CH224_VSNS", b, "Nối tiếp chân VBUS của CH224K (§6.2)", src="CH224")
    R(d, "R103", "6.8k 1%", "CH224_CFG1", GND, b,
      "Chọn điện áp xin: 6.8k=9V (mặc định), 24k=12V, 56k=15V, bỏ trống=20V", src="CH224")
    R(d, "R104", "0R", "PD_CC1", "CH224_CC1", b, "Phương án A: CC của sạc vào CH224K")
    R(d, "R105", "0R", "PD_CC2", "CH224_CC2", b, "Phương án A: CC của sạc vào CH224K")
    R(d, "R106", "0R", "PD_CC1", "LT_PDCC1", b,
      "Phương án B: CC của sạc vào cổng PD thứ hai của LT7911D", dnp=True, conf=CHUABIET)
    R(d, "R107", "0R", "PD_CC2", "LT_PDCC2", b, "Phương án B (như R106)", dnp=True, conf=CHUABIET)
    R(d, "R108", "10k", "PD_PG", "3V3", b, "Kéo lên cho PG (open-drain) tới MCU PB12")

    # U102: VIN -> 5V2_PHONE (LMR33630A, 400 kHz)
    def lmr(ref, out, fbb, en_top, en_bot, desc, cin, cvcc, cboot, lref, lval, lisat, couts,
            rfbt, rfbb, ren1, ren2, cinhf):
        box(d, ref, "LMR33630ADDA", "LMR33630ADDAR",
            "Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias", b, desc, [
                P("1", "GND", PWR_IN, GND, "L"),
                P("2", "VIN", PWR_IN, "VIN", "L", decap=tuple(cin) + (cinhf,)),
                P("3", "EN", IN, f"{ref}_EN", "L", f"chia áp UVLO {en_top}/{en_bot}"),
                P("4", "PG", OC, NC, "L", "không dùng"),
                P("5", "FB", IN, f"{ref}_FB", "R"),
                P("6", "VCC", PWR_OUT, f"{ref}_VCC", "R", "LDO nội, tụ 1 µF"),
                P("7", "BOOT", PASSIVE, f"{ref}_BOOT", "R"),
                P("8", "SW", OUT, f"{ref}_SW", "R"),
                P("9", "EP", PWR_IN, GND, "R", "pad nhiệt = GND"),
            ], lcsc="C841384", src="KICAD_SYM", conf=COTHE,
            note="VREF 1.0 V, ngưỡng EN ~1.2 V: đọc lại datasheet TI SNVSAX4 trước khi đặt hàng")
        for c in cin:
            C(d, c, "10uF 50V X7R", "VIN", GND, b, f"Tụ vào {ref}", fp="C1206")
        C(d, cinhf, "100nF 50V", "VIN", GND, b, f"Tụ cao tần sát chân VIN/GND {ref}", fp="C0603")
        C(d, cvcc, "1uF 16V", f"{ref}_VCC", GND, b, f"Tụ LDO nội {ref}")
        C(d, cboot, "100nF 16V", f"{ref}_BOOT", f"{ref}_SW", b, f"Tụ bootstrap {ref}")
        L(d, lref, lval, f"{ref}_SW", out, b, f"Cuộn cảm buck {ref}, Isat ≥ {lisat}",
          "Inductor_SMD:L_Bourns_SRP1038C_10.0x10.0mm", mpn="Bourns SRP1038C-100M (hoặc tương đương)",
          conf=COTHE)
        for c in couts:
            C(d, c, "22uF 10V X5R", out, GND, b, f"Tụ ra {ref}", fp="C1206")
        R(d, rfbt, "100k 1%", out, f"{ref}_FB", b, f"Hồi tiếp trên {ref}")
        R(d, rfbb, fbb, f"{ref}_FB", GND, b, f"Hồi tiếp dưới {ref}: Vout = 1.0 V x (1 + 100k/Rfbb)")
        R(d, ren1, en_top, "VIN", f"{ref}_EN", b, f"Chia áp EN {ref} (trên)")
        R(d, ren2, en_bot, f"{ref}_EN", GND, b, f"Chia áp EN {ref} (dưới)")

    lmr("U102", "5V2_PHONE", "23.7k 1%", "100k", "20k",
        "Buck VIN -> 5V2_PHONE (5.22 V / 3 A) nuôi iPhone; khởi động khi VIN > ~7.2 V",
        ["C103", "C104"], "C106", "C107", "L101", "10uH", "5 A", ["C108", "C109", "C110"],
        "R109", "R110", "R111", "R112", "C105")
    lmr("U103", "5V_SYS", "24.9k 1%", "100k", "39k",
        "Buck VIN -> 5V_SYS (5.02 V / 3 A) cho Core1106 và các rail; khởi động khi VIN > ~4.3 V",
        ["C111", "C112"], "C114", "C115", "L102", "10uH", "4 A", ["C116", "C117", "C118"],
        "R113", "R114", "R115", "R116", "C113")

    # U104: 5V_SYS -> 3V3 ; U105: 5V_SYS -> 1V2 (after 3V3)
    def tlv(ref, out, en_net, cin, cinhf, lref, couts, rtop, rbot, rbot_val, desc):
        box(d, ref, "TLV62569DBV", "TLV62569DBVR", "Package_TO_SOT_SMD:SOT-23-5", b, desc, [
            P("1", "EN", IN, en_net, "L"),
            P("2", "GND", PWR_IN, GND, "L"),
            P("3", "SW", OUT, f"{ref}_SW", "R"),
            P("4", "VIN", PWR_IN, "5V_SYS", "L", decap=(cin, cinhf)),
            P("5", "FB", IN, f"{ref}_FB", "R"),
        ], src="KICAD_SYM", conf=COTHE, note="VREF 0.6 V, 1.5 MHz: đọc lại datasheet TI SLVSDI0")
        C(d, cin, "10uF 10V", "5V_SYS", GND, b, f"Tụ vào {ref}", fp="C0603")
        C(d, cinhf, "100nF", "5V_SYS", GND, b, f"Tụ cao tần vào {ref}")
        L(d, lref, "2.2uH", f"{ref}_SW", out, b, f"Cuộn cảm {ref}, Isat ≥ 3 A, 4x4 mm",
          "Inductor_SMD:L_Bourns-SRN4018", mpn="Bourns SRN4018-2R2M (hoặc tương đương)", conf=COTHE)
        for c in couts:
            C(d, c, "22uF 6.3V X5R", out, GND, b, f"Tụ ra {ref}", fp="C0805")
        R(d, rtop, "100k 1%", out, f"{ref}_FB", b, f"Hồi tiếp trên {ref}")
        R(d, rbot, rbot_val, f"{ref}_FB", GND, b, f"Hồi tiếp dưới {ref}: Vout = 0.6 V x (1 + 100k/Rbot)")

    tlv("U104", "3V3", "U104_EN", "C119", "C120", "L103", ["C121", "C122"], "R118", "R119",
        "22.1k 1%", "Buck 5V_SYS -> 3V3 (3.315 V / 2 A)")
    R(d, "R117", "100k", "5V_SYS", "U104_EN", b, "EN U104: bật ngay khi có 5V_SYS")
    tlv("U105", "1V2", "U105_EN", "C123", "C124", "L104", ["C126", "C127"], "R121", "R122",
        "100k 1%", "Buck 5V_SYS -> 1V2 (1.2 V / 2 A) cho LT7911D, bật sau 3V3")
    R(d, "R120", "100k", "3V3", "U105_EN", b, "EN U105 lấy từ 3V3 qua RC: 1V2 lên sau 3V3 ~4 ms")
    C(d, "C125", "100nF", "U105_EN", GND, b, "Tụ trễ EN U105 (τ = 10 ms)")

    # iPhone VBUS switch: back-to-back P-MOSFET, gate driven by 2N7002
    JP = "Jumper:SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm"
    box(d, "JP101", "PSW_SRC", "", JP, b,
        "Chọn nguồn cho iPhone: 1-2 = 5V2_PHONE (phương án A, mặc định), 2-3 = VIN (phương án B)", [
            P("1", "A", PASSIVE, "5V2_PHONE", "L"),
            P("2", "C", PASSIVE, "PSW_IN", "R"),
            P("3", "B", PASSIVE, "VIN", "L"),
        ], note="Làm pad rộng ≥ 2 mm, phủ thiếc dày: dòng 3 A")
    sop8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
    for ref, dnet, desc in (("Q101", "PSW_IN", "P-MOSFET phía nguồn (back-to-back)"),
                            ("Q102", "PSW_OUT", "P-MOSFET phía iPhone (back-to-back)")):
        box(d, ref, "AO4407A", "AO4407A", sop8, b, desc + ": -30 V, ~11 mΩ @ -10 V", [
            P("1", "S", PASSIVE, "PSW_S", "L"), P("2", "S", PASSIVE, "PSW_S", "L"),
            P("3", "S", PASSIVE, "PSW_S", "L"), P("4", "G", IN, "PSW_G", "L"),
            P("5", "D", PASSIVE, dnet, "R"), P("6", "D", PASSIVE, dnet, "R"),
            P("7", "D", PASSIVE, dnet, "R"), P("8", "D", PASSIVE, dnet, "R"),
        ], src="KICAD_SYM", conf=COTHE,
            note="Sơ đồ chân SO-8 MOSFET chuẩn (S=1-3, G=4, D=5-8), như ký hiệu IRF7404 của KiCad")
    R(d, "R123", "100k", "PSW_S", "PSW_G", b, "Kéo cổng lên nguồn chung: MOSFET tắt mặc định")
    C(d, "C128", "47nF 50V", "PSW_S", "PSW_G", b, "Khởi động mềm (~0.5 ms), giới hạn dòng nạp tụ iPhone")
    D(d, "D102", "BZT52C10", "PSW_S", "PSW_G", b, "Zener 10 V kẹp Vgs khi dùng phương án B (VIN tới 20 V)",
      "Diode_SMD:D_SOD-123", symbol="D_Zener", mpn="BZT52C10")
    R(d, "R124", "10k", "PSW_G", "PSW_GD", b, "Điện trở cổng: Vgs = -Vin x 100k/110k")
    box(d, "Q103", "2N7002", "2N7002", "Package_TO_SOT_SMD:SOT-23", b, "N-MOSFET kéo cổng xuống", [
        P("1", "G", IN, "PHONE_VBUS_EN", "L"), P("2", "S", PASSIVE, GND, "R"),
        P("3", "D", PASSIVE, "PSW_GD", "R")], src="KICAD_SYM")
    R(d, "R125", "100k", "PHONE_VBUS_EN", GND, b, "Mặc định tắt VBUS iPhone")
    R(d, "R127", "0R", "MCU_VBUS_EN", "PHONE_VBUS_EN", b, "Phương án A: CH32V305 bật VBUS iPhone")
    R(d, "R128", "0R", "LT_VBUS_EN", "PHONE_VBUS_EN", b,
      "Phương án B: GPIO của LT7911D bật VBUS iPhone", dnp=True, conf=CHUABIET)
    R(d, "R126", "10m 1%", "PSW_OUT", "PHONE_VBUS", b, "Shunt đo dòng sạc iPhone (Kelvin)", fp="R1206",
      mpn="1206 10 mΩ 1% 0.5 W")
    box(d, "U106", "INA180A2", "INA180A2IDBVR", "Package_TO_SOT_SMD:SOT-23-5", b,
        "Khuếch đại dòng shunt, gain 50: 3 A -> 1.5 V", [
            P("1", "OUT", OUT, "PHONE_ISENSE", "R"),
            P("2", "GND", PWR_IN, GND, "L"),
            P("3", "IN+", IN, "PSW_OUT", "L", "Kelvin sát R126 phía MOSFET"),
            P("4", "IN-", IN, "PHONE_VBUS", "L", "Kelvin sát R126 phía iPhone"),
            P("5", "V+", PWR_IN, "3V3", "L", decap=("C129",)),
        ], src="KICAD_SYM", conf=COTHE)
    C(d, "C129", "100nF", "3V3", GND, b, "Tụ nguồn U106")
    C(d, "C130", "1nF", "PHONE_ISENSE", GND, b, "Lọc đầu ra U106 trước ADC")

    # Development option: power the box from the PC USB-C port
    box(d, "JP102", "PC_PWR", "", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm", b,
        "Hở mặc định. Hàn để nuôi box từ PC (chỉ khi KHÔNG cắm sạc vào J101)", [
            P("1", "A", PASSIVE, "PC_VBUS", "L"), P("2", "B", PASSIVE, "PCPWR_A", "R")])
    D(d, "D104", "SS34", "5V_SYS", "PCPWR_A", b, "Schottky 3 A 40 V: PC_VBUS -> 5V_SYS (chế độ phát triển)",
      "Diode_SMD:D_SMA", symbol="D_Schottky", mpn="SS34")


# ---------------------------------------------------------------------------
# Sheet 2: iPhone USB-C + LT7911D
# ---------------------------------------------------------------------------
def build_iphone(d: Design):
    b = "iphone"
    usbc24 = [
        P("A1", "GND", PASSIVE, GND, "L"), P("A2", "TX1+", BIDI, "SS_TX1_P", "L"),
        P("A3", "TX1-", BIDI, "SS_TX1_N", "L"), P("A4", "VBUS", PASSIVE, "PHONE_VBUS", "L"),
        P("A5", "CC1", BIDI, "PHONE_CC1", "L"), P("A6", "D+", BIDI, "PHONE_USB_DP", "L"),
        P("A7", "D-", BIDI, "PHONE_USB_DN", "L"), P("A8", "SBU1", BIDI, "PHONE_SBU1", "L"),
        P("A9", "VBUS", PASSIVE, "PHONE_VBUS", "L"), P("A10", "RX2-", BIDI, "SS_RX2_N", "L"),
        P("A11", "RX2+", BIDI, "SS_RX2_P", "L"), P("A12", "GND", PASSIVE, GND, "L"),
        P("B1", "GND", PASSIVE, GND, "R"), P("B2", "TX2+", BIDI, "SS_TX2_P", "R"),
        P("B3", "TX2-", BIDI, "SS_TX2_N", "R"), P("B4", "VBUS", PASSIVE, "PHONE_VBUS", "R"),
        P("B5", "CC2", BIDI, "PHONE_CC2", "R"), P("B6", "D+", BIDI, "PHONE_USB_DP", "R"),
        P("B7", "D-", BIDI, "PHONE_USB_DN", "R"), P("B8", "SBU2", BIDI, "PHONE_SBU2", "R"),
        P("B9", "VBUS", PASSIVE, "PHONE_VBUS", "R"), P("B10", "RX1-", BIDI, "SS_RX1_N", "R"),
        P("B11", "RX1+", BIDI, "SS_RX1_P", "R"), P("B12", "GND", PASSIVE, GND, "R"),
        P("S1", "SHIELD", PASSIVE, GND, "R"),
    ]
    box(d, "J201", "USB-C iPhone", "Molex 105450-0101",
        "Connector_USB:USB_C_Receptacle_Molex_105450-0101", b,
        "Ổ USB-C 24 chân đủ cặp SS (DP Alt Mode 4 lane) + USB 2.0 + CC + VBUS", usbc24,
        src="USBC_SPEC", note="Chọn ổ 24P đạt USB 3.2 Gen2; ổ 16P (USB 2.0) không có cặp SS")

    # LT7911D. Pins 1-23: [Có thể] (vendor brief via search excerpts). Others: [Chưa biết].
    CO, CB = COTHE, CHUABIET
    lt = [
        P("1", "VCC12D_RX", PWR_IN, "1V2", "L", "1.2 V digital DP RX", CO, ("C203",)),
        P("2", "D0P", IN, "SS_RX2_P", "L", "DP lane 0+: ánh xạ cặp SS tạm thời, theo sơ đồ tham chiếu Lontium", CO),
        P("3", "D0N", IN, "SS_RX2_N", "L", "DP lane 0-", CO),
        P("4", "VCC12A_RX", PWR_IN, "1V2_LT_A", "L", "1.2 V analog DP RX", CO, ("C204",)),
        P("5", "D1P", IN, "SS_TX2_P", "L", "DP lane 1+", CO),
        P("6", "D1N", IN, "SS_TX2_N", "L", "DP lane 1-", CO),
        P("7", "VCC33_RX", PWR_IN, "3V3_LT", "L", "3.3 V DP RX", CO, ("C205",)),
        P("8", "D2P", IN, "SS_RX1_P", "L", "DP lane 2+", CO),
        P("9", "D2N", IN, "SS_RX1_N", "L", "DP lane 2-", CO),
        P("10", "VCC12_PI", PWR_IN, "1V2_LT_A", "L", "1.2 V phase interpolator", CO, ("C206",)),
        P("11", "D3P", IN, "SS_TX1_P", "L", "DP lane 3+", CO),
        P("12", "D3N", IN, "SS_TX1_N", "L", "DP lane 3-", CO),
        P("13", "VCC12_RXPLL", PWR_IN, "1V2_LT_A", "L", "1.2 V RX PLL", CO, ("C207",)),
        P("14", "UCC1", BIDI, "PHONE_CC1", "L", "Type-C CC1 phía iPhone (PD + Alt Mode)", CO),
        P("15", "UCC2", BIDI, "PHONE_CC2", "L", "Type-C CC2 phía iPhone", CO),
        P("16", "VCC33_IO", PWR_IN, "3V3_LT", "L", "3.3 V IO (I2C, GPIO 3.3 V)", CO, ("C208",)),
        P("17", "AUXP", BIDI, "LT_AUX_P", "L", "DP AUX+ (qua tụ 100 nF từ SBU1)", CO),
        P("18", "AUXN", BIDI, "LT_AUX_N", "L", "DP AUX- (qua tụ 100 nF từ SBU2)", CO),
        P("19", "SLEEP_33", IN, "LT_SLEEP", "R", "chức năng chưa rõ: để TP + R204 DNP", CO),
        P("20", "RST_N", IN, "LT_RST_N", "R", "reset mức thấp", CO),
        P("21", "CSCL", IN, "LT_SCL", "R", "I2C slave 0x2B (7-bit)", CO),
        P("22", "CSDA", BIDI, "LT_SDA", "R", "I2C slave", CO),
        P("23", "RX_HPD", OUT, "LT_RX_HPD", "R", "HPD phía DP; ở Type-C HPD đi qua bản tin PD: để TP", CO),
        P("?VDD", "VDD", PWR_IN, "1V2", "L", "chân lõi: điện áp chưa rõ (giả định 1.2 V)", CB, ("C209",)),
        P("?VCC33_TX", "VCC33_TX", PWR_IN, "3V3_LT", "L", "3.3 V MIPI TX (có thể nhiều chân)", CB, ("C210",)),
        P("?VCC12_TX", "VCC12_TX", PWR_IN, "1V2", "L", "1.2 V MIPI TX (có thể nhiều chân)", CB, ("C211",)),
        P("?XTALI", "XTALI", IN, "LT_XI", "R", "thạch anh (tần số chưa rõ, giả định 25 MHz)", CB),
        P("?XTALO", "XTALO", OUT, "LT_XO", "R", "thạch anh", CB),
        P("?TXCP", "TXA_CLKP", OUT, "CSI_CLK_P", "R", "MIPI port dùng cho CSI: clock+", CB),
        P("?TXCN", "TXA_CLKN", OUT, "CSI_CLK_N", "R", "clock-", CB),
        P("?TX0P", "TXA_D0P", OUT, "CSI_D0_P", "R", "CSI lane 0+", CB),
        P("?TX0N", "TXA_D0N", OUT, "CSI_D0_N", "R", "", CB),
        P("?TX1P", "TXA_D1P", OUT, "CSI_D1_P", "R", "CSI lane 1+", CB),
        P("?TX1N", "TXA_D1N", OUT, "CSI_D1_N", "R", "", CB),
        P("?TX2P", "TXA_D2P", OUT, "CSI_D2_P", "R", "CSI lane 2+", CB),
        P("?TX2N", "TXA_D2N", OUT, "CSI_D2_N", "R", "", CB),
        P("?TX3P", "TXA_D3P", OUT, "CSI_D3_P", "R", "CSI lane 3+", CB),
        P("?TX3N", "TXA_D3N", OUT, "CSI_D3_N", "R", "", CB),
        P("?INT", "GPIO_INT", OUT, "LT_INT", "R", "GPIO ngắt tới SoC (firmware quyết định chân nào)", CB),
        P("?VBUSEN", "GPIO_VBUS_EN", OUT, "LT_VBUS_EN", "R", "phương án B: điều khiển công tắc VBUS", CB),
        P("?PDCC1", "PD_CC1", BIDI, "LT_PDCC1", "L", "phương án B: CC phía sạc (nếu chip có cổng PD thứ hai)", CB),
        P("?PDCC2", "PD_CC2", BIDI, "LT_PDCC2", "L", "phương án B", CB),
        P("?IIS_WS", "IIS_WS", OUT, NC, "R", "âm thanh: không dùng", CB),
        P("?IIS_SCLK", "IIS_SCLK", OUT, NC, "R", "không dùng", CB),
        P("?IIS_MCLK", "IIS_MCLK", OUT, NC, "R", "không dùng", CB),
        P("?IIS_D0", "IIS_D0", OUT, NC, "R", "không dùng", CB),
        P("?SPDIF", "VSYNC_OUT/SPDIF", OUT, NC, "R", "không dùng", CB),
        P("?EPAD", "EPAD", PWR_IN, GND, "L", "pad nhiệt = GND (giả định)", CB),
    ]
    box(d, "U201", "LT7911D", "LT7911D", "box-v1:LT7911D_QFN-64-1EP_7.5x7.5mm_P0.4mm", b,
        "Type-C/DP1.2 -> MIPI CSI-2, PD + DP Alt Mode sink, MCU + flash nội", lt,
        lcsc="C5310990", src="LT7911D_BRIEF", conf=COTHE,
        note="Datasheet chịu NDA: chỉ vẽ chân theo chức năng; footprint phải làm lại khi có datasheet")
    _two(d, "X201", "25MHz", "LT_XI", "LT_XO", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", b,
         "Thạch anh LT7911D (tần số và tải chưa rõ)", "X", conf=CHUABIET)
    # a 4-pad crystal: add the two GND pads
    xp = d.part("X201")
    xp.pins = [P("1", "~", PASSIVE, "LT_XI", "L"), P("2", "GND", PASSIVE, GND, "B"),
               P("3", "~", PASSIVE, "LT_XO", "R"), P("4", "GND", PASSIVE, GND, "B")]
    C(d, "C201", "18pF C0G", "LT_XI", GND, b, "Tụ tải thạch anh (chưa rõ, theo sơ đồ Lontium)", conf=CHUABIET)
    C(d, "C202", "18pF C0G", "LT_XO", GND, b, "Tụ tải thạch anh", conf=CHUABIET)
    for ref, net, what in (("C203", "1V2", "pin 1"), ("C204", "1V2_LT_A", "pin 4"),
                           ("C205", "3V3_LT", "pin 7"), ("C206", "1V2_LT_A", "pin 10"),
                           ("C207", "1V2_LT_A", "pin 13"), ("C208", "3V3_LT", "pin 16"),
                           ("C209", "1V2", "VDD"), ("C210", "3V3_LT", "VCC33_TX"),
                           ("C211", "1V2", "VCC12_TX")):
        C(d, ref, "100nF", net, GND, b, f"Tụ lọc sát LT7911D {what}")
    C(d, "C212", "10uF 6.3V", "1V2", GND, b, "Tụ khối 1V2 tại LT7911D", fp="C0603")
    C(d, "C213", "10uF 6.3V", "1V2_LT_A", GND, b, "Tụ khối 1V2_LT_A", fp="C0603")
    C(d, "C214", "10uF 6.3V", "3V3_LT", GND, b, "Tụ khối 3V3_LT", fp="C0603")
    FB(d, "FB201", "3V3", "3V3_LT", b, "Hạt ferrite tách 3.3 V của LT7911D")
    FB(d, "FB202", "1V2", "1V2_LT_A", b, "Hạt ferrite tách 1.2 V analog/PLL")
    R(d, "R201", "2.2k", "LT_SCL", "3V3", b, "Kéo lên I2C (bus I2C2_M0 của RV1106, 3.3 V)")
    R(d, "R202", "2.2k", "LT_SDA", "3V3", b, "Kéo lên I2C")
    R(d, "R203", "10k", "LT_RST_N", "3V3", b, "Kéo lên RST_N: LT7911D chạy ngay khi có nguồn")
    C(d, "C215", "1uF", "LT_RST_N", GND, b, "RC reset khi bật nguồn (τ = 10 ms)")
    R(d, "R204", "10k", "LT_SLEEP", GND, b, "Tuỳ chọn mức cho SLEEP_33 (chưa rõ)", dnp=True, conf=CHUABIET)
    R(d, "R205", "100k", "LT_INT", GND, b, "Giữ LT_INT ở mức thấp khi LT7911D đang reset")
    C(d, "C216", "100nF", "PHONE_SBU1", "LT_AUX_P", b, "Tụ AC AUX (chưa rõ chiều/phân cực, theo sơ đồ Lontium)",
      conf=CHUABIET)
    C(d, "C217", "100nF", "PHONE_SBU2", "LT_AUX_N", b, "Tụ AC AUX", conf=CHUABIET)
    R(d, "R206", "1M", "LT_AUX_P", GND, b, "Phân cực AUX phía sink (chưa rõ)", dnp=True, conf=CHUABIET)
    R(d, "R207", "1M", "LT_AUX_N", "3V3_LT", b, "Phân cực AUX phía sink (chưa rõ)", dnp=True, conf=CHUABIET)
    TP(d, "TP201", "LT_SLEEP", b, "Đo/ép SLEEP_33")
    TP(d, "TP202", "LT_RX_HPD", b, "Đo RX_HPD")

    # ESD at the connector
    tpd = "Package_SON:USON-10_2.5x1.0mm_P0.5mm"
    for ref, a, bn, c, dd, desc in (
            ("U202", "SS_TX1_P", "SS_TX1_N", "SS_RX1_P", "SS_RX1_N", "ESD cặp SS TX1/RX1"),
            ("U203", "SS_TX2_P", "SS_TX2_N", "SS_RX2_P", "SS_RX2_N", "ESD cặp SS TX2/RX2"),
            ("U204", "PHONE_CC1", "PHONE_CC2", "PHONE_SBU1", "PHONE_SBU2", "ESD CC1/CC2/SBU1/SBU2")):
        box(d, ref, "TPD4E05U06DQA", "TPD4E05U06DQAR", tpd, b, desc + " (0.5 pF, flow-through)", [
            P("1", "D1+", PASSIVE, a, "L"), P("2", "D1-", PASSIVE, bn, "L"),
            P("3", "GND", PWR_IN, GND, "L"), P("4", "D2+", PASSIVE, c, "L"),
            P("5", "D2-", PASSIVE, dd, "L"), P("6", "NC", NCPIN, NC, "R", "pad đi xuyên"),
            P("7", "NC", NCPIN, NC, "R"), P("8", "GND", PWR_IN, GND, "R"),
            P("9", "NC", NCPIN, NC, "R"), P("10", "NC", NCPIN, NC, "R"),
        ], lcsc="C138714", src="KICAD_SYM")
    box(d, "U205", "USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", b,
        "ESD cặp USB 2.0 iPhone", [
            P("1", "I/O1", PASSIVE, "PHONE_USB_DP", "L"), P("2", "GND", PASSIVE, GND, "L"),
            P("3", "I/O2", PASSIVE, "PHONE_USB_DN", "L"), P("4", "I/O2", PASSIVE, "PHONE_USB_DN", "R"),
            P("5", "VBUS", PASSIVE, "3V3", "R", "nối 3V3 (tham chiếu kẹp)"),
            P("6", "I/O1", PASSIVE, "PHONE_USB_DP", "R"),
        ], lcsc="C7519", src="KICAD_SYM")
    C(d, "C218", "100nF", "3V3", GND, b, "Tụ tại chân VBUS của U205")
    D(d, "D201", "SMF6.0A", "PHONE_VBUS", GND, b,
      "TVS VBUS iPhone (phương án B, VIN tới 20 V: đổi sang SMF22A)", "Diode_SMD:D_SOD-123F",
      symbol="D_TVS", mpn="SMF6.0A")
    C(d, "C219", "10uF 25V", "PHONE_VBUS", GND, b, "Tụ VBUS phía iPhone (nguồn Type-C ≤ 120 µF)", fp="C0805")
    C(d, "C220", "100nF 25V", "PHONE_VBUS", GND, b, "Tụ cao tần VBUS iPhone")
    R(d, "R210", "10k", "PHONE_VBUS", GND, b, "Xả VBUS về vSafe0V (< 0.8 V trong ~0.4 s)", fp="R0603")
    R(d, "R211", "5.1k", "PHONE_CC1", GND, b,
      "Rd tạm cho bring-up chế độ chỉ HID (chưa hàn LT7911D): iPhone thành nguồn + host", dnp=True,
      src="USBC_SPEC")
    R(d, "R212", "5.1k", "PHONE_CC2", GND, b, "Rd tạm cho bring-up chế độ chỉ HID", dnp=True, src="USBC_SPEC")


# ---------------------------------------------------------------------------
# Sheet 3: CH32V305RBT6
# ---------------------------------------------------------------------------
def build_mcu(d: Design):
    b = "mcu"
    nc = NC
    pins = [
        P("1", "VBAT", PWR_IN, "3V3", "L", "không dùng RTC dự phòng", decap=("C303",)),
        P("2", "PC13", BIDI, nc, "L"), P("3", "PC14/OSC32_IN", BIDI, nc, "L"),
        P("4", "PC15/OSC32_OUT", BIDI, nc, "L"),
        P("5", "OSC_IN/PD0", IN, "HSE_IN", "L", "HSE 8 MHz (USBHS PLL cần 4 MHz = HSE/2)", src="CH32EVT"),
        P("6", "OSC_OUT/PD1", OUT, "HSE_OUT", "L"),
        P("7", "NRST", IN, "MCU_NRST", "L", "RC 10k/100nF + RV1106 + SWD"),
        P("8", "PC0", BIDI, nc, "L"), P("9", "PC1", BIDI, nc, "L"), P("10", "PC2", BIDI, nc, "L"),
        P("11", "PC3", BIDI, nc, "L"),
        P("12", "VSSA", PWR_IN, GND, "L"),
        P("13", "VDDA", PWR_IN, "VDDA_MCU", "L", "phải bằng VIO (§2.5.3)", decap=("C308", "C309")),
        P("14", "PA0/ADC0", IN, "CC1_SENSE", "L", "ADC: áp CC1 phía iPhone (qua 100k)"),
        P("15", "PA1/ADC1", IN, "CC2_SENSE", "L", "ADC: áp CC2"),
        P("16", "PA2/ADC2", IN, "PHONE_VBUS_SENSE", "L", "ADC: VBUS iPhone / 7.67"),
        P("17", "PA3/ADC3", IN, "PHONE_ISENSE", "L", "ADC: dòng sạc 0.5 V/A"),
        P("18", "VSS_4", PWR_IN, GND, "L"),
        P("19", "VDD_4", PWR_IN, "3V3", "L", decap=("C304",)),
        P("20", "PA4/SPI1_NSS", IN, "SPI_CS", "L", "SPI slave từ RV1106"),
        P("21", "PA5/SPI1_SCK", IN, "SPI_SCK", "L"),
        P("22", "PA6/SPI1_MISO", OUT, "SPI_MISO", "L"),
        P("23", "PA7/SPI1_MOSI", IN, "SPI_MOSI", "L"),
        P("24", "PC4", BIDI, nc, "L"), P("25", "PC5", BIDI, nc, "L"),
        P("26", "PB0", OUT, "MCU_IRQ", "L", "báo sự kiện cho RV1106"),
        P("27", "PB1", OUT, "MCU_VBUS_EN", "L", "bật VBUS iPhone (qua R127)"),
        P("28", "PB2/BOOT1", IN, "MCU_BOOT1", "L", "10k xuống GND"),
        P("29", "PB10/USART3_TX", OUT, "MCU_DBG_TX", "L", "log debug của MCU"),
        P("30", "PB11/USART3_RX", IN, "MCU_DBG_RX", "L"),
        P("31", "VSS_1", PWR_IN, GND, "L"),
        P("32", "VIO_1", PWR_IN, "3V3", "L", decap=("C305",)),
        # right side, counter-clockwise order 64 -> 33
        P("64", "VIO_3", PWR_IN, "3V3", "R", decap=("C307",)),
        P("63", "VSS_3", PWR_IN, GND, "R"),
        P("62", "PB9", BIDI, nc, "R"), P("61", "PB8", BIDI, nc, "R"),
        P("60", "BOOT0", IN, "MCU_BOOT0", "R", "10k xuống GND; RV1106 kéo lên để vào bootloader ISP"),
        P("59", "PB7/USBHS_DP", BIDI, "PHONE_USB_DP", "R", "USB 2.0 HS tới iPhone (PHY nội)"),
        P("58", "PB6/USBHS_DM", BIDI, "PHONE_USB_DN", "R"),
        P("57", "PB5", BIDI, nc, "R"), P("56", "PB4", BIDI, nc, "R"), P("55", "PB3", BIDI, nc, "R"),
        P("54", "PD2", BIDI, nc, "R"), P("53", "PC12", BIDI, nc, "R"), P("52", "PC11", BIDI, nc, "R"),
        P("51", "PC10", BIDI, nc, "R"), P("50", "PA15", BIDI, nc, "R"),
        P("49", "PA14/SWCLK", IN, "SWCLK", "R", "WCH-LinkE"),
        P("48", "VDD_2", PWR_IN, "3V3", "R", decap=("C306",)),
        P("47", "VSS_2", PWR_IN, GND, "R"),
        P("46", "PA13/SWDIO", BIDI, "SWDIO", "R", "WCH-LinkE"),
        P("45", "PA12/OTG_FS_DP", BIDI, "MCU_FS_DP", "R", "USB FS thứ hai -> header J502"),
        P("44", "PA11/OTG_FS_DM", BIDI, "MCU_FS_DN", "R"),
        P("43", "PA10/USART1_RX", IN, "MCU_UART_RX", "R", "UART link + ISP bootloader"),
        P("42", "PA9/USART1_TX", OUT, "MCU_UART_TX", "R", "UART link + ISP bootloader"),
        P("41", "PA8", BIDI, nc, "R"), P("40", "PC9", BIDI, nc, "R"),
        P("39", "PC8/TIM8_CH3", OUT, "LED_G", "R", "LED xanh (PWM)"),
        P("38", "PC7/TIM8_CH2", OUT, "LED_Y", "R", "LED vàng (PWM)"),
        P("37", "PC6/TIM8_CH1", OUT, "LED_R", "R", "LED đỏ (PWM)"),
        P("36", "PB15", BIDI, nc, "R"), P("35", "PB14", BIDI, nc, "R"), P("34", "PB13", BIDI, nc, "R"),
        P("33", "PB12", IN, "PD_PG", "R", "PG của CH224K (thấp = PD đã thương lượng)"),
    ]
    box(d, "U301", "CH32V305RBT6", "CH32V305RBT6", "Package_QFP:LQFP-64_10x10mm_P0.5mm", b,
        "MCU RISC-V: USB HS (PHY nội) làm HID cho iPhone, link SPI/UART tới RV1106", pins,
        lcsc="C5187529", src="CH32DS")
    _two(d, "X301", "8MHz", "HSE_IN", "HSE_OUT", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", b,
         "Thạch anh HSE 8 MHz, CL 12 pF, ±20 ppm", "X", src="CH32EVT")
    xp = d.part("X301")
    xp.pins = [P("1", "~", PASSIVE, "HSE_IN", "L"), P("2", "GND", PASSIVE, GND, "B"),
               P("3", "~", PASSIVE, "HSE_OUT", "R"), P("4", "GND", PASSIVE, GND, "B")]
    C(d, "C301", "18pF C0G", "HSE_IN", GND, b, "Tụ tải HSE: 2 x (12 pF - ~3 pF ký sinh)")
    C(d, "C302", "18pF C0G", "HSE_OUT", GND, b, "Tụ tải HSE")
    for ref, net, what in (("C303", "3V3", "VBAT pin 1"), ("C304", "3V3", "VDD_4 pin 19"),
                           ("C305", "3V3", "VIO_1 pin 32"), ("C306", "3V3", "VDD_2 pin 48"),
                           ("C307", "3V3", "VIO_3 pin 64"), ("C308", "VDDA_MCU", "VDDA pin 13")):
        C(d, ref, "100nF", net, GND, b, f"Tụ lọc {what} (datasheet Figure 4-1-1)", src="CH32DS")
    C(d, "C309", "1uF", "VDDA_MCU", GND, b, "Tụ VDDA thêm cho ADC")
    C(d, "C310", "10uF 6.3V", "3V3", GND, b, "Tụ khối 3V3 tại MCU", fp="C0603")
    FB(d, "FB301", "3V3", "VDDA_MCU", b, "Ferrite tách VDDA (VDDA = VIO về DC)")
    R(d, "R301", "4.7k", "MCU_NRST", "3V3", b,
      "Kéo lên NRST, đủ mạnh để thắng pull-down mặc định của GPIO1_D3 (RV1106) qua R413")
    C(d, "C311", "100nF", "MCU_NRST", GND, b, "Tụ NRST")
    R(d, "R302", "10k", "MCU_BOOT0", GND, b, "BOOT0 = 0: chạy từ flash")
    R(d, "R303", "10k", "MCU_BOOT1", GND, b, "BOOT1 = 0: BOOT0 = 1 thì vào system memory (ISP)")
    R(d, "R304", "100k", "PHONE_CC1", "CC1_SENSE", b, "Đo áp CC1, trở kháng cao để không tải CC")
    C(d, "C312", "1nF", "CC1_SENSE", GND, b, "Lọc ADC CC1")
    R(d, "R305", "100k", "PHONE_CC2", "CC2_SENSE", b, "Đo áp CC2")
    C(d, "C313", "1nF", "CC2_SENSE", GND, b, "Lọc ADC CC2")
    R(d, "R306", "100k", "PHONE_VBUS", "PHONE_VBUS_SENSE", b, "Chia áp VBUS iPhone (20 V -> 2.6 V)")
    R(d, "R307", "15k", "PHONE_VBUS_SENSE", GND, b, "Chia áp VBUS iPhone")
    C(d, "C314", "10nF", "PHONE_VBUS_SENSE", GND, b, "Lọc ADC VBUS")


# ---------------------------------------------------------------------------
# Sheet 4: Core1106 + Ethernet + USB-C to PC
# ---------------------------------------------------------------------------
CORE1106_NAMES = {
    1: "MIPI_CSI_RX_CK1N/GPI3_B2", 2: "MIPI_CSI_RX_CK1P/GPI3_B3", 3: "MIPI_CSI_RX_D3N/GPI3_B0",
    4: "MIPI_CSI_RX_D3P/GPI3_B1", 5: "MIPI_CSI_RX_D2N/GPI3_B4", 6: "MIPI_CSI_RX_D2P/GPI3_B5",
    7: "MIPI_CSI_RX_D1N/GPI3_B6", 8: "MIPI_CSI_RX_D1P/GPI3_B7", 9: "MIPI_CSI_RX_CK0N/GPI3_C0",
    10: "MIPI_CSI_RX_CK0P/GPI3_C1", 11: "MIPI_CSI_RX_D0N/GPI3_C2", 12: "MIPI_CSI_RX_D0P/GPI3_C3",
    13: "PWM1_M2/GPIO3_D3 (1V8)", 14: "I2C3_SDA_M2/GPIO3_D2 (1V8)", 15: "I2C3_SCL_M2/GPIO3_D1 (1V8)",
    16: "I2C4_SCL_M2/GPIO3_C7 (1V8)", 17: "I2C4_SDA_M2/GPIO3_D0 (1V8)", 18: "VI_CIF_VSYNC/GPIO3_C5 (1V8)",
    19: "MIPI_CLK0_OUT/GPIO3_C4 (1V8)", 20: "MIPI_CLK1_OUT/GPIO3_C6 (1V8)", 21: "GND",
    22: "USB_N", 23: "USB_P", 24: "USB_VBUSDET", 25: "GND", 26: "SARADC_IN0/GPIO4_C0",
    27: "SARADC_IN1/GPIO4_C1", 28: "GND", 29: "GND", 30: "CODEC_LINEOUT", 31: "CODEC_MICBIAS",
    32: "CODEC_MIC0N", 33: "CODEC_MIC0P", 34: "CODEC_MIC1N", 35: "CODEC_MIC1P", 36: "GND",
    37: "EMMC_D0/GPIO4_A4", 38: "EMMC_D1/GPIO4_A3", 39: "EMMC_D2/GPIO4_A2", 40: "EMMC_D3/GPIO4_A6",
    41: "EMMC_D4/GPIO4_A5", 42: "EMMC_D5/GPIO4_A7", 43: "EMMC_D6/GPIO4_A1", 44: "EMMC_D7/GPIO4_A0",
    45: "EMMC_CMD/GPIO4_B0", 46: "EMMC_CLK/GPIO4_B1", 47: "GND", 48: "SDMMC_DET/GPIO3_A1",
    49: "SDMMC_D0/GPIO3_A3", 50: "SDMMC_D1/GPIO3_A2", 51: "SDMMC_D2/GPIO3_A7", 52: "SDMMC_D3/GPIO3_A6",
    53: "SDMMC_CMD/GPIO3_A5", 54: "SDMMC_CLK/GPIO3_A4", 55: "GND", 56: "GND", 57: "GND",
    58: "UART0_RX_M0/GPIO0_A0", 59: "UART0_TX_M0/GPIO0_A1", 60: "PWM3_IR_M0/GPIO0_A2",
    61: "PWR_CTRL_M1/GPIO0_A3", 62: "PWR_CTRL_M0/GPIO0_A4", 63: "I2C1_SCL_M0/GPIO0_A5",
    64: "I2C1_SDA_M0/GPIO0_A6", 65: "I2C2_SCL_M0/UART3_TX_M0/GPIO1_A0",
    66: "I2C2_SDA_M0/UART3_RX_M0/GPIO1_A1", 67: "PWM0_M0/GPIO1_A2", 68: "UART1_TX_M0/GPIO1_A3",
    69: "UART1_RX_M0/GPIO1_A4", 70: "UART4_RX_M0/GPIO1_B0", 71: "UART4_TX_M0/GPIO1_B1",
    72: "UART2_TX_M1/GPIO1_B2", 73: "UART2_RX_M1/GPIO1_B3", 74: "NPOR", 75: "GND",
    76: "VCC3V3_RTC", 77: "VCC_1V8", 78: "VCC_3V3", 79: "VCC5V0_SYS", 80: "VCC5V0_SYS",
    81: "VCC5V0_SYS", 82: "GND", 83: "GND", 84: "GND", 85: "FEPHY_RXN", 86: "FEPHY_RXP",
    87: "FEPHY_TXN", 88: "FEPHY_TXP", 89: "GND", 90: "GPIO1_D3", 91: "GPIO1_D2", 92: "GPIO1_D1",
    93: "GPIO1_D0", 94: "GPIO1_C7", 95: "GPIO1_C6", 96: "GPIO1_C5", 97: "GPIO1_C4",
    98: "SPI0_MISO_M0/GPIO1_C3", 99: "SPI0_MOSI_M0/GPIO1_C2", 100: "SPI0_CLK_M0/GPIO1_C1",
    101: "SPI0_CS0_M0/GPIO1_C0", 102: "GPIO2_A0", 103: "GPIO2_A1", 104: "GPIO2_A2", 105: "GPIO2_A3",
    106: "GPIO2_A4", 107: "GPIO2_A5", 108: "GPIO2_A6", 109: "GPIO2_A7", 110: "GPIO2_B0",
    111: "GPIO2_B1", 112: "GND",
}

CORE1106_USE = {
    # pin: (net, kind, note)
    3: ("CSI_D3_N", IN, "4-lane: D0-D3 + CK0"), 4: ("CSI_D3_P", IN, ""),
    5: ("CSI_D2_N", IN, ""), 6: ("CSI_D2_P", IN, ""), 7: ("CSI_D1_N", IN, ""), 8: ("CSI_D1_P", IN, ""),
    9: ("CSI_CLK_N", IN, "clock lane CK0 khi gộp 2 D-PHY thành 4 lane [Có thể]"),
    10: ("CSI_CLK_P", IN, ""), 11: ("CSI_D0_N", IN, ""), 12: ("CSI_D0_P", IN, ""),
    22: ("PC_USB_DN", BIDI, "USB 2.0 OTG -> PC"), 23: ("PC_USB_DP", BIDI, ""),
    24: ("PC_VBUS_DET", IN, "VBUS PC qua 10k/18k (như Luckfox Pico Ultra)"),
    26: ("SOC_RECOVERY", IN, "phím RECOVERY, luôn kéo lên 1.8 V"),
    27: ("VIN_SENSE", IN, "đo VIN (ADC 1.8 V): VIN x 8.2/108.2"),
    65: ("LT_SCL", OUT, "I2C2_M0 -> LT7911D (3.3 V)"), 66: ("LT_SDA", BIDI, ""),
    67: ("LT_INT", IN, "ngắt từ LT7911D (IRQ cạnh lên)"),
    70: ("MCU_UART_TX", IN, "UART4_M0 RX <- CH32 USART1 TX"),
    71: ("MCU_UART_RX", OUT, "UART4_M0 TX -> CH32 USART1 RX"),
    72: ("SOC_CON_TX", OUT, "console UART2_M1 (fiq-debugger) -> J503"),
    73: ("SOC_CON_RX", IN, "console RX"),
    74: ("SOC_NPOR", IN, "reset RV1106 (nút SW502)"),
    77: ("VCC_1V8_MOD", PWR_OUT, "1.8 V ra từ module"), 78: ("VCC_3V3_MOD", PWR_OUT, "3.3 V ra, chỉ TP"),
    79: ("5V_SYS", PWR_IN, "4.6-5.2 V, ≤ 1 A"), 80: ("5V_SYS", PWR_IN, ""), 81: ("5V_SYS", PWR_IN, ""),
    85: ("ETH_RX_N", BIDI, "PHY 100M trong RV1106"), 86: ("ETH_RX_P", BIDI, ""),
    87: ("ETH_TX_N", BIDI, ""), 88: ("ETH_TX_P", BIDI, ""),
    90: ("SOC_MCU_RST", OUT, "reset CH32 qua R413 1k; GPIO1_D3 mặc định kéo xuống yếu, R301 4.7k thắng"),
    91: ("MCU_BOOT0", OUT, "kéo lên để CH32 vào bootloader USART1"),
    92: ("MCU_IRQ", IN, "ngắt từ CH32"),
    61: ("LT_RST_N", OUT, "reset LT7911D (reset-gpios, active low); GPIO0_A3 mặc định kéo lên: "
                          "LT7911D chạy ngay khi cấp nguồn"),
    98: ("SPI_MISO", IN, "SPI0_M0 master <- CH32 SPI1"), 99: ("SPI_MOSI", OUT, ""),
    100: ("SPI_SCK", OUT, ""), 101: ("SPI_CS", OUT, ""),
}

CORE1106_NC_NOTE = {
    range(1, 3): "CK1 chỉ dùng ở chế độ 2x2 lane",
    range(13, 21): "miền 1.8 V, không dùng",
    range(30, 36): "codec không dùng",
    range(37, 47): "bản eMMC: pad bị ngắt trên module",
    range(48, 55): "bản Wi-Fi: SDMMC nối vào Wi-Fi trên module",
    range(63, 65): "bản Wi-Fi: dùng cho BT UART",
    range(68, 70): "bản Wi-Fi: dùng cho BT UART",
}


def build_soc(d: Design):
    b = "soc"
    pins = []
    for n in range(1, 113):
        name = CORE1106_NAMES[n]
        side = "L" if n <= 56 else "R"
        if name == "GND":
            pins.append(P(str(n), "GND", PWR_IN, GND, side, src="CORE1106_XLS"))
        elif n in CORE1106_USE:
            net, kind, note = CORE1106_USE[n]
            decap = ("C401", "C402") if n == 79 else (("C402",) if n in (80, 81) else ())
            pins.append(P(str(n), name, kind, net, side, note, decap=decap, src="CORE1106_XLS"))
        else:
            note = "không dùng"
            for rg, txt in CORE1106_NC_NOTE.items():
                if n in rg:
                    note = txt
            if n == 76:
                note = "RTC lấy từ VCC_3V3 qua diode trên module; để trống"
            kind = PWR_IN if n == 76 else BIDI
            pins.append(P(str(n), name, kind, NC, side, note, src="CORE1106_XLS"))
    # right side listed top->bottom as 112..57 (counter-clockwise like the module)
    left = [p for p in pins if int(p.num) <= 56]
    right = sorted([p for p in pins if int(p.num) > 56], key=lambda p: -int(p.num))
    box(d, "U401", "Luckfox Core1106", "Luckfox Core1106 (RV1106G3, 256 MB, eMMC 8 GB, Wi-Fi 6/BT 5.2)",
        "box-v1:Luckfox_Core1106_Castellated_30x30mm_P1.0mm", b,
        "Module SoC RV1106G3: CSI-2 4 lane, H.264, PHY Ethernet 100M, USB 2.0 OTG, Wi-Fi trên module",
        left + right, src="CORE1106_XLS", conf=COTHE,
        note="Footprint: dùng Core1106-SMT của Luckfox (30x30 mm, 112 pad, bước 1.0 mm)")
    C(d, "C401", "22uF 10V", "5V_SYS", GND, b, "Tụ khối VCC5V0_SYS sát module", fp="C0805")
    C(d, "C402", "100nF", "5V_SYS", GND, b, "Tụ cao tần VCC5V0_SYS")
    TP(d, "TP401", "VCC_3V3_MOD", b, "Kiểm tra PMIC module đã lên")

    box(d, "J401", "USB-C PC", "TYPE-C-31-M-12", "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", b,
        "USB-C tới PC: box là thiết bị USB (UVC + mạng USB + điều khiển)",
        usbc16("J401", b, "PC_VBUS", "PC_CC1", "PC_CC2", "PC_USB_DP", "PC_USB_DN", ""),
        lcsc="C165948", src="KICAD_SYM")
    R(d, "R401", "5.1k 1%", "PC_CC1", GND, b, "Rd: box là UFP (sink) phía PC", src="USBC_SPEC")
    R(d, "R402", "5.1k 1%", "PC_CC2", GND, b, "Rd", src="USBC_SPEC")
    box(d, "U402", "USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", b, "ESD USB 2.0 phía PC", [
        P("1", "I/O1", PASSIVE, "PC_USB_DP", "L"), P("2", "GND", PASSIVE, GND, "L"),
        P("3", "I/O2", PASSIVE, "PC_USB_DN", "L"), P("4", "I/O2", PASSIVE, "PC_USB_DN", "R"),
        P("5", "VBUS", PASSIVE, "3V3", "R"), P("6", "I/O1", PASSIVE, "PC_USB_DP", "R"),
    ], lcsc="C7519", src="KICAD_SYM")
    C(d, "C403", "100nF", "3V3", GND, b, "Tụ tại chân VBUS của U402")
    C(d, "C404", "1uF 25V", "PC_VBUS", GND, b, "Tụ VBUS phía PC (UFP ≤ 10 µF)", fp="C0603")
    D(d, "D401", "SMF6.0A", "PC_VBUS", GND, b, "TVS VBUS PC", "Diode_SMD:D_SOD-123F", symbol="D_TVS",
      mpn="SMF6.0A")
    R(d, "R403", "10k", "PC_VBUS", "PC_VBUS_DET", b, "Chia áp VBUS -> USB_VBUSDET (5 V -> 3.2 V)", src="PICO_ULTRA")
    R(d, "R404", "18k", "PC_VBUS_DET", GND, b, "Chia áp VBUSDET", src="PICO_ULTRA")
    C(d, "C405", "100nF", "PC_VBUS_DET", GND, b, "Lọc VBUSDET", src="PICO_ULTRA")

    box(d, "J402", "RJ45 10/100", "HR911105A", "Connector_RJ:RJ45_Hanrun_HR911105A_Horizontal", b,
        "RJ45 có biến áp (magjack) 10/100", [
            P("1", "TD+", PASSIVE, "ETH_TXP_J", "L"), P("2", "TD-", PASSIVE, "ETH_TXN_J", "L"),
            P("3", "RD+", PASSIVE, "ETH_RXP_J", "L"), P("4", "TCT", PASSIVE, "ETH_TCT", "L"),
            P("5", "RCT", PASSIVE, "ETH_RCT", "L"), P("6", "RD-", PASSIVE, "ETH_RXN_J", "L"),
            P("7", "NC", NCPIN, NC, "R"),
            P("8", "BS", PASSIVE, "ETH_BS", "R", "nút Bob-Smith (1 nF/2 kV trong jack) [Có thể]", COTHE),
            P("9", "LED1", PASSIVE, NC, "R", "LED: Core1106 không đưa chân LED của PHY ra", CHUABIET),
            P("10", "LED1", PASSIVE, NC, "R", "", CHUABIET),
            P("11", "LED2", PASSIVE, NC, "R", "", CHUABIET),
            P("12", "LED2", PASSIVE, NC, "R", "", CHUABIET),
            P("SH", "SHIELD", PASSIVE, GND, "R"),
        ], lcsc="C12074", src="KICAD_SYM")
    for ref, a, bb in (("R405", "ETH_TX_P", "ETH_TXP_J"), ("R406", "ETH_TX_N", "ETH_TXN_J"),
                       ("R407", "ETH_RX_P", "ETH_RXP_J"), ("R408", "ETH_RX_N", "ETH_RXN_J")):
        R(d, ref, "0R", a, bb, b, "0R như thiết kế Luckfox: chỗ để thêm lọc/ESD", src="PICO_PLUS_ETH")
    C(d, "C406", "10nF 50V", "ETH_TCT", GND, b, "Tụ center tap TX (PHY kiểu voltage-mode)", src="PICO_PLUS_ETH")
    C(d, "C407", "10nF 50V", "ETH_RCT", GND, b, "Tụ center tap RX", src="PICO_PLUS_ETH")
    C(d, "C408", "1nF 100V", "ETH_BS", GND, b, "Tụ nút Bob-Smith", fp="C0603", src="PICO_PLUS_ETH")

    R(d, "R409", "100k 1%", "VIN", "VIN_SENSE", b, "Chia áp VIN cho SARADC_IN1 (20 V -> 1.52 V)")
    R(d, "R410", "8.2k 1%", "VIN_SENSE", GND, b, "Chia áp VIN")
    C(d, "C409", "10nF", "VIN_SENSE", GND, b, "Lọc ADC VIN")
    R(d, "R411", "10k", "SOC_RECOVERY", "VCC_1V8_MOD", b, "Kéo lên SARADC_IN0 (bắt buộc)", src="PICO_ULTRA")
    C(d, "C410", "1nF C0G", "SOC_RECOVERY", GND, b, "Lọc phím RECOVERY", src="PICO_ULTRA")
    R(d, "R412", "100R", "SOC_RECOVERY", "RECOVERY_KEY", b, "Nối tiếp phím RECOVERY", src="PICO_ULTRA")
    R(d, "R413", "1k", "SOC_MCU_RST", "MCU_NRST", b, "Hạn dòng khi RV1106 và WCH-LinkE cùng lái NRST")


# ---------------------------------------------------------------------------
# Sheet 5: debug, LEDs, buttons, test points
# ---------------------------------------------------------------------------
def build_debug(d: Design):
    b = "debug"
    hdr = "Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical"
    box(d, "J501", "MCU SWD+UART", "PinHeader 1x07 2.54", hdr.format(n=7), b,
        "Header WCH-LinkE cho CH32V305 (SWD 2 dây + UART debug)", [
            P("1", "3V3", PASSIVE, "3V3", "L", "tham chiếu mức cho WCH-LinkE, không cấp ngược"),
            P("2", "SWDIO", PASSIVE, "SWDIO", "L"), P("3", "SWCLK", PASSIVE, "SWCLK", "L"),
            P("4", "GND", PASSIVE, GND, "L"), P("5", "NRST", PASSIVE, "MCU_NRST", "L"),
            P("6", "TX", PASSIVE, "MCU_DBG_TX", "L", "TX của MCU"),
            P("7", "RX", PASSIVE, "MCU_DBG_RX", "L", "RX của MCU"),
        ])
    box(d, "J502", "MCU USB FS", "PinHeader 1x03 2.54", hdr.format(n=3), b,
        "USB Full-Speed thứ hai của CH32V305 (ISP qua USB, thử nghiệm)", [
            P("1", "D+", PASSIVE, "MCU_FS_DP", "L"), P("2", "D-", PASSIVE, "MCU_FS_DN", "L"),
            P("3", "GND", PASSIVE, GND, "L")])
    box(d, "J503", "SoC UART", "PinHeader 1x03 2.54", hdr.format(n=3), b,
        "Console RV1106 (UART2_M1, 115200 8N1, 3.3 V)", [
            P("1", "GND", PASSIVE, GND, "L"), P("2", "TX", PASSIVE, "SOC_CON_TX", "L", "TX của RV1106"),
            P("3", "RX", PASSIVE, "SOC_CON_RX", "L", "RX của RV1106")], src="RV1106_DTS")
    sw = "Button_Switch_SMD:SW_SPST_TL3342"
    _two(d, "SW501", "RECOVERY", "RECOVERY_KEY", GND, sw, b,
         "Giữ khi cấp nguồn: RV1106 vào chế độ loader (rockusb) qua USB-C PC", "SW", mpn="TL3342")
    _two(d, "SW502", "RESET", "SOC_NPOR", GND, sw, b, "Reset RV1106 (NPOR)", "SW", mpn="TL3342")
    for ref, rref, col, rv, net in (("D501", "R501", "RED", "680R", "LED_R"),
                                    ("D502", "R502", "YELLOW", "680R", "LED_Y"),
                                    ("D503", "R503", "GREEN", "560R", "LED_G")):
        R(d, rref, rv, net, net + "_A", b, f"Hạn dòng LED {col} ~2 mA")
        LED(d, ref, col, GND, net + "_A", b, f"LED trạng thái {col} (đỏ: chưa có iPhone, vàng: có HID "
                                             "chưa có hình, xanh: sẵn sàng)",
            mpn=f"LED 0603 {col.lower()}, Vf ≤ 2.2 V")
    for i, net in enumerate(["VIN", "5V2_PHONE", "5V_SYS", "3V3", "1V2", "PHONE_VBUS", GND, GND,
                             "3V3_LT", "1V2_LT_A"], start=1):
        TP(d, f"TP5{i:02d}", net, b, f"Test point {net}")


if __name__ == "__main__":
    dz = build()
    print(f"{len(dz.parts)} parts, {sum(len(p.pins) for p in dz.parts)} pins, "
          f"{len(dz.net_pins())} nets")
