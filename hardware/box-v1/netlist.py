"""iPhone control box v1 (Option B) - single source of truth for the circuit and its bill of materials.

Standard library only. Every component, every pin and every net of the board is
declared here, together with the orderable part behind every component (the BUY
catalogue); `generate.py` turns it into KiCad 8 schematics, SVG sheets, a BOM
and netlists, and `check.py` validates it.

Conventions
-----------
* Reference designators are numbered per sheet: 1xx power, 2xx iPhone port +
  LT7911D, 3xx CH32V305, 4xx Core1106 + Ethernet + PC USB-C, 5xx debug/LEDs.
* A pin is either on a net (`net="NAME"`) or explicitly no-connect (`net=NC`).
* `conf` holds the confidence of the pin *number and function* (on a Pin) or of the
  part choice and value (on a Part), with the same labels as
  docs/research/custom-box.md: CONFIRMED = read from the primary source,
  LIKELY = vendor claim, indirect source or calculated value to re-check against the
  full datasheet, UNKNOWN = not public, must be confirmed. Pin numbers that are not
  public start with "?" (for example "?XTALI") so that they can never be mistaken for
  a real pad number.
* `src` points to an entry of SOURCES (URL + document + section/page).
* `decap` lists the capacitor references that decouple an IC power pin.
* `buy` is the key of the orderable part in BUY. Two-pin helpers derive it from the
  footprint and the value ("R0402:10k"); ICs and connectors name it explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

NC = "__NC__"  # sentinel: pin explicitly left unconnected

CONFIRMED = "Confirmed"
LIKELY = "Likely"
UNKNOWN = "Unknown"

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
                      "C5310990, so they are [Likely] only"),
    "LT7911D_DS": ("https://datasheet.lcsc.com/lcsc/2212091108_LONTIUM-SEMICONDUCTOR-LT7911D_C5310990.pdf",
                   "Lontium LT7911D datasheet R1.4 as published by LCSC for part C5310990 (also mirrored on "
                   "gitcode.com, Open-source-documentation-tutorial/3e28d). Public, not under NDA, but blocked "
                   "by the build environment's proxy: not read here. It holds the full 64-pin table, the "
                   "package drawing and the reference circuit that pins 24-64 and the footprint need"),
    "CORE1106_XLS": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106-PinOut.xls",
                     "Luckfox Core1106-PinOut.xls: 112 pads, pin name, IO power domain, remarks "
                     "(eMMC / Wi-Fi variants disconnect pads 37-46, 48-54, 63-64, 68-69)"),
    "CORE1106_SCH": ("https://github.com/LuckfoxTECH/Luckfox-Pico-docs/blob/main/Hardware/Schematic/Core1106.pdf",
                     "Luckfox Core1106 schematic p.1: VCC5V0_SYS input -> EA3036C (3V3/0V9/1V8) + MP1605 "
                     "(DDR); VCC_3V3/VCC_1V8 exported on pads 78/77; FEPHY_REXT 6.04k on module; "
                     "'USB must always power supply'; Wi-Fi/BT module U6 (SKI.WB800DCS.2) pin WL_BT_ANT "
                     "-> ANT1, a 3-pin part with pins GND/DATA/GND (antenna connector)"),
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


# ---------------------------------------------------------------------------
# Ordering data (single source for bom.csv, the KiCad MPN/LCSC fields and ORDERING.md)
# ---------------------------------------------------------------------------
# Evidence behind an LCSC code, a JLCPCB part class or a unit price. lcsc.com and jlcpcb.com
# are blocked by the proxy of the build environment, so no code could be read from the
# vendor's own page: every LCSC code below is LIKELY, none is CONFIRMED.
EVIDENCE = {
    "SNAP": ("https://github.com/CDFER/JLCPCB-Kicad-Library/tree/bb1df2e/symbols",
             "JLCPCB basic/preferred parts list as mirrored by CDFER/JLCPCB-Kicad-Library (MIT), "
             "commit bb1df2e, data of 2026-04-02: LCSC code, MPN, manufacturer, JLCPCB class "
             "(Basic/Preferred), stock and JLCPCB unit price"),
    "WEB": ("https://www.lcsc.com/",
            "web-search excerpt of the lcsc.com or jlcpcb.com product page, read 2026-09-26 (the pages "
            "themselves are blocked by the build proxy); a price is LCSC's 'from' price, i.e. the "
            "largest quantity break"),
    "ALLOW": ("", "no price found: planning allowance, not a quote"),
    "NONE": ("", "no LCSC listing found: choose at order"),
}
LCSC_CONF = {"SNAP": LIKELY, "WEB": LIKELY}   # a CONFIRMED code would need the vendor page itself

# Assembly classes
SMT, THT, MODULE, PCB = "SMT", "THT", "MODULE", "PCB"


@dataclass(frozen=True)
class Buy:
    manufacturer: str
    mpn: str
    lcsc: str = ""             # LCSC / JLCPCB part code, "" = choose at order
    ev: str = "NONE"           # EVIDENCE key of the LCSC code (and of jlc / price unless price_ev)
    jlc: str = ""              # JLCPCB class: Basic, Preferred; "" = not established (treat as Extended)
    price: Optional[float] = None   # USD per piece, estimate
    price_ev: str = ""         # EVIDENCE key of the price ("" = same as ev)
    assembly: str = SMT        # SMT, THT, MODULE (castellated module), PCB (copper only, nothing to buy)
    note: str = ""

    @property
    def lcsc_conf(self) -> str:
        return LCSC_CONF.get(self.ev, "") if self.lcsc else ""

    @property
    def price_basis(self) -> str:
        return self.price_ev or self.ev


def _uniroyal(mpn, lcsc, cls, price):
    return Buy("UNI-ROYAL (Uniroyal Elec)", mpn, lcsc, "SNAP", cls, price, note="thick film 1 % 62.5 mW 0402")


_R0402 = {
    "0R": _uniroyal("0402WGF0000TCE", "C17168", "Basic", 0.004),
    "100R": _uniroyal("0402WGF1000TCE", "C25076", "Basic", 0.004),
    "560R": Buy("YAGEO", "RC0402FR-07560RL", price=0.01, price_ev="ALLOW",
                note="thick film 1 % 0402; UNI-ROYAL 0402WGF5600TCE is equivalent"),
    "680R": _uniroyal("0402WGF6800TCE", "C25130", "Preferred", 0.004),
    "1k": _uniroyal("0402WGF1001TCE", "C11702", "Basic", 0.004),
    "2.2k": _uniroyal("0402WGF2201TCE", "C25879", "Basic", 0.004),
    "4.7k": _uniroyal("0402WGF4701TCE", "C25900", "Basic", 0.004),
    "5.1k": _uniroyal("0402WGF5101TCE", "C25905", "Basic", 0.004),
    "6.8k": _uniroyal("0402WGF6801TCE", "C25917", "Preferred", 0.004),
    "8.2k": _uniroyal("0402WGF8201TCE", "C25924", "Preferred", 0.004),
    "10k": _uniroyal("0402WGF1002TCE", "C25744", "Basic", 0.005),
    "15k": _uniroyal("0402WGF1502TCE", "C25756", "Basic", 0.004),
    "18k": _uniroyal("0402WGF1802TCE", "C25762", "Preferred", 0.004),
    "20k": _uniroyal("0402WGF2002TCE", "C25765", "Basic", 0.004),
    "22.1k": Buy("YAGEO", "RC0402FR-0722K1L", price=0.01, price_ev="ALLOW",
                 note="thick film 1 % 0402; UNI-ROYAL 0402WGF2212TCE is equivalent"),
    "23.7k": Buy("YAGEO", "RC0402FR-0723K7L", "C327362", "WEB", price=0.01, price_ev="ALLOW",
                 note="thick film 1 % 0402"),
    "24.9k": Buy("YAGEO", "RC0402FR-0724K9L", price=0.01, price_ev="ALLOW",
                 note="thick film 1 % 0402; UNI-ROYAL 0402WGF2492TCE is equivalent"),
    "39k": _uniroyal("0402WGF3902TCE", "C25783", "Preferred", 0.004),
    "100k": _uniroyal("0402WGF1003TCE", "C25741", "Basic", 0.004),
    "1M": _uniroyal("0402WGF1004TCE", "C26083", "Basic", 0.004),
}

_SAMSUNG = "Samsung Electro-Mechanics"
_FH = "FH (Guangdong Fenghua)"

BUY = {
    # --- resistors ------------------------------------------------------------------------
    **{f"R0402:{v}": b for v, b in _R0402.items()},
    **{f"R0402:{v} 1%": b for v, b in _R0402.items()},
    "R0603:10k": Buy("UNI-ROYAL (Uniroyal Elec)", "0603WAF1002T5E", "C25804", "SNAP", "Basic", 0.005,
                     note="thick film 1 % 100 mW 0603"),
    "R1206:1k": Buy("UNI-ROYAL (Uniroyal Elec)", "1206W4F1001T5E", "C4410", "SNAP", "Basic", 0.007,
                    note="thick film 1 % 250 mW 1206"),
    "R1206:10m 1%": Buy("YAGEO", "RL1206FR-7W0R01L", "C155193", "WEB", "", 0.0191,
                        note="current sense 10 mΩ 1 % 0.5 W 1206"),
    # --- capacitors ------------------------------------------------------------------------
    "C0402:100nF": Buy(_SAMSUNG, "CL05B104KO5NNNC", "C1525", "SNAP", "Basic", 0.005, note="16 V X7R 0402"),
    "C0402:100nF 16V": Buy(_SAMSUNG, "CL05B104KO5NNNC", "C1525", "SNAP", "Basic", 0.005, note="16 V X7R 0402"),
    "C0402:100nF 50V": Buy(_SAMSUNG, "CL05B104KB54PNC", "C307331", "SNAP", "Basic", 0.007, note="50 V X7R 0402"),
    "C0402:1uF 25V": Buy(_SAMSUNG, "CL05A105KA5NQNC", "C52923", "SNAP", "Basic", 0.007, note="25 V X5R 0402"),
    "C0402:1nF": Buy(_FH, "0402B102K500NT", "C1523", "SNAP", "Basic", 0.005, note="50 V X7R 0402"),
    "C0402:1nF C0G": Buy("Murata", "GRM1555C1H102JA01D", "C76947", "WEB", price=0.01, price_ev="ALLOW",
                         note="50 V C0G 0402"),
    "C0402:10nF": Buy(_SAMSUNG, "CL05B103KB5NNNC", "C15195", "SNAP", "Basic", 0.005, note="50 V X7R 0402"),
    "C0402:10nF 50V": Buy(_SAMSUNG, "CL05B103KB5NNNC", "C15195", "SNAP", "Basic", 0.005, note="50 V X7R 0402"),
    "C0402:18pF C0G": Buy(_FH, "0402CG180J500NT", "C1549", "SNAP", "Basic", 0.004, note="50 V C0G ±5 % 0402"),
    "C0603:47nF 50V": Buy(_SAMSUNG, "CL10B473KB8NNNC", "C1622", "SNAP", "Basic", 0.008, note="50 V X7R 0603"),
    "C0603:100nF 50V": Buy("YAGEO", "CC0603KRX7R9BB104", "C14663", "SNAP", "Basic", 0.006, note="50 V X7R 0603"),
    "C0603:1uF 50V": Buy(_SAMSUNG, "CL10A105KB8NNNC", "C15849", "SNAP", "Basic", 0.011, note="50 V X5R 0603"),
    "C0603:10uF 10V": Buy(_SAMSUNG, "CL10A106KP8NNNC", "C19702", "SNAP", "Basic", 0.011, note="10 V X5R 0603"),
    "C0805:10uF 25V": Buy(_SAMSUNG, "CL21A106KAYNNNE", "C15850", "SNAP", "Basic", 0.023, note="25 V X5R 0805"),
    "C0805:22uF 25V X5R": Buy(_SAMSUNG, "CL21A226MAQNNNE", "C45783", "SNAP", "Basic", 0.033,
                              note="25 V X5R 0805"),
    "C1206:10uF 50V X7R": Buy(_SAMSUNG, "CL31B106KBHNNNE", "C89632", "WEB", "", 0.0556,
                              note="50 V X7R 1206 (price: LCSC 100+ break)"),
    "C1206:22uF 25V X5R": Buy(_SAMSUNG, "CL31A226KAHNNNE", "C12891", "SNAP", "Basic", 0.056,
                              note="25 V X5R 1206"),
    "C1206:1nF 2kV X7R": Buy(_FH, "1206B102K202NT", "C9196", "SNAP", "Basic", 0.012, note="2 kV X7R 1206"),
    "CP6.3x7.7:47uF 35V": Buy("KNSCHA", "RVT47UF35V67RV0039", "C2836440", "WEB", "", 0.019,
                              note="aluminium electrolytic 47 µF 35 V ±20 %, 6.3x7.7 mm"),
    # --- inductors, ferrites, fuse --------------------------------------------------------------
    "L10x10:10uH": Buy("Bourns", "SRP1038A-100M", "C780184", "WEB", "", 0.436,
                       note="10 µH shielded, rated 7.5 A per LCSC excerpt; replaces SRP1038C-100M, which "
                            "Bourns has moved to end of life; alternative SRP1038CC-100M; check the land "
                            "pattern against the SRP1038C footprint"),
    "L4x4:2.2uH": Buy("Bourns", "SRN4018-2R2M", "C913207", "WEB", "", 0.1445,
                      note="2.2 µH semi-shielded 4x4x1.8 mm, rated 2.9 A, 44 mΩ max per LCSC excerpt"),
    "FB0603:600R@100MHz": Buy("Murata", "BLM18KG601SN1D", "C85833", "WEB", "", 0.011,
                              note="600 Ω @ 100 MHz, 1.3 A, 0.15 Ω, 0603"),
    "F1206:5A 32V": Buy("Littelfuse", "0466005.NRHF", "C57525", "WEB", "", 0.0326,
                        note="466 series very fast acting 5 A 32 V 1206 (halogen-free version of 0466005.NR)"),
    # --- diodes, LEDs ---------------------------------------------------------------------------
    "SMBJ20A": Buy("hongjiacheng", "SMBJ20A", "C19077575", "SNAP", "Preferred", 0.035,
                   note="600 W TVS, 20 V standoff, 32.4 V clamp at 18.5 A, SMB"),
    "BZT52C10": Buy("Diodes Incorporated", "BZT52C10-7-F", "C155227", "WEB", price=0.02, price_ev="ALLOW",
                    note="10 V 500 mW zener SOD-123"),
    "SS34": Buy("MDD (Microdiode Electronics)", "SS34", "C8678", "SNAP", "Basic", 0.031,
                note="3 A 40 V Schottky SMA"),
    "SMF6.0A": Buy("Littelfuse", "SMF6.0A", "C207260", "WEB", price=0.05, price_ev="ALLOW",
                   note="200 W unidirectional TVS, 6 V standoff, SOD-123FL"),
    "LED:RED": Buy("Hubei KENTO Elec", "KT-0603R", "C2286", "SNAP", "Basic", 0.009,
                   note="red 615-630 nm, Vf 1.8-2.4 V @ 20 mA"),
    "LED:YELLOW": Buy("Foshan NationStar", "NCD0603Y2", "C89811", "SNAP", "Preferred", 0.021,
                      note="yellow 586-595 nm, Vf 1.6-2.6 V"),
    "LED:GREEN": Buy("Hubei KENTO Elec", "KT-0603YG", "C2289", "WEB", "", 0.0059,
                     note="yellow-green (AlInGaP class) 0603; confirm Vf on the datasheet"),
    # --- transistors ------------------------------------------------------------------------------
    "AO4407A": Buy("Alpha & Omega Semiconductor", "AO4407A", "C16072", "WEB", "", 0.107,
                   note="P-MOSFET -30 V; RDS(on) < 13 mΩ @ -10 V, about 18 mΩ @ -4.5 V per datasheet excerpt"),
    "2N7002": Buy("Changjiang Electronics Tech (CJ)", "2N7002", "C8545", "SNAP", "Basic", 0.018),
    # --- ICs ----------------------------------------------------------------------------------------
    "CH224K": Buy("WCH (Jiangsu Qin Heng)", "CH224K", "C970725", "WEB", "", 0.33),
    "LMR33630ADDAR": Buy("Texas Instruments", "LMR33630ADDAR", "C841384", "WEB", "", 0.5163),
    "TLV62569DBVR": Buy("Texas Instruments", "TLV62569DBVR", "C141836", "WEB", "", 0.25, price_ev="ALLOW",
                        note="the search excerpt showed $0.0424, which looks implausibly low; allowance used"),
    "INA180A2IDBVR": Buy("Texas Instruments", "INA180A2IDBVR", "C192764", "WEB", "", 0.1065),
    "LT7911D": Buy("Lontium Semiconductor", "LT7911D", "C5310990", "WEB", "", 4.9,
                   note="price from a Global Sources excerpt ($4.9, chip only); firmware for CSI output "
                        "and the full datasheet come from Lontium or its distributor"),
    "TPD4E05U06DQAR": Buy("Texas Instruments", "TPD4E05U06DQAR", "C138714", "WEB", "", 0.0361),
    "USBLC6-2SC6": Buy("STMicroelectronics", "USBLC6-2SC6", "C7519", "WEB", "", 0.0896),
    "CH32V305RBT6": Buy("WCH (Jiangsu Qin Heng)", "CH32V305RBT6", "C5187529", "WEB", "", 1.3334),
    "CORE1106": Buy("Luckfox", "Core1106 (RV1106G3, 256 MB, 8 GB eMMC, Wi-Fi 6/BT 5.2 variant)", "", "NONE", "n/a",
                    27.0, price_ev="WEB", assembly=MODULE,
                    note="not stocked by LCSC: buy from Luckfox or Waveshare and consign; Waveshare lists the "
                         "variants as Core11060208 / Core11060408 / Core11061208 / Core11061408 (which code is "
                         "G3 + eMMC + Wi-Fi was not verified here: check the store page); price is the top of "
                         "the $16.34-26.99 range seen in search excerpts"),
    # --- crystals ---------------------------------------------------------------------------------------
    "XTAL8M": Buy("YXC (Yangxing Tech)", "X32258MOB4SI", "C2682775", "WEB", "", 0.0791,
                  note="8 MHz SMD3225-4P; 'MO' = 12 pF by analogy with X322525MOB4SI: confirm CL = 12 pF"),
    "XTAL25M": Buy("YXC (Yangxing Tech)", "X322525MOB4SI", "C9006", "SNAP", "Basic", 0.074,
                   note="25 MHz 12 pF ±10 ppm SMD3225-4P; placeholder until Lontium gives the crystal spec"),
    # --- connectors, switches -------------------------------------------------------------------------------
    "TYPE-C-31-M-12": Buy("Korean Hroparts Elec", "TYPE-C-31-M-12", "C165948", "WEB", "", 0.096,
                          note="USB-C 16P USB 2.0 receptacle, SMD, 5 A"),
    "TYPE-C-31-M-04": Buy("Korean Hroparts Elec", "TYPE-C-31-M-04", "C129018", "WEB", "", 0.3594,
                          note="USB-C 24P USB 3.1 receptacle, hybrid (A row SMD, B row through-hole); replaces "
                               "the Molex 105450-0101 of the first draft, whose all-SMD B row sits behind a "
                               "keep-out and cannot be escaped without via-in-pad"),
    "HR911105A": Buy("HANRUN (Zhongshan HanRun Elec)", "HR911105A", "C12074", "WEB", "", 0.9082,
                     assembly=THT, note="RJ45 with 10/100 magnetics and LEDs, through-hole"),
    "HDR1x07": Buy("Würth Elektronik", "61300711121", price=0.10, price_ev="ALLOW", assembly=THT,
                   note="WR-PHD 2.54 mm 1x7 straight; any 2.54 mm 1x7 header fits"),
    "HDR1x03": Buy("Würth Elektronik", "61300311121", price=0.05, price_ev="ALLOW", assembly=THT,
                   note="WR-PHD 2.54 mm 1x3 straight; any 2.54 mm 1x3 header fits"),
    "TS1187A": Buy("XKB Connection", "TS-1187A-B-A-B", "C318884", "SNAP", "Basic", 0.025,
                   note="SMD tactile 5.1 x 5.1 mm, 1.5 mm actuator, 160 gf; smaller and cheaper than the "
                        "TL3342 of the first draft"),
    # --- PCB features (nothing to buy) ---------------------------------------------------------------------
    "PCB:TP": Buy("-", "none (copper test pad)", assembly=PCB),
    "PCB:SJ": Buy("-", "none (solder jumper on the PCB)", assembly=PCB),
}


@dataclass
class Pin:
    num: str
    name: str
    kind: str
    net: str
    side: str = "L"          # L, R, T, B on the box symbol
    note: str = ""
    conf: str = CONFIRMED
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
    conf: str = CONFIRMED   # confidence of the part choice / values
    buy: str = ""           # key into BUY
    order: Optional[Buy] = None   # resolved by build()

    @property
    def is_ic(self) -> bool:
        return self.ref[0] == "U"

    @property
    def manufacturer(self) -> str:
        return self.order.manufacturer if self.order else ""

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
    "power": "Power: PD input, rails, iPhone VBUS switch",
    "iphone": "iPhone USB-C + LT7911D",
    "mcu": "CH32V305 (HID)",
    "soc": "Core1106 (RV1106) + Ethernet + PC USB-C",
    "debug": "Debug, LEDs, buttons, test points",
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


def _two(d, ref, value, n1, n2, fp, block, desc, symbol, buy="", dnp=False,
         note="", names=("1", "2"), conf=CONFIRMED, src=""):
    return d.add(Part(ref=ref, value=value, mpn="", footprint=fp, block=block, desc=desc,
                      pins=[Pin("1", names[0], PASSIVE, n1, "L"),
                            Pin("2", names[1], PASSIVE, n2, "R")],
                      dnp=dnp, note=note, symbol=symbol, conf=conf, src=src, buy=buy))


def R(d, ref, value, n1, n2, block, desc, fp="R0402", **kw):
    kw.setdefault("buy", f"{fp}:{value}")
    return _two(d, ref, value, n1, n2, FP.get(fp, fp), block, desc, "R", **kw)


def C(d, ref, value, n1, n2, block, desc, fp="C0402", **kw):
    kw.setdefault("buy", f"{fp}:{value}")
    return _two(d, ref, value, n1, n2, FP.get(fp, fp), block, desc, "C", **kw)


def L(d, ref, value, n1, n2, block, desc, fp, **kw):
    return _two(d, ref, value, n1, n2, fp, block, desc, "L", **kw)


def FB(d, ref, n1, n2, block, desc, **kw):
    return _two(d, ref, "600R@100MHz", n1, n2, FP["FB0603"], block, desc, "FB",
                buy="FB0603:600R@100MHz", **kw)


def D(d, ref, value, k, a, block, desc, fp, symbol="D", **kw):
    """Diode: pin 1 = K, pin 2 = A (KiCad Device:D convention)."""
    kw.setdefault("buy", value)
    return _two(d, ref, value, k, a, fp, block, desc, symbol, names=("K", "A"), **kw)


def LED(d, ref, value, k, a, block, desc, **kw):
    return _two(d, ref, value, k, a, FP["LED0603"], block, desc, "LED", names=("K", "A"),
                buy=f"LED:{value}", **kw)


def TP(d, ref, net, block, desc):
    return d.add(Part(ref=ref, value=net, mpn="", footprint="TestPoint:TestPoint_Pad_D1.5mm",
                      block=block, desc=desc, pins=[Pin("1", "TP", PASSIVE, net, "L")],
                      symbol="TP", buy="PCB:TP"))


def box(d, ref, value, buy, fp, block, desc, pins, **kw):
    return d.add(Part(ref=ref, value=value, mpn="", footprint=fp, block=block, desc=desc,
                      pins=pins, symbol="box", buy=buy, **kw))


P = Pin

# ---------------------------------------------------------------------------
# net metadata
# ---------------------------------------------------------------------------
RAILS = {
    "GND": "0 V",
    "VBUS_IN": "5-20 V (PD, 9 V by default)",
    "VIN": "5-20 V after the fuse",
    "5V2_PHONE": "5.2 V / 3 A for the iPhone",
    "5V_SYS": "5.0 V / 3 A system",
    "3V3": "3.3 V / 2 A",
    "1V2": "1.2 V / 2 A (LT7911D)",
    "3V3_LT": "3.3 V filtered for the LT7911D",
    "1V2_LT_A": "1.2 V filtered (LT7911D analog/PLL)",
    "VDDA_MCU": "3.3 V filtered for the CH32V305 VDDA",
    "CH224_VDD": "3.3 V internal shunt of the CH224K",
    "PHONE_VBUS": "iPhone port VBUS (0 or 5.2 V)",
    "PC_VBUS": "VBUS from the PC (5 V)",
    "VCC_1V8_MOD": "1.8 V output of the Core1106 (≤ 300 mA)",
    "VCC_3V3_MOD": "3.3 V output of the Core1106 (≤ 300 mA): I2C pull-ups, TP",
}

DIFF = [
    # (P net, N net, Zdiff, description)
    ("SS_TX1_P", "SS_TX1_N", 100, "DP lane from the iPhone (receptacle TX1 pair, A2/A3)"),
    ("SS_RX1_P", "SS_RX1_N", 100, "DP lane from the iPhone (RX1 pair, B11/B10)"),
    ("SS_TX2_P", "SS_TX2_N", 100, "DP lane from the iPhone (TX2 pair, B2/B3)"),
    ("SS_RX2_P", "SS_RX2_N", 100, "DP lane from the iPhone (RX2 pair, A11/A10)"),
    ("LT_AUX_P", "LT_AUX_N", 100, "DP AUX after the AC capacitors"),
    ("PHONE_SBU1", "PHONE_SBU2", 100, "SBU1/SBU2 = DP AUX before the capacitors"),
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
    ("ETH_TXP_J", "ETH_TXN_J", 100, "TX after the 0R, into the magnetics"),
    ("ETH_RXP_J", "ETH_RXN_J", 100, "RX after the 0R, into the magnetics"),
]


# Net classes of the board (JLC04161H-7628 stack-up, README §5 and §11): widths for 100 Ω / 90 Ω on
# L1 over the GND plane L2. name: (track, clearance, diff-pair width, diff-pair gap, via Ø, via drill),
# in mm. Used by generate.py (project file) and pcb.py (board).
NETCLASSES = {
    "Default": (0.15, 0.15, 0.2, 0.15, 0.55, 0.3),
    "DIFF_100R": (0.2, 0.15, 0.2, 0.15, 0.55, 0.3),
    "USB_90R": (0.25, 0.15, 0.25, 0.15, 0.55, 0.3),
    "PWR_1A": (0.5, 0.2, 0.2, 0.15, 0.8, 0.4),
    # supplies that reach 0.5 mm-pitch pins (LQFP, QFN): no wider than the pins, pours add copper
    "PWR_FINE": (0.3, 0.15, 0.2, 0.15, 0.55, 0.3),
    "PWR_3A": (0.8, 0.2, 0.2, 0.15, 0.8, 0.4),
    # the 0.3 mm VBUS pads of the iPhone receptacle: thin where it leaves the pads, a pour carries 3 A
    "PWR_3A_FINE": (0.3, 0.15, 0.2, 0.15, 0.8, 0.4),
}
# (class, glob pattern), first match wins
NETCLASS_PATTERNS = (
    [("DIFF_100R", p) for p in ("SS_*", "CSI_*", "LT_AUX_*", "PHONE_SBU*", "ETH_TX*", "ETH_RX*")] +
    [("USB_90R", p) for p in ("PHONE_USB_D*", "PC_USB_D*", "MCU_FS_D*")] +
    [("PWR_3A_FINE", "PHONE_VBUS")] +
    [("PWR_3A", p) for p in ("VBUS_IN", "VIN", "5V2_PHONE", "PSW_*", "5V_SYS", "U102_SW", "U103_SW",
                             "PCPWR_A")] +
    [("PWR_FINE", p) for p in ("3V3", "1V2", "3V3_LT", "1V2_LT_A", "VDDA_MCU", "VCC_3V3_MOD", "GND")] +
    [("PWR_1A", p) for p in ("U104_SW", "U105_SW", "PC_VBUS")]
)


def netclass_of(net: str) -> str:
    import fnmatch
    for cls, pat in NETCLASS_PATTERNS:
        if fnmatch.fnmatchcase(net, pat):
            return cls
    return "Default"


def build() -> Design:
    d = Design()
    build_power(d)
    build_iphone(d)
    build_mcu(d)
    build_soc(d)
    build_debug(d)
    _net_info(d)
    _attach_orders(d)
    return d


def _attach_orders(d: Design):
    """Resolve every part's BUY key; the MPN and LCSC fields of the part come from BUY only."""
    for p in d.parts:
        p.order = BUY.get(p.buy)
        if p.order is not None:
            p.mpn = p.order.mpn
            p.lcsc = p.order.lcsc


def _net_info(d: Design):
    for name, v in RAILS.items():
        d.nets[name] = NetInfo(name, "gnd" if name == GND else "rail", rail_v=v, desc=v)
    for p, n, z, desc in DIFF:
        d.nets[p] = NetInfo(p, "diff", desc, pair=n, zdiff=z)
        d.nets[n] = NetInfo(n, "diff", desc, pair=p, zdiff=z)
    for name in ("HSE_IN", "HSE_OUT", "LT_XI", "LT_XO"):
        d.nets[name] = NetInfo(name, "clock", "crystal")
    for name in ("CC1_SENSE", "CC2_SENSE", "PHONE_VBUS_SENSE", "PHONE_ISENSE", "VIN_SENSE",
                 "PC_VBUS_DET", "SOC_RECOVERY"):
        d.nets[name] = NetInfo(name, "analog", "ADC")
    for net in d.net_pins():
        d.nets.setdefault(net, NetInfo(net, "signal"))


# ---------------------------------------------------------------------------
# Sheet 1: power
# ---------------------------------------------------------------------------
def usbc16(ref, block, vbus, cc1, cc2, dp, dn, desc):
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
        "USB-C port that takes PD power from the charger (power + CC only)",
        usbc16("J101", b, "VBUS_IN", "PD_CC1", "PD_CC2", None, None, ""),
        src="KICAD_SYM",
        note="D+/D- left open: the CH224K runs in PD-only mode (CH224 datasheet §5.5)")
    _two(d, "F101", "5A 32V", "VBUS_IN", "VIN", "Fuse:Fuse_1206_3216Metric", b,
         "Very fast 5 A / 32 V input fuse", "F", buy="F1206:5A 32V", conf=LIKELY)
    D(d, "D101", "SMBJ20A", "VIN", GND, b,
      "TVS on VIN: 20 V standoff, 32.4 V clamp, below the 36 V input rating of the LMR33630", "Diode_SMD:D_SMB",
      symbol="D_TVS")
    _two(d, "C101", "47uF 35V", "VIN", GND, "Capacitor_SMD:CP_Elec_6.3x7.7", b,
         "VIN bulk capacitor, damps ringing when a long cable is hot-plugged", "CP",
         buy="CP6.3x7.7:47uF 35V")

    # CH224K PD sink (datasheet §6.2 reference, resistor mode)
    box(d, "U101", "CH224K", "CH224K", "Package_SO:SSOP-10-1EP_3.9x4.9mm_P1mm_EP2.1x3.3mm", b,
        "PD trigger (sink) for the power input", [
            P("1", "VDD", PWR_IN, "CH224_VDD", "L", "internal 3.3 V shunt, fed from VIN through 1 kΩ",
              decap=("C102",)),
            P("2", "CFG2", PASSIVE, NC, "L", "resistor mode: CFG2/CFG3 must stay open (§5.2.1)"),
            P("3", "CFG3", PASSIVE, NC, "L", "same as CFG2"),
            P("4", "DP", BIDI, "CH224_DPDM", "L", "DP-DM shorted: PD only (§5.5)"),
            P("5", "DM", BIDI, "CH224_DPDM", "L", "DP-DM shorted"),
            P("6", "CC2", BIDI, "CH224_CC2", "R", "through R105 0R to J101.B5", src="CH224KICAD"),
            P("7", "CC1", BIDI, "CH224_CC1", "R", "through R104 0R to J101.A5", src="CH224KICAD"),
            P("8", "VBUS", PASSIVE, NC, "R", "left open: PD-only mode allows it (§5.5), and the pin is "
              "rated 13.5 V, below VIN at 15-20 V"),
            P("9", "CFG1", PASSIVE, "CH224_CFG1", "R", "6.8 kΩ to GND = request 9 V"),
            P("10", "PG", OC, "PD_PG", "R", "open drain, low = requested voltage present"),
            P("11", "GND", PWR_IN, GND, "R", "EPAD (the datasheet calls it pin 0)"),
        ], src="CH224")
    R(d, "R101", "1k", "VIN", "CH224_VDD", b, "CH224K VDD feed resistor (datasheet §6.2)",
      fp="R1206", note="1206 (0.25 W): 0.14 W at 15 V; a 20 V request (R103 open) would need 0.28 W, so "
      "requests stay at 9-15 V (Option B removes U101)", src="CH224")
    C(d, "C102", "1uF 50V", "CH224_VDD", GND, b, "CH224K VDD capacitor", fp="C0603", src="CH224")
    R(d, "R103", "6.8k 1%", "CH224_CFG1", GND, b,
      "Requested voltage: 6.8k=9V (default), 24k=12V, 56k=15V, open=20V", src="CH224")
    R(d, "R104", "0R", "PD_CC1", "CH224_CC1", b, "Option A: charger CC to the CH224K")
    R(d, "R105", "0R", "PD_CC2", "CH224_CC2", b, "Option A: charger CC to the CH224K")
    R(d, "R106", "0R", "PD_CC1", "LT_PDCC1", b,
      "Option B: charger CC to the second PD port of the LT7911D", dnp=True, conf=UNKNOWN)
    R(d, "R107", "0R", "PD_CC2", "LT_PDCC2", b, "Option B (as R106)", dnp=True, conf=UNKNOWN)
    R(d, "R108", "10k", "PD_PG", "3V3", b, "Pull-up for PG (open drain) to MCU PB12")
    box(d, "U107", "TPD4E05U06DQA", "TPD4E05U06DQAR", "Package_SON:USON-10_2.5x1.0mm_P0.5mm", b,
        "ESD for the CC lines of J101 (charger) and J401 (PC), between the two rear ports (flow-through)", [
            P("1", "D1+", PASSIVE, "PD_CC1", "L"), P("2", "D1-", PASSIVE, "PD_CC2", "L"),
            P("3", "GND", PWR_IN, GND, "L"), P("4", "D2+", PASSIVE, "PC_CC1", "L"),
            P("5", "D2-", PASSIVE, "PC_CC2", "L"), P("6", "NC", NCPIN, NC, "R", "flow-through pad"),
            P("7", "NC", NCPIN, NC, "R"), P("8", "GND", PWR_IN, GND, "R"),
            P("9", "NC", NCPIN, NC, "R"), P("10", "NC", NCPIN, NC, "R"),
        ], src="KICAD_SYM")

    # U102: VIN -> 5V2_PHONE (LMR33630A, 400 kHz)
    def lmr(ref, out, fbb, en_top, en_bot, desc, cin, cvcc, cboot, lref, lval, lisat, couts,
            rfbt, rfbb, ren1, ren2, cinhf):
        box(d, ref, "LMR33630ADDA", "LMR33630ADDAR",
            "Package_SO:Texas_HSOP-8-1EP_3.9x4.9mm_P1.27mm_ThermalVias", b, desc, [
                P("1", "GND", PWR_IN, GND, "L"),
                P("2", "VIN", PWR_IN, "VIN", "L", decap=tuple(cin) + (cinhf,)),
                P("3", "EN", IN, f"{ref}_EN", "L", f"UVLO divider {en_top}/{en_bot}"),
                P("4", "PG", OC, NC, "L", "not used"),
                P("5", "FB", IN, f"{ref}_FB", "R"),
                P("6", "VCC", PWR_OUT, f"{ref}_VCC", "R", "internal LDO, 1 µF capacitor"),
                P("7", "BOOT", PASSIVE, f"{ref}_BOOT", "R"),
                P("8", "SW", OUT, f"{ref}_SW", "R"),
                P("9", "EP", PWR_IN, GND, "R", "thermal pad = GND"),
            ], src="KICAD_SYM", conf=LIKELY,
            note="VREF 1.0 V, EN threshold ~1.2 V: re-read TI datasheet SNVSAX4 before ordering")
        for c in cin:
            C(d, c, "10uF 50V X7R", "VIN", GND, b, f"{ref} input capacitor", fp="C1206")
        C(d, cinhf, "100nF 50V", "VIN", GND, b, f"High-frequency capacitor at the {ref} VIN/GND pins",
          fp="C0603")
        C(d, cvcc, "1uF 25V", f"{ref}_VCC", GND, b, f"{ref} internal LDO capacitor")
        C(d, cboot, "100nF 16V", f"{ref}_BOOT", f"{ref}_SW", b, f"{ref} bootstrap capacitor")
        L(d, lref, lval, f"{ref}_SW", out, b, f"{ref} buck inductor, Isat ≥ {lisat}",
          "Inductor_SMD:L_Bourns_SRP1038C_10.0x10.0mm", buy="L10x10:10uH", conf=LIKELY)
        for c in couts:
            C(d, c, "22uF 25V X5R", out, GND, b, f"{ref} output capacitor", fp="C1206")
        R(d, rfbt, "100k 1%", out, f"{ref}_FB", b, f"{ref} upper feedback resistor")
        R(d, rfbb, fbb, f"{ref}_FB", GND, b, f"{ref} lower feedback resistor: Vout = 1.0 V x (1 + 100k/Rfbb)")
        R(d, ren1, en_top, "VIN", f"{ref}_EN", b, f"{ref} EN divider (top)")
        R(d, ren2, en_bot, f"{ref}_EN", GND, b, f"{ref} EN divider (bottom)")

    lmr("U102", "5V2_PHONE", "23.7k 1%", "100k", "20k",
        "Buck VIN -> 5V2_PHONE (5.22 V / 3 A) for the iPhone; starts when VIN > ~7.2 V",
        ["C103", "C104"], "C106", "C107", "L101", "10uH", "5 A", ["C108", "C109", "C110"],
        "R109", "R110", "R111", "R112", "C105")
    lmr("U103", "5V_SYS", "24.9k 1%", "100k", "39k",
        "Buck VIN -> 5V_SYS (5.02 V / 3 A) for the Core1106 and the rails; starts when VIN > ~4.3 V",
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
        ], src="KICAD_SYM", conf=LIKELY, note="VREF 0.6 V, 1.5 MHz: re-read TI datasheet SLVSDI0")
        C(d, cin, "10uF 10V", "5V_SYS", GND, b, f"{ref} input capacitor", fp="C0603")
        C(d, cinhf, "100nF", "5V_SYS", GND, b, f"{ref} high-frequency input capacitor")
        L(d, lref, "2.2uH", f"{ref}_SW", out, b, f"{ref} inductor, Isat ≥ 3 A, 4x4 mm",
          "Inductor_SMD:L_Bourns-SRN4018", buy="L4x4:2.2uH", conf=LIKELY)
        for c in couts:
            C(d, c, "22uF 25V X5R", out, GND, b, f"{ref} output capacitor", fp="C0805")
        R(d, rtop, "100k 1%", out, f"{ref}_FB", b, f"{ref} upper feedback resistor")
        R(d, rbot, rbot_val, f"{ref}_FB", GND, b, f"{ref} lower feedback resistor: Vout = 0.6 V x (1 + 100k/Rbot)")

    tlv("U104", "3V3", "U104_EN", "C119", "C120", "L103", ["C121", "C122"], "R118", "R119",
        "22.1k 1%", "Buck 5V_SYS -> 3V3 (3.315 V / 2 A)")
    R(d, "R117", "100k", "5V_SYS", "U104_EN", b, "U104 EN: on as soon as 5V_SYS is present")
    tlv("U105", "1V2", "U105_EN", "C123", "C124", "L104", ["C126", "C127"], "R121", "R122",
        "100k 1%", "Buck 5V_SYS -> 1V2 (1.2 V / 2 A) for the LT7911D, after 3V3")
    R(d, "R120", "100k", "3V3", "U105_EN", b, "U105 EN from 3V3 through an RC: 1V2 comes up ~4 ms after 3V3")
    C(d, "C125", "100nF", "U105_EN", GND, b, "U105 EN delay capacitor (τ = 10 ms)")

    # iPhone VBUS switch: back-to-back P-MOSFET, gate driven by 2N7002
    JP = "Jumper:SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm"
    box(d, "JP101", "PSW_SRC", "PCB:SJ", JP, b,
        "iPhone supply select: 1-2 = 5V2_PHONE (Option A, default), 2-3 = VIN (Option B)", [
            P("1", "A", PASSIVE, "5V2_PHONE", "L"),
            P("2", "C", PASSIVE, "PSW_IN", "R"),
            P("3", "B", PASSIVE, "VIN", "L"),
        ], note="Make the pads ≥ 2 mm wide with a thick solder coat: 3 A")
    sop8 = "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
    for ref, dnet, desc in (("Q101", "PSW_IN", "Source-side P-MOSFET (back-to-back)"),
                            ("Q102", "PSW_OUT", "iPhone-side P-MOSFET (back-to-back)")):
        box(d, ref, "AO4407A", "AO4407A", sop8, b, desc + ": -30 V, ~11 mΩ @ -10 V", [
            P("1", "S", PASSIVE, "PSW_S", "L"), P("2", "S", PASSIVE, "PSW_S", "L"),
            P("3", "S", PASSIVE, "PSW_S", "L"), P("4", "G", IN, "PSW_G", "L"),
            P("5", "D", PASSIVE, dnet, "R"), P("6", "D", PASSIVE, dnet, "R"),
            P("7", "D", PASSIVE, dnet, "R"), P("8", "D", PASSIVE, dnet, "R"),
        ], src="KICAD_SYM", conf=LIKELY,
            note="Standard SO-8 MOSFET pinout (S=1-3, G=4, D=5-8), as the KiCad IRF7404 symbol")
    R(d, "R123", "100k", "PSW_S", "PSW_G", b, "Gate pulled up to the common source: MOSFETs off by default")
    C(d, "C128", "47nF 50V", "PSW_S", "PSW_G", b, "Soft start (~0.5 ms), limits the charging current into the iPhone",
      fp="C0603")
    D(d, "D102", "BZT52C10", "PSW_S", "PSW_G", b, "10 V zener clamping Vgs in Option B (VIN up to 20 V)",
      "Diode_SMD:D_SOD-123", symbol="D_Zener")
    R(d, "R124", "10k", "PSW_G", "PSW_GD", b, "Gate resistor: Vgs = -Vin x 100k/110k")
    box(d, "Q103", "2N7002", "2N7002", "Package_TO_SOT_SMD:SOT-23", b, "N-MOSFET pulling the gate down", [
        P("1", "G", IN, "PHONE_VBUS_EN", "L"), P("2", "S", PASSIVE, GND, "R"),
        P("3", "D", PASSIVE, "PSW_GD", "R")], src="KICAD_SYM")
    R(d, "R125", "100k", "PHONE_VBUS_EN", GND, b, "iPhone VBUS off by default")
    R(d, "R127", "0R", "MCU_VBUS_EN", "PHONE_VBUS_EN", b, "Option A: the CH32V305 switches the iPhone VBUS")
    R(d, "R128", "0R", "LT_VBUS_EN", "PHONE_VBUS_EN", b,
      "Option B: an LT7911D GPIO switches the iPhone VBUS", dnp=True, conf=UNKNOWN)
    R(d, "R126", "10m 1%", "PSW_OUT", "PHONE_VBUS", b, "iPhone charging current shunt (Kelvin)", fp="R1206")
    box(d, "U106", "INA180A2", "INA180A2IDBVR", "Package_TO_SOT_SMD:SOT-23-5", b,
        "Shunt current amplifier, gain 50: 3 A -> 1.5 V", [
            P("1", "OUT", OUT, "PHONE_ISENSE", "R"),
            P("2", "GND", PWR_IN, GND, "L"),
            P("3", "IN+", IN, "PSW_OUT", "L", "Kelvin at R126, MOSFET side"),
            P("4", "IN-", IN, "PHONE_VBUS", "L", "Kelvin at R126, iPhone side"),
            P("5", "V+", PWR_IN, "3V3", "L", decap=("C129",)),
        ], src="KICAD_SYM", conf=LIKELY)
    C(d, "C129", "100nF", "3V3", GND, b, "U106 supply capacitor")
    C(d, "C130", "1nF", "PHONE_ISENSE", GND, b, "U106 output filter before the ADC")

    # Development option: power the box from the PC USB-C port
    box(d, "JP102", "PC_PWR", "PCB:SJ", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm", b,
        "Open by default. Bridge it to power the box from the PC (only when NO charger is on J101)", [
            P("1", "A", PASSIVE, "PC_VBUS", "L"), P("2", "B", PASSIVE, "PCPWR_A", "R")])
    D(d, "D104", "SS34", "5V_SYS", "PCPWR_A", b, "3 A 40 V Schottky: PC_VBUS -> 5V_SYS (development mode)",
      "Diode_SMD:D_SMA", symbol="D_Schottky")


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
    ] + [P(n, "SHIELD", PASSIVE, GND, "R") for n in ("31", "32", "33", "34")]
    box(d, "J201", "USB-C iPhone", "TYPE-C-31-M-04",
        "box-v1:USB_C_Receptacle_HRO_TYPE-C-31-M-04", b,
        "24-pin USB-C receptacle with all SS pairs (4-lane DP Alt Mode) + USB 2.0 + CC + VBUS", usbc24,
        src="USBC_SPEC", note="Hybrid: A row SMD, B row through-hole, so the B row is reached from the inner "
                             "layers; shell pads 31-34. A 16P (USB 2.0) receptacle has no SS pairs")

    # LT7911D. Pins 1-23: [Likely] (vendor brief via search excerpts). Others: [Unknown].
    CO, CB = LIKELY, UNKNOWN
    lt = [
        P("1", "VCC12D_RX", PWR_IN, "1V2", "L", "1.2 V digital DP RX", CO, ("C203",)),
        P("2", "D0P", IN, "SS_RX2_P", "L", "DP lane 0+: provisional SS pair mapping, per the Lontium reference design", CO),
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
        P("14", "UCC1", BIDI, "PHONE_CC1", "L", "Type-C CC1, iPhone side (PD + Alt Mode)", CO),
        P("15", "UCC2", BIDI, "PHONE_CC2", "L", "Type-C CC2, iPhone side", CO),
        P("16", "VCC33_IO", PWR_IN, "3V3_LT", "L", "3.3 V IO (I2C, 3.3 V GPIO)", CO, ("C208",)),
        P("17", "AUXP", BIDI, "LT_AUX_P", "L", "DP AUX+ (through a 100 nF capacitor from SBU1)", CO),
        P("18", "AUXN", BIDI, "LT_AUX_N", "L", "DP AUX- (through a 100 nF capacitor from SBU2)", CO),
        P("19", "SLEEP_33", IN, "LT_SLEEP", "R", "function unclear: TP + R204 DNP", CO),
        P("20", "RST_N", IN, "LT_RST_N", "R", "reset, active low", CO),
        P("21", "CSCL", IN, "LT_SCL", "R", "I2C slave 0x2B (7-bit)", CO),
        P("22", "CSDA", BIDI, "LT_SDA", "R", "I2C slave", CO),
        P("23", "RX_HPD", OUT, "LT_RX_HPD", "R", "DP-side HPD; over Type-C HPD travels in PD messages: TP only", CO),
        P("?VDD", "VDD", PWR_IN, "1V2", "L", "core pin: voltage unclear (1.2 V assumed)", CB, ("C209",)),
        P("?VCC33_TX", "VCC33_TX", PWR_IN, "3V3_LT", "L", "3.3 V MIPI TX (possibly several pins)", CB, ("C210",)),
        P("?VCC12_TX", "VCC12_TX", PWR_IN, "1V2", "L", "1.2 V MIPI TX (possibly several pins)", CB, ("C211",)),
        P("?XTALI", "XTALI", IN, "LT_XI", "R", "crystal (frequency unclear, 25 MHz assumed)", CB),
        P("?XTALO", "XTALO", OUT, "LT_XO", "R", "crystal", CB),
        P("?TXCP", "TXA_CLKP", OUT, "CSI_CLK_P", "R", "MIPI port used for CSI: clock+", CB),
        P("?TXCN", "TXA_CLKN", OUT, "CSI_CLK_N", "R", "clock-", CB),
        P("?TX0P", "TXA_D0P", OUT, "CSI_D0_P", "R", "CSI lane 0+", CB),
        P("?TX0N", "TXA_D0N", OUT, "CSI_D0_N", "R", "", CB),
        P("?TX1P", "TXA_D1P", OUT, "CSI_D1_P", "R", "CSI lane 1+", CB),
        P("?TX1N", "TXA_D1N", OUT, "CSI_D1_N", "R", "", CB),
        P("?TX2P", "TXA_D2P", OUT, "CSI_D2_P", "R", "CSI lane 2+", CB),
        P("?TX2N", "TXA_D2N", OUT, "CSI_D2_N", "R", "", CB),
        P("?TX3P", "TXA_D3P", OUT, "CSI_D3_P", "R", "CSI lane 3+", CB),
        P("?TX3N", "TXA_D3N", OUT, "CSI_D3_N", "R", "", CB),
        P("?INT", "GPIO_INT", OUT, "LT_INT", "R", "interrupt GPIO to the SoC (the firmware decides which pin)", CB),
        P("?VBUSEN", "GPIO_VBUS_EN", OUT, "LT_VBUS_EN", "R", "Option B: drives the VBUS switch", CB),
        P("?PDCC1", "PD_CC1", BIDI, "LT_PDCC1", "L", "Option B: charger-side CC (if the chip has a second PD port)", CB),
        P("?PDCC2", "PD_CC2", BIDI, "LT_PDCC2", "L", "Option B", CB),
        P("?IIS_WS", "IIS_WS", OUT, NC, "R", "audio: not used", CB),
        P("?IIS_SCLK", "IIS_SCLK", OUT, NC, "R", "not used", CB),
        P("?IIS_MCLK", "IIS_MCLK", OUT, NC, "R", "not used", CB),
        P("?IIS_D0", "IIS_D0", OUT, NC, "R", "not used", CB),
        P("?SPDIF", "VSYNC_OUT/SPDIF", OUT, NC, "R", "not used", CB),
        P("?EPAD", "EPAD", PWR_IN, GND, "L", "thermal pad = GND (assumed)", CB),
    ]
    box(d, "U201", "LT7911D", "LT7911D", "box-v1:LT7911D_QFN-64-1EP_7.5x7.5mm_P0.4mm", b,
        "Type-C/DP1.2 -> MIPI CSI-2, PD + DP Alt Mode sink, on-chip MCU + flash", lt,
        src="LT7911D_BRIEF", conf=LIKELY,
        note="Pins 24-64 are drawn by function only: take them and the footprint from the public datasheet R1.4 "
             "(source LT7911D_DS) before U201 is routed")
    _two(d, "X201", "25MHz", "LT_XI", "LT_XO", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", b,
         "LT7911D crystal (frequency and load unknown)", "X", buy="XTAL25M", conf=UNKNOWN)
    # a 4-pad crystal: add the two GND pads
    xp = d.part("X201")
    xp.pins = [P("1", "~", PASSIVE, "LT_XI", "L"), P("2", "GND", PASSIVE, GND, "B"),
               P("3", "~", PASSIVE, "LT_XO", "R"), P("4", "GND", PASSIVE, GND, "B")]
    C(d, "C201", "18pF C0G", "LT_XI", GND, b, "Crystal load capacitor (unclear, per the Lontium reference design)",
      conf=UNKNOWN)
    C(d, "C202", "18pF C0G", "LT_XO", GND, b, "Crystal load capacitor", conf=UNKNOWN)
    for ref, net, what in (("C203", "1V2", "pin 1"), ("C204", "1V2_LT_A", "pin 4"),
                           ("C205", "3V3_LT", "pin 7"), ("C206", "1V2_LT_A", "pin 10"),
                           ("C207", "1V2_LT_A", "pin 13"), ("C208", "3V3_LT", "pin 16"),
                           ("C209", "1V2", "VDD"), ("C210", "3V3_LT", "VCC33_TX"),
                           ("C211", "1V2", "VCC12_TX")):
        C(d, ref, "100nF", net, GND, b, f"Decoupling capacitor at LT7911D {what}")
    C(d, "C212", "10uF 10V", "1V2", GND, b, "1V2 bulk capacitor at the LT7911D", fp="C0603")
    C(d, "C213", "10uF 10V", "1V2_LT_A", GND, b, "1V2_LT_A bulk capacitor", fp="C0603")
    C(d, "C214", "10uF 10V", "3V3_LT", GND, b, "3V3_LT bulk capacitor", fp="C0603")
    FB(d, "FB201", "3V3", "3V3_LT", b, "Ferrite bead isolating the LT7911D 3.3 V")
    FB(d, "FB202", "1V2", "1V2_LT_A", b, "Ferrite bead isolating the 1.2 V analog/PLL supply")
    R(d, "R201", "2.2k", "LT_SCL", "VCC_3V3_MOD", b,
      "I2C pull-up to the module's 3.3 V (the RV1106 IO domain), so the bus never feeds an unpowered SoC")
    R(d, "R202", "2.2k", "LT_SDA", "VCC_3V3_MOD", b, "I2C pull-up (as R201)")
    R(d, "R203", "10k", "LT_RST_N", "3V3", b, "RST_N pull-up: the LT7911D runs as soon as it is powered")
    C(d, "C215", "100nF", "LT_RST_N", GND, b, "Power-on reset RC (τ = 1 ms)")
    R(d, "R213", "1k", "SOC_LT_RST", "LT_RST_N", b,
      "Series resistor: the RV1106 GPIO no longer discharges C215 directly")
    R(d, "R204", "10k", "LT_SLEEP", GND, b, "Optional level for SLEEP_33 (unclear)", dnp=True, conf=UNKNOWN)
    R(d, "R205", "100k", "LT_INT", GND, b, "Holds LT_INT low while the LT7911D is in reset")
    C(d, "C216", "100nF", "PHONE_SBU1", "LT_AUX_P", b,
      "AUX AC capacitor (direction/bias unclear, per the Lontium reference design)", conf=UNKNOWN)
    C(d, "C217", "100nF", "PHONE_SBU2", "LT_AUX_N", b, "AUX AC capacitor", conf=UNKNOWN)
    R(d, "R206", "1M", "LT_AUX_P", GND, b, "Sink-side AUX bias (unclear)", dnp=True, conf=UNKNOWN)
    R(d, "R207", "1M", "LT_AUX_N", "3V3_LT", b, "Sink-side AUX bias (unclear)", dnp=True, conf=UNKNOWN)
    TP(d, "TP201", "LT_SLEEP", b, "Measure/force SLEEP_33")
    TP(d, "TP202", "LT_RX_HPD", b, "Measure RX_HPD")

    # ESD at the connector
    tpd = "Package_SON:USON-10_2.5x1.0mm_P0.5mm"
    for ref, a, bn, c, dd, desc in (
            ("U202", "SS_TX1_P", "SS_TX1_N", "SS_RX1_P", "SS_RX1_N", "ESD for SS pairs TX1/RX1"),
            ("U203", "SS_TX2_P", "SS_TX2_N", "SS_RX2_P", "SS_RX2_N", "ESD for SS pairs TX2/RX2"),
            ("U204", "PHONE_CC1", "PHONE_CC2", "PHONE_SBU1", "PHONE_SBU2", "ESD for CC1/CC2/SBU1/SBU2")):
        box(d, ref, "TPD4E05U06DQA", "TPD4E05U06DQAR", tpd, b, desc + " (0.5 pF, flow-through)", [
            P("1", "D1+", PASSIVE, a, "L"), P("2", "D1-", PASSIVE, bn, "L"),
            P("3", "GND", PWR_IN, GND, "L"), P("4", "D2+", PASSIVE, c, "L"),
            P("5", "D2-", PASSIVE, dd, "L"), P("6", "NC", NCPIN, NC, "R", "flow-through pad"),
            P("7", "NC", NCPIN, NC, "R"), P("8", "GND", PWR_IN, GND, "R"),
            P("9", "NC", NCPIN, NC, "R"), P("10", "NC", NCPIN, NC, "R"),
        ], src="KICAD_SYM")
    box(d, "U205", "USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", b,
        "ESD for the iPhone USB 2.0 pair", [
            P("1", "I/O1", PASSIVE, "PHONE_USB_DP", "L"), P("2", "GND", PASSIVE, GND, "L"),
            P("3", "I/O2", PASSIVE, "PHONE_USB_DN", "L"), P("4", "I/O2", PASSIVE, "PHONE_USB_DN", "R"),
            P("5", "VBUS", PASSIVE, "3V3", "R", "tied to 3V3 (clamp reference)"),
            P("6", "I/O1", PASSIVE, "PHONE_USB_DP", "R"),
        ], src="KICAD_SYM")
    C(d, "C218", "100nF", "3V3", GND, b, "Capacitor at the U205 VBUS pin")
    D(d, "D201", "SMF6.0A", "PHONE_VBUS", GND, b,
      "iPhone VBUS TVS (Option B with VIN up to 20 V: change to SMF22A)", "Diode_SMD:D_SOD-123F",
      symbol="D_TVS")
    C(d, "C219", "10uF 25V", "PHONE_VBUS", GND, b, "iPhone-side VBUS capacitor (Type-C source ≤ 120 µF)", fp="C0805")
    C(d, "C220", "100nF 50V", "PHONE_VBUS", GND, b, "iPhone VBUS high-frequency capacitor")
    R(d, "R210", "10k", "PHONE_VBUS", GND, b, "Discharges VBUS to vSafe0V (< 0.8 V in ~0.4 s)", fp="R0603")
    R(d, "R211", "5.1k", "PHONE_CC1", GND, b,
      "Temporary Rd for HID-only bring-up (LT7911D not fitted): the iPhone becomes source + host", dnp=True,
      src="USBC_SPEC")
    R(d, "R212", "5.1k", "PHONE_CC2", GND, b, "Temporary Rd for HID-only bring-up", dnp=True, src="USBC_SPEC")


# ---------------------------------------------------------------------------
# Sheet 3: CH32V305RBT6
# ---------------------------------------------------------------------------
def build_mcu(d: Design):
    b = "mcu"
    nc = NC
    pins = [
        P("1", "VBAT", PWR_IN, "3V3", "L", "backup RTC not used", decap=("C303",)),
        P("2", "PC13", BIDI, nc, "L"), P("3", "PC14/OSC32_IN", BIDI, nc, "L"),
        P("4", "PC15/OSC32_OUT", BIDI, nc, "L"),
        P("5", "OSC_IN/PD0", IN, "HSE_IN", "L", "HSE 8 MHz (the USBHS PLL needs 4 MHz = HSE/2)", src="CH32EVT"),
        P("6", "OSC_OUT/PD1", OUT, "HSE_OUT", "L"),
        P("7", "NRST", IN, "MCU_NRST", "L", "RC 4.7k/100nF + RV1106 + SWD"),
        P("8", "PC0", BIDI, nc, "L"), P("9", "PC1", BIDI, nc, "L"), P("10", "PC2", BIDI, nc, "L"),
        P("11", "PC3", BIDI, nc, "L"),
        P("12", "VSSA", PWR_IN, GND, "L"),
        P("13", "VDDA", PWR_IN, "VDDA_MCU", "L", "must equal VIO (§2.5.3)", decap=("C308", "C309")),
        P("14", "PA0/ADC0", IN, "CC1_SENSE", "L", "ADC: iPhone-side CC1 voltage (through 100k)"),
        P("15", "PA1/ADC1", IN, "CC2_SENSE", "L", "ADC: CC2 voltage"),
        P("16", "PA2/ADC2", IN, "PHONE_VBUS_SENSE", "L", "ADC: iPhone VBUS / 7.67"),
        P("17", "PA3/ADC3", IN, "PHONE_ISENSE", "L", "ADC: charging current 0.5 V/A"),
        P("18", "VSS_4", PWR_IN, GND, "L"),
        P("19", "VDD_4", PWR_IN, "3V3", "L", decap=("C304",)),
        P("20", "PA4/SPI1_NSS", IN, "SPI_CS", "L", "SPI slave of the RV1106"),
        P("21", "PA5/SPI1_SCK", IN, "SPI_SCK", "L"),
        P("22", "PA6/SPI1_MISO", OUT, "SPI_MISO", "L"),
        P("23", "PA7/SPI1_MOSI", IN, "SPI_MOSI", "L"),
        P("24", "PC4", BIDI, nc, "L"), P("25", "PC5", BIDI, nc, "L"),
        P("26", "PB0", OUT, "MCU_IRQ", "L", "event interrupt to the RV1106"),
        P("27", "PB1", OUT, "MCU_VBUS_EN", "L", "switches the iPhone VBUS (through R127)"),
        P("28", "PB2/BOOT1", IN, "MCU_BOOT1", "L", "10k to GND"),
        P("29", "PB10/USART3_TX", OUT, "MCU_DBG_TX", "L", "MCU debug log"),
        P("30", "PB11/USART3_RX", IN, "MCU_DBG_RX", "L"),
        P("31", "VSS_1", PWR_IN, GND, "L"),
        P("32", "VIO_1", PWR_IN, "3V3", "L", decap=("C305",)),
        # right side, counter-clockwise order 64 -> 33
        P("64", "VIO_3", PWR_IN, "3V3", "R", decap=("C307",)),
        P("63", "VSS_3", PWR_IN, GND, "R"),
        P("62", "PB9", BIDI, nc, "R"), P("61", "PB8", BIDI, nc, "R"),
        P("60", "BOOT0", IN, "MCU_BOOT0", "R", "10k to GND; the RV1106 pulls it high to enter the ISP bootloader"),
        P("59", "PB7/USBHS_DP", BIDI, "PHONE_USB_DP", "R", "USB 2.0 HS to the iPhone (internal PHY)"),
        P("58", "PB6/USBHS_DM", BIDI, "PHONE_USB_DN", "R"),
        P("57", "PB5", BIDI, nc, "R"), P("56", "PB4", BIDI, nc, "R"), P("55", "PB3", BIDI, nc, "R"),
        P("54", "PD2", BIDI, nc, "R"), P("53", "PC12", BIDI, nc, "R"), P("52", "PC11", BIDI, nc, "R"),
        P("51", "PC10", BIDI, nc, "R"), P("50", "PA15", BIDI, nc, "R"),
        P("49", "PA14/SWCLK", IN, "SWCLK", "R", "WCH-LinkE"),
        P("48", "VDD_2", PWR_IN, "3V3", "R", decap=("C306",)),
        P("47", "VSS_2", PWR_IN, GND, "R"),
        P("46", "PA13/SWDIO", BIDI, "SWDIO", "R", "WCH-LinkE"),
        P("45", "PA12/OTG_FS_DP", BIDI, "MCU_FS_DP", "R", "second USB FS -> header J502"),
        P("44", "PA11/OTG_FS_DM", BIDI, "MCU_FS_DN", "R"),
        P("43", "PA10/USART1_RX", IN, "MCU_UART_RX", "R", "UART link + ISP bootloader"),
        P("42", "PA9/USART1_TX", OUT, "MCU_UART_TX", "R", "UART link + ISP bootloader"),
        P("41", "PA8", BIDI, nc, "R"), P("40", "PC9", BIDI, nc, "R"),
        P("39", "PC8/TIM8_CH3", OUT, "LED_G", "R", "green LED (PWM)"),
        P("38", "PC7/TIM8_CH2", OUT, "LED_Y", "R", "yellow LED (PWM)"),
        P("37", "PC6/TIM8_CH1", OUT, "LED_R", "R", "red LED (PWM)"),
        P("36", "PB15", BIDI, nc, "R"), P("35", "PB14", BIDI, nc, "R"), P("34", "PB13", BIDI, nc, "R"),
        P("33", "PB12", IN, "PD_PG", "R", "CH224K PG (low = PD negotiated)"),
    ]
    box(d, "U301", "CH32V305RBT6", "CH32V305RBT6", "Package_QFP:LQFP-64_10x10mm_P0.5mm", b,
        "RISC-V MCU: USB HS (internal PHY) as HID for the iPhone, SPI/UART link to the RV1106", pins,
        src="CH32DS")
    _two(d, "X301", "8MHz", "HSE_IN", "HSE_OUT", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm", b,
         "8 MHz HSE crystal, CL 12 pF, ±20 ppm", "X", buy="XTAL8M", src="CH32EVT")
    xp = d.part("X301")
    xp.pins = [P("1", "~", PASSIVE, "HSE_IN", "L"), P("2", "GND", PASSIVE, GND, "B"),
               P("3", "~", PASSIVE, "HSE_OUT", "R"), P("4", "GND", PASSIVE, GND, "B")]
    C(d, "C301", "18pF C0G", "HSE_IN", GND, b, "HSE load capacitor: 2 x (12 pF - ~3 pF stray)")
    C(d, "C302", "18pF C0G", "HSE_OUT", GND, b, "HSE load capacitor")
    for ref, net, what in (("C303", "3V3", "VBAT pin 1"), ("C304", "3V3", "VDD_4 pin 19"),
                           ("C305", "3V3", "VIO_1 pin 32"), ("C306", "3V3", "VDD_2 pin 48"),
                           ("C307", "3V3", "VIO_3 pin 64"), ("C308", "VDDA_MCU", "VDDA pin 13")):
        C(d, ref, "100nF", net, GND, b, f"Decoupling capacitor {what} (datasheet Figure 4-1-1)", src="CH32DS")
    C(d, "C309", "1uF 25V", "VDDA_MCU", GND, b, "Extra VDDA capacitor for the ADC")
    C(d, "C310", "10uF 10V", "3V3", GND, b, "3V3 bulk capacitor at the MCU", fp="C0603")
    FB(d, "FB301", "3V3", "VDDA_MCU", b, "Ferrite isolating VDDA (VDDA = VIO at DC)")
    R(d, "R301", "4.7k", "MCU_NRST", "3V3", b,
      "NRST pull-up, strong enough to win over the default pull-down of GPIO1_D3 (RV1106) through R413")
    C(d, "C311", "100nF", "MCU_NRST", GND, b, "NRST capacitor")
    R(d, "R302", "10k", "MCU_BOOT0", GND, b, "BOOT0 = 0: run from flash")
    R(d, "R303", "10k", "MCU_BOOT1", GND, b, "BOOT1 = 0: with BOOT0 = 1 the MCU enters system memory (ISP)")
    R(d, "R304", "100k", "PHONE_CC1", "CC1_SENSE", b, "CC1 voltage sense, high impedance so the CC line is not loaded")
    C(d, "C312", "1nF", "CC1_SENSE", GND, b, "CC1 ADC filter")
    R(d, "R305", "100k", "PHONE_CC2", "CC2_SENSE", b, "CC2 voltage sense")
    C(d, "C313", "1nF", "CC2_SENSE", GND, b, "CC2 ADC filter")
    R(d, "R306", "100k", "PHONE_VBUS", "PHONE_VBUS_SENSE", b, "iPhone VBUS divider (20 V -> 2.6 V)")
    R(d, "R307", "15k", "PHONE_VBUS_SENSE", GND, b, "iPhone VBUS divider")
    C(d, "C314", "10nF", "PHONE_VBUS_SENSE", GND, b, "VBUS ADC filter")


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
    9: ("CSI_CLK_N", IN, "clock lane CK0 when the two D-PHYs are combined into 4 lanes [Likely]"),
    10: ("CSI_CLK_P", IN, ""), 11: ("CSI_D0_N", IN, ""), 12: ("CSI_D0_P", IN, ""),
    22: ("PC_USB_DN", BIDI, "USB 2.0 OTG -> PC"), 23: ("PC_USB_DP", BIDI, ""),
    24: ("PC_VBUS_DET", IN, "PC VBUS through 10k/18k (as Luckfox Pico Ultra)"),
    26: ("SOC_RECOVERY", IN, "RECOVERY key, always pulled up to 1.8 V"),
    27: ("VIN_SENSE", IN, "VIN measurement (1.8 V ADC): VIN x 8.2/108.2"),
    65: ("LT_SCL", OUT, "I2C2_M0 -> LT7911D (3.3 V)"), 66: ("LT_SDA", BIDI, ""),
    67: ("LT_INT", IN, "interrupt from the LT7911D (rising-edge IRQ)"),
    70: ("MCU_UART_TX", IN, "UART4_M0 RX <- CH32 USART1 TX"),
    71: ("MCU_UART_RX", OUT, "UART4_M0 TX -> CH32 USART1 RX"),
    72: ("SOC_CON_TX", OUT, "console UART2_M1 (fiq-debugger) -> J503"),
    73: ("SOC_CON_RX", IN, "console RX"),
    74: ("SOC_NPOR", IN, "RV1106 reset (button SW502)"),
    77: ("VCC_1V8_MOD", PWR_OUT, "1.8 V output of the module"),
    78: ("VCC_3V3_MOD", PWR_OUT, "3.3 V output: I2C pull-ups of the LT7911D bus, TP"),
    79: ("5V_SYS", PWR_IN, "4.6-5.2 V, ≤ 1 A"), 80: ("5V_SYS", PWR_IN, ""), 81: ("5V_SYS", PWR_IN, ""),
    85: ("ETH_RX_N", BIDI, "100M PHY inside the RV1106"), 86: ("ETH_RX_P", BIDI, ""),
    87: ("ETH_TX_N", BIDI, ""), 88: ("ETH_TX_P", BIDI, ""),
    90: ("SOC_MCU_RST", OUT, "resets the CH32 through R413 1k; GPIO1_D3 defaults to a weak pull-down, R301 4.7k wins"),
    91: ("MCU_BOOT0", OUT, "pulled high to put the CH32 into its USART1 bootloader"),
    92: ("MCU_IRQ", IN, "interrupt from the CH32"),
    61: ("SOC_LT_RST", OUT, "LT7911D reset through R213 (reset-gpios, active low); GPIO0_A3 defaults to "
                            "pull-up: the LT7911D runs as soon as power is applied"),
    98: ("SPI_MISO", IN, "SPI0_M0 master <- CH32 SPI1"), 99: ("SPI_MOSI", OUT, ""),
    100: ("SPI_SCK", OUT, ""), 101: ("SPI_CS", OUT, ""),
}

CORE1106_NC_NOTE = {
    range(1, 3): "CK1 only used in 2x2-lane mode",
    range(13, 21): "1.8 V domain, not used",
    range(30, 36): "codec not used",
    range(37, 47): "eMMC variant: pad disconnected on the module",
    range(48, 55): "Wi-Fi variant: SDMMC wired to the on-module Wi-Fi",
    range(63, 65): "Wi-Fi variant: used for the BT UART",
    range(68, 70): "Wi-Fi variant: used for the BT UART",
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
            note = "not used"
            for rg, txt in CORE1106_NC_NOTE.items():
                if n in rg:
                    note = txt
            if n == 76:
                note = "RTC fed from VCC_3V3 through a diode on the module; left open"
            kind = PWR_IN if n == 76 else BIDI
            pins.append(P(str(n), name, kind, NC, side, note, src="CORE1106_XLS"))
    # right side listed top->bottom as 112..57 (counter-clockwise like the module)
    left = [p for p in pins if int(p.num) <= 56]
    right = sorted([p for p in pins if int(p.num) > 56], key=lambda p: -int(p.num))
    box(d, "U401", "Luckfox Core1106", "CORE1106",
        "box-v1:Luckfox_Core1106_Castellated_30x30mm_P1.0mm", b,
        "RV1106G3 SoC module: 4-lane CSI-2, H.264, 100M Ethernet PHY, USB 2.0 OTG, on-module Wi-Fi",
        left + right, src="CORE1106_XLS", conf=LIKELY,
        note="Footprint: use Luckfox Core1106-SMT (30x30 mm, 112 pads, 1.0 mm pitch)")
    C(d, "C401", "22uF 25V X5R", "5V_SYS", GND, b, "VCC5V0_SYS bulk capacitor next to the module", fp="C0805")
    C(d, "C402", "100nF", "5V_SYS", GND, b, "VCC5V0_SYS high-frequency capacitor")
    TP(d, "TP401", "VCC_3V3_MOD", b, "Checks that the module PMIC is up")

    box(d, "J401", "USB-C PC", "TYPE-C-31-M-12", "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", b,
        "USB-C to the PC: the box is a USB device (UVC + USB network + control)",
        usbc16("J401", b, "PC_VBUS", "PC_CC1", "PC_CC2", "PC_USB_DP", "PC_USB_DN", ""),
        src="KICAD_SYM")
    R(d, "R401", "5.1k 1%", "PC_CC1", GND, b, "Rd: the box is the UFP (sink) towards the PC", src="USBC_SPEC")
    R(d, "R402", "5.1k 1%", "PC_CC2", GND, b, "Rd", src="USBC_SPEC")
    box(d, "U402", "USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", b, "ESD for the PC-side USB 2.0", [
        P("1", "I/O1", PASSIVE, "PC_USB_DP", "L"), P("2", "GND", PASSIVE, GND, "L"),
        P("3", "I/O2", PASSIVE, "PC_USB_DN", "L"), P("4", "I/O2", PASSIVE, "PC_USB_DN", "R"),
        P("5", "VBUS", PASSIVE, "3V3", "R"), P("6", "I/O1", PASSIVE, "PC_USB_DP", "R"),
    ], src="KICAD_SYM")
    C(d, "C403", "100nF", "3V3", GND, b, "Capacitor at the U402 VBUS pin")
    C(d, "C404", "1uF 50V", "PC_VBUS", GND, b, "PC-side VBUS capacitor (UFP ≤ 10 µF)", fp="C0603")
    D(d, "D401", "SMF6.0A", "PC_VBUS", GND, b, "PC VBUS TVS", "Diode_SMD:D_SOD-123F", symbol="D_TVS")
    R(d, "R403", "10k", "PC_VBUS", "PC_VBUS_DET", b, "VBUS divider -> USB_VBUSDET (5 V -> 3.2 V)", src="PICO_ULTRA")
    R(d, "R404", "18k", "PC_VBUS_DET", GND, b, "VBUSDET divider", src="PICO_ULTRA")
    C(d, "C405", "100nF", "PC_VBUS_DET", GND, b, "VBUSDET filter", src="PICO_ULTRA")

    box(d, "J402", "RJ45 10/100", "HR911105A", "Connector_RJ:RJ45_Hanrun_HR911105A_Horizontal", b,
        "RJ45 with magnetics (magjack) 10/100", [
            P("1", "TD+", PASSIVE, "ETH_TXP_J", "L"), P("2", "TD-", PASSIVE, "ETH_TXN_J", "L"),
            P("3", "RD+", PASSIVE, "ETH_RXP_J", "L"), P("4", "TCT", PASSIVE, "ETH_TCT", "L"),
            P("5", "RCT", PASSIVE, "ETH_RCT", "L"), P("6", "RD-", PASSIVE, "ETH_RXN_J", "L"),
            P("7", "NC", NCPIN, NC, "R"),
            P("8", "BS", PASSIVE, "ETH_BS", "R", "Bob-Smith node (1 nF/2 kV inside the jack) [Likely]", LIKELY),
            P("9", "LED1", PASSIVE, NC, "R", "LEDs: the Core1106 does not bring out the PHY LED pins", UNKNOWN),
            P("10", "LED1", PASSIVE, NC, "R", "", UNKNOWN),
            P("11", "LED2", PASSIVE, NC, "R", "", UNKNOWN),
            P("12", "LED2", PASSIVE, NC, "R", "", UNKNOWN),
            P("SH", "SHIELD", PASSIVE, GND, "R"),
        ], src="KICAD_SYM")
    for ref, a, bb in (("R405", "ETH_TX_P", "ETH_TXP_J"), ("R406", "ETH_TX_N", "ETH_TXN_J"),
                       ("R407", "ETH_RX_P", "ETH_RXP_J"), ("R408", "ETH_RX_N", "ETH_RXN_J")):
        R(d, ref, "0R", a, bb, b, "0R as in the Luckfox design: room for a filter/ESD part", src="PICO_PLUS_ETH")
    C(d, "C406", "10nF 50V", "ETH_TCT", GND, b, "TX centre-tap capacitor (voltage-mode PHY)", src="PICO_PLUS_ETH")
    C(d, "C407", "10nF 50V", "ETH_RCT", GND, b, "RX centre-tap capacitor", src="PICO_PLUS_ETH")
    C(d, "C408", "1nF 2kV X7R", "ETH_BS", GND, b,
      "Bob-Smith node capacitor (2 kV keeps the 1500 V isolation of the magnetics)", fp="C1206",
      src="PICO_PLUS_ETH")

    R(d, "R409", "100k 1%", "VIN", "VIN_SENSE", b, "VIN divider for SARADC_IN1 (20 V -> 1.52 V)")
    R(d, "R410", "8.2k 1%", "VIN_SENSE", GND, b, "VIN divider")
    C(d, "C409", "10nF", "VIN_SENSE", GND, b, "VIN ADC filter")
    R(d, "R411", "10k", "SOC_RECOVERY", "VCC_1V8_MOD", b, "SARADC_IN0 pull-up (mandatory)", src="PICO_ULTRA")
    C(d, "C410", "1nF C0G", "SOC_RECOVERY", GND, b, "RECOVERY key filter", src="PICO_ULTRA")
    R(d, "R412", "100R", "SOC_RECOVERY", "RECOVERY_KEY", b, "RECOVERY key series resistor", src="PICO_ULTRA")
    R(d, "R413", "1k", "SOC_MCU_RST", "MCU_NRST", b, "Limits the current when the RV1106 and a WCH-LinkE both drive NRST")


# ---------------------------------------------------------------------------
# Sheet 5: debug, LEDs, buttons, test points
# ---------------------------------------------------------------------------
def build_debug(d: Design):
    b = "debug"
    hdr = "Connector_PinHeader_2.54mm:PinHeader_1x{n:02d}_P2.54mm_Vertical"
    box(d, "J501", "MCU SWD+UART", "HDR1x07", hdr.format(n=7), b,
        "WCH-LinkE header for the CH32V305 (2-wire SWD + debug UART)", [
            P("1", "3V3", PASSIVE, "3V3", "L", "level reference for the WCH-LinkE, not a supply input"),
            P("2", "SWDIO", PASSIVE, "SWDIO", "L"), P("3", "SWCLK", PASSIVE, "SWCLK", "L"),
            P("4", "GND", PASSIVE, GND, "L"), P("5", "NRST", PASSIVE, "MCU_NRST", "L"),
            P("6", "TX", PASSIVE, "MCU_DBG_TX", "L", "MCU TX"),
            P("7", "RX", PASSIVE, "MCU_DBG_RX", "L", "MCU RX"),
        ])
    box(d, "J502", "MCU USB FS", "HDR1x03", hdr.format(n=3), b,
        "Second (Full-Speed) USB of the CH32V305 (ISP over USB, experiments)", [
            P("1", "D+", PASSIVE, "MCU_FS_DP", "L"), P("2", "D-", PASSIVE, "MCU_FS_DN", "L"),
            P("3", "GND", PASSIVE, GND, "L")])
    box(d, "J503", "SoC UART", "HDR1x03", hdr.format(n=3), b,
        "RV1106 console (UART2_M1, 115200 8N1, 3.3 V)", [
            P("1", "GND", PASSIVE, GND, "L"), P("2", "TX", PASSIVE, "SOC_CON_TX", "L", "RV1106 TX"),
            P("3", "RX", PASSIVE, "SOC_CON_RX", "L", "RV1106 RX")], src="RV1106_DTS")
    sw = "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A"
    _two(d, "SW501", "RECOVERY", "RECOVERY_KEY", GND, sw, b,
         "Hold at power-up: the RV1106 enters loader mode (rockusb) over the PC USB-C", "SW", buy="TS1187A")
    _two(d, "SW502", "RESET", "SOC_NPOR", GND, sw, b, "RV1106 reset (NPOR)", "SW", buy="TS1187A")
    for ref, rref, col, rv, net in (("D501", "R501", "RED", "680R", "LED_R"),
                                    ("D502", "R502", "YELLOW", "680R", "LED_Y"),
                                    ("D503", "R503", "GREEN", "560R", "LED_G")):
        R(d, rref, rv, net, net + "_A", b, f"{col} LED current limit ~2 mA")
        LED(d, ref, col, GND, net + "_A", b, f"{col} status LED (red: no iPhone, yellow: HID but no "
                                             "video, green: ready)")
    for i, net in enumerate(["VIN", "5V2_PHONE", "5V_SYS", "3V3", "1V2", "PHONE_VBUS", GND, GND,
                             "3V3_LT", "1V2_LT_A"], start=1):
        TP(d, f"TP5{i:02d}", net, b, f"Test point {net}")


if __name__ == "__main__":
    dz = build()
    print(f"{len(dz.parts)} parts, {sum(len(p.pins) for p in dz.parts)} pins, "
          f"{len(dz.net_pins())} nets")
