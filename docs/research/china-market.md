# Cheap Chinese iPhone-control hardware: how it works and what to adopt

- **Date:** 2026-09-24. **Scope:** hardware and near-hardware ways to drive iPhones without a jailbreak, from the
  Chinese market (群控 / 中控 / 连点器 / 键鼠转换器 / 投屏) and English sources, compared with `iphone-hid`.
- **Method:** web search in Chinese and English, plus direct reading of code and docs where they could be reached:
  - the **iMouse XP Python SDK** (`imouse-xp` 0.0.7 wheel from PyPI, unpacked and read);
  - **VKCOM/devicehub** ESP32 firmware and host code (GitHub raw);
  - **Sipeed NanoKVM-Go** wiki pages (GitHub raw);
  - the UxPlay and quicktime_video_hack READMEs, Apple developer forum 699205, and Apple's MDM docs JSON.
- **Access limits:** the egress proxy blocked most Chinese sites (CSDN, Zhihu, Juejin, ieasyclick, iosautot,
  doc.some3c.com, gamesir.com, doc.xiaoji.com) and several English ones. For those, only the search snippet was
  seen, and they are marked **(snippet)**. Re-open them before relying on exact wording.
- **Labels:** **[Confirmed]** means a primary source (code or docs) was read directly. **[Likely]** means credible
  but indirect evidence: a vendor claim, snippets, or several consistent sources. **[Unknown]** means untested or
  contradictory.

---

## 0. Bottom line

1. **Nobody in this market has a trick that beats "HID pointer + AssistiveTouch".** Every current iPhone solution
   that works without a jailbreak or an app injects input as a USB or BLE mouse and keyboard through AssistiveTouch
   (plus Full Keyboard Access). The exceptions are physical tappers and game-integrated SDKs. The architecture
   `iphone-hid` uses is the industry standard.
2. **Absolute positioning over USB now has three independent sources:**
   - Aiden (already known);
   - **EasyClick**: its OTG firmware is "absolute only, needs iOS 17+", and absolute gives "no drift, no
     compensation";
   - **Sipeed NanoKVM-Go**: its "Follow Mouse" (absolute) mode is documented for iPhone 15/16/17, with 4-point
     calibration.

   R1 is very likely to resolve in our favour on USB-C.
3. **A vendor claims absolute also works over BLE on iOS 17+.** EasyClick ships absolute BLE firmware for the
   ESP32-C3 (snippet). If true, the Lightning line also escapes relative-mode acceleration. This should become
   the **first BLE test**.
4. **The Chinese "box" vendors (iMouse, SOME 3C, EasyClick) use AirPlay mirroring, not HDMI, for video.** For
   Lightning phones they often add a wired OTG/Ethernet link. They also use **iOS Shortcuts** as a side channel for
   clipboard, file transfer, restart and toggles, and **Full Keyboard Access "Tab+key" chords** for system actions.
5. **Relative-mode vendors do what `iphone-hid` does:** fixed step sizes, fixed cadence, corner or edge reset.
   devicehub's open code adds two concrete refinements: a measured table of step sizes and **edge anchoring that
   avoids the rounded corner**. iMouse and SOME 3C keep a **shared calibration library keyed by (model, iOS
   version)**, which is indirect evidence that pointer acceleration is reproducible between phones.
6. **Touch-digitizer HID died in iOS 13.4.** BLE digitizer and stylus devices worked up to iOS 13.3.1 and were
   closed in 13.4. That is why game-controller "G-Touch" and converter products moved to mouse clicks through
   AssistiveTouch, to game-integrated SDKs, or to physical capacitive tapping.

---

## 1. Solution types

| # | Type (examples) | Typical price | Video path | Input path | Positioning / calibration | Scale | Evidence |
|---|---|---|---|---|---|---|---|
| 1 | **HID "免越狱群控" boards:** iMouse/爱鼠 (Gen 2, XP), SOME 3C board (its English docs host "iMouse XP" pages, so likely an iMouse reseller), "超级黑洞" 中控 | Board about US$38 (SOME 3C); iMouse hardware on Taobao, price not found; plus a software licence | **AirPlay mirroring** to a receiver in the PC software (per-device fps, resolution and refresh settings; mDNS name). Lightning phones can use an "OTG network cable" (USB Ethernet) | CH9329-class USB HID ("双头" cable) through OTG: relative mouse + keyboard + **Full Keyboard Access** | Relative. A **"mouse parameter" profile per (model, iOS version)**, collected with a **web page added to the Home Screen and served by the PC**, shared in a public library (CRC-keyed). Corner reset (`/mouse/reset`), "快准狠" fast mode. Vision (find image/colour, OCR) on the AirPlay frames | One board per phone. 20-phone chassis (20 USB ports, 550 W PSU); 21 chassis per 42U rack = 420 phones | [Confirmed] SDK code; settings and collection flow (snippet) |
| 2 | **EasyClick HID** (BLE on ESP32-C3; "OTG HID" on ESP32-S3) | Generic dev board plus licence; firmware is free (snippet) | Their own "agentless screenshot" / USB mirroring. OTG mode requires "wireless debugging", so it relies on developer services, not pure hardware | BLE HID (C3) or wired USB HID through OTG (S3) | **Relative firmware** needs a "compensation rate" (补偿率) or drift grows with swipe length; zeroing function. **Absolute firmware: "works well on iOS 17+", no compensation.** OTG firmware is absolute-only (iOS 17+). BLE setup sets AssistiveTouch tracking to the **slowest** value | One board per phone ("一机一板") | [Likely] vendor docs (snippet) |
| 3 | **Open-source ESP32 BLE mouse:** VKCOM devicehub | ESP32-C6/C3/WROOM, about US$5 | Not HID-based (they use WDA for video) | BLE relative mouse; host sends 1-char commands over serial | **Max** Tracking Speed and max Tracking Sensitivity. Dead reckoning with 3 fixed step sizes (±1, ±4, ±8 counts); one axis per report; 15 ms host pace, 20 ms firmware delay. **Edge-anchor reset that avoids the rounded corner** | One ESP32 per phone; per-board BLE name + random MAC | [Confirmed] code |
| 4 | **USB-C KVM dongles:** Sipeed **NanoKVM-Go** / Go+; Openterface Mini-KVM (MS2109 + CH9329) | NanoKVM-Go US$59 early bird / US$89 MSRP (Go+ 79/129); Mini-KVM not verified | NanoKVM-Go: a single USB-C cable (DP Alt Mode + PD passthrough), on-board encoding, **about 60 ms at 1080p60**, Wi-Fi 6. Mini-KVM: MS2109, under 140 ms (snippet) | NanoKVM-Go: USB HID; modes **Follow Mouse (absolute)**, Exclusive (relative), Multi-touch (for Android) | Absolute with an "Input Calibration" profile (auto from crop, or a custom **four-point calibration**). iPhone needs AssistiveTouch, **Orientation Lock**, and **"Repair iPhone drag"** after each connection (the mouse otherwise stays pressed) | One per phone; iPhone 15/16/17 except 16e/17e | [Confirmed] vendor wiki |
| 5 | **PC-as-BLE-HID + AirPlay software:** Wormhole/虫洞 | Paid software, no extra hardware | AirPlay receiver on the PC | The PC's own Bluetooth adapter acts as a BLE HID peripheral (Windows 10 1703+) | "Advanced mouse algorithm" (details not public); turns on AssistiveTouch | One phone per PC Bluetooth identity | [Likely] (snippet) |
| 6 | **Game controllers / key-mouse converters:** GameSir G-Touch; Flydigi Q1 "智联"; Flydigi "电容隔空映射" (Wasp) | Consumer gamepads, tens of US$ | None | (a) **BLE digitizer touches up to iOS 13.3.1**; (b) iOS 13.4–14.1: mouse clicks through AssistiveTouch ("small flashing dots"); broken from iOS 14.2; (c) Flydigi 智联: game-integrated BLE SDK (the game must support it); (d) Flydigi capacitive: the controller emits a finger-like field through sensor strips, at fixed spots | G-Touch: per-game key-position configs; a calibration dot must appear in the bottom-left corner (rotate the phone if not); R1 fixed at the **top-left corner** | One phone | [Confirmed] forum 699205 (digitizer died in 13.4); rest snippet |
| 7 | **BLE "auto clickers"** (OUTXE and similar Taobao items) | Consumer, about US$10–30 (not verified) | None | BLE mouse + AssistiveTouch; you place the pointer yourself, the device repeats clicks (at most about 6/s) | None: clicks wherever the pointer is | One phone | [Likely] (snippet) |
| 8 | **Physical capacitive tappers** (物理连点器: NE555 or MCU + conductive pads; "1 host, 8 clicks" kits) | ¥20–100 | None | Grounds or pulses a conductive pad stuck to the screen | Fixed physical positions; at most about 10 taps/s (limited by the touch sensor) | 1–8 pads per phone | [Likely] (snippet) |
| 9 | **Robot arm + camera** (龙测, TMach, three-axis stylus rigs) | Industrial (thousands of US$) | Camera | Stylus on a 3- or 6-axis arm | Hand-eye calibration | One phone per arm | [Likely] (snippet) |
| 10 | **Agent-based (not hardware):** proxy IPA / WDA cloud phones; EasyClick "USB_HID 免硬件" (iOS 17+) | Signing costs | WDA / ReplayKit / H.265 WebRTC (30–50 ms claimed) | XCTest events | Exact coordinates | Large | [Likely] (snippet). **Out of scope:** installs a signed app or needs Developer Mode |
| 11 | **USB "QuickTime" capture** (3uAirPlayer USB mode, qvh, "USB 投屏") | Free | USB bulk H.264 from iPhone; works on Lightning | None | None | Many per host | [Confirmed] qvh README. Needs host iPhone tooling + Trust; puts the phone in "demo mode" status bar |

---

## 2. Techniques worth adopting (ranked by expected value)

### 2.1 Absolute pointer over USB, with a 4-point calibration and the "stuck drag" fix
- **Evidence:**
  - EasyClick OTG HID firmware (ESP32-S3) is **absolute-only and requires iOS 17+**. Its relative firmware "has
    better compatibility but needs a compensation rate"; its absolute firmware "works well on iOS 17+, no
    compensation, more precise" (snippet). Their guide also says absolute requires a 1:1 `setScale` and correct
    `setScreenSize`, and mentions scale factors for notched vs non-notched screens (snippet).
  - NanoKVM-Go documents iPhone 15/16/17 support. Its "Follow Mouse" mode is the old "Absolute Mode" (wiki PR
    #1038). It has an **Input Calibration** profile: automatic from the video crop, or a custom **four-point
    calibration** "relying on visible pointer or touch feedback". It needs **Orientation Lock** for precise cursor
    tracking, and **"Repair iPhone drag"** after each connection, because otherwise "the mouse may stay in a pressed
    state".
  - Together with Aiden's code, that makes three independent vendors.
- **Confidence:** **[Likely]**, strong: absolute over USB works on iOS 17+ iPhones. **[Unknown]**: whether the
  CH9329's mixed rel+abs descriptor (report ID 2, 0..4095) is accepted. Neither vendor uses a CH9329 for absolute.
  EasyClick uses an ESP32-S3; Sipeed uses its own SoC gadget.
- **Adopt:**
  1. Keep T1 as the first test.
  2. If the CH9329 fails, go straight to ESP32-S3 TinyUSB absolute. That is the same chip EasyClick ships for this
     exact job.
  3. Add a "release everything, then one idle absolute report" repair step right after enumeration, mirroring
     NanoKVM's "Repair iPhone drag". It is cheap insurance against a stuck button on connect.
  4. Require Orientation Lock (portrait) in `docs/iphone-setup.md`.
  5. Keep the 6-parameter affine fit; NanoKVM's 4-point calibration shows a pure linear map is not enough (the
     crop and offset matter).
- **Sources:**
  - [EasyClick 3-way comparison (snippet)](https://juejin.cn/post/7677077660527525934)
  - [EasyClick OTG HID tutorial (snippet)](https://ieasyclick.com/iostjdocs/zh-cn/advance/tj-otg-starter/)
  - [EasyClick HID guide (snippet)](https://juejin.cn/post/7647909099691311138)
  - NanoKVM-Go [introduction](https://github.com/sipeed/sipeed_wiki/blob/main/docs/hardware/en/kvm/NanoKVM_Go/introduction.md), [quick_start](https://github.com/sipeed/sipeed_wiki/blob/main/docs/hardware/en/kvm/NanoKVM_Go/quick_start.md), [user_guide](https://github.com/sipeed/sipeed_wiki/blob/main/docs/hardware/en/kvm/NanoKVM_Go/user_guide.md), [faq](https://github.com/sipeed/sipeed_wiki/blob/main/docs/hardware/en/kvm/NanoKVM_Go/faq.md), [PR #1038](https://github.com/sipeed/sipeed_wiki/pull/1038)

### 2.2 Absolute pointer over BLE on iOS 17+: test it first on the ESP32
- **Evidence:** EasyClick's BLE tutorial for the ESP32-C3 offers "relative and absolute coordinate firmware
  builds"; "absolute mouse works well on iOS 17+ with no compensation and more accurate taps" (snippet). The
  comparison article gives iOS 18+ for the BLE mode (snippet). The only counter-evidence is old: iPad iOS 13, 2020
  ([forum 652700](https://developer.apple.com/forums/thread/652700)).
- **Confidence:** **[Likely]**, one vendor, snippet only. **[Unknown]** which descriptor they use.
- **Adopt:** make it test **T9(b0)**, before any BLE relative work. Add a descriptor option to
  `firmware/esp32_ble_hid`:
  - Generic Desktop / Mouse / Pointer, Physical collection;
  - X/Y 16-bit absolute (0..32767), buttons, relative wheel;
  - that is, the Aiden/PiKVM layout carried over HOGP.

  If it works, Lightning phones get sub-point taps and the whole acceleration and pacer problem disappears there.
- **Sources:** [EasyClick BLE tutorial (snippet)](https://ieasyclick.com/en/iosdocs/advance/ios-usb-ble/), [comparison (snippet)](https://juejin.cn/post/7677077660527525934).

### 2.3 Lightning: wired USB HID through the Camera Adapter, video through AirPlay (the "OTG network cable" design)
- **What the vendors do:**
  - iMouse and SOME 3C phones get HID over OTG and video over **AirPlay**.
  - SOME 3C advertises an "intranet OTG network cable" for internet access.
  - The iMouse SDK has per-device AirPlay settings (`air_ratio`, `air_fps`, `air_refresh`), auto-connect, a
    receiver name, an mDNS rule and port 17000.
  - A Lightning to USB 3 Camera Adapter (with charge port) plus a small hub carries **HID + a USB Ethernet adapter
    + charging** at the same time.
- **Gain for `iphone-hid`:**
  - USB HID on Lightning phones, so **absolute** (2.1) and no BLE pairing or reconnect problems;
  - no US$49 Digital AV Adapter and no capture card per phone.
- **Cost:**
  - an AirPlay receiver, a new component that needs the owner's approval before it is used;
  - H.264 decode, or passthrough;
  - about 100–200 ms latency (AirServer about 150 ms (snippet); the Airplay-SDK vendor claims about 120 ms
    (snippet));
  - mirroring must be started on the phone (Control Center → Screen Mirroring, done with the HID).
- **UxPlay facts** [Confirmed, README]:
  - one client per instance; run one instance per phone with distinct `-m` (MAC/deviceID) and `-p` ports;
  - `-vrtp` forwards the **decrypted H.264 as RTP without decoding**, a passthrough like MJPEG today;
  - `-nohold`; `-restrict/-allow <deviceID>` to bind each phone to its instance;
  - `-pin/-reg` so the phone pairs once;
  - `-fps` below 30 "useful to reduce latency" with several instances;
  - `-vsync no` for live mirroring;
  - an optional BLE beacon for discovery.
- **Also:** MDM `RequestMirroring` exists (iOS 7+), but it only *prompts the user*, so it is not a way to start
  mirroring unattended. [Confirmed, Apple docs JSON]
- **Confidence:** **[Likely]** that it works. **[Unknown]** whether AirPlay mirroring stays up for 24/7 operation
  (locks, network blips, prompts).
- **Adopt:** as the **Lightning-line alternative** to evaluate in T9 (e: Camera Adapter + hub + CH9329 + USB
  Ethernet), subject to owner approval of AirPlay. It is also the only video path for 16e/17e.
- **Sources:**
  - [iMouse SDK (PyPI)](https://pypi.org/project/imouse-xp/): `models/config_model.py`, `api/device_api.py`
  - [SOME 3C board (snippet)](https://some3c.com/products/iphone-farm-ios-automation-control-board)
  - [UxPlay README](https://github.com/FDH2/UxPlay)
  - [Apple RequestMirroringCommand](https://developer.apple.com/documentation/devicemanagement/requestmirroringcommand)
  - [AirServer protocol note (snippet)](https://support.airserver.com/support/solutions/articles/43000531769-what-is-the-airplay-screen-mirroring-protocol-)
  - [Airplay-SDK (snippet)](https://github.com/xfirefly/Airplay-SDK)

### 2.4 Full Keyboard Access "Tab+key" chords (and Apple Fn) for system actions: no pointer, no Cmd/Shift
- **Evidence:**
  - SOME 3C and iMouse setups require **Full Keyboard Access ON, Auto-Hide 1 s**, besides AssistiveTouch.
  - devicehub's reverse-engineered iMouse HID traffic (`imouse.js`) shows:
    - **Lock = Tab + L**;
    - **Back = Tab + B**;
    - Home = Tab, then Cmd+H.
  - Apple's FKA command list: Fn+H Home, Fn+↑ App Switcher, Fn+C Control Center, Fn+N Notification Center,
    **Tab+L Lock Screen**, Fn+S Siri. Commands are **customizable** under Accessibility › Keyboards › Full
    Keyboard Access › Commands.
  - The iMouse author documented emulating Apple's **Fn** key (vendor usage page 0xFF, usage 0x03, "Top Case
    KeyboardFn") in a USB HID descriptor for iOS 15+.
- **Why it matters:**
  - Tab is an ordinary key, not a HID modifier. Tab+key chords therefore likely avoid the Aiden Cmd/Shift/Option bug
    (R4), which is a hypothesis to test.
  - They need no pointer position.
  - The CH9329 cannot send Fn; the ESP32 firmware can.
- **Confidence:** [Confirmed] the Apple command list (snippet of Apple's page) and the iMouse traffic (devicehub
  code). **[Unknown]** whether FKA interferes with the AssistiveTouch pointer; the vendors run both together, so it
  probably does not.
- **Adopt:**
  1. Add FKA to the phone setup.
  2. Remap FKA commands to Tab+letter chords for Home, App Switcher, Control Center, Spotlight and Lock.
  3. Add a Fn usage to the ESP32 descriptor.
  4. Add a T5 variant: Tab-chords vs Cmd-chords.
- **Sources:**
  - [devicehub imouse.js](https://github.com/VKCOM/devicehub/blob/master/lib/units/ios-device/plugins/touch/imouse.js)
  - [Apple FKA (snippet)](https://support.apple.com/guide/iphone/control-iphone-with-an-external-keyboard-ipha4375873f/ios)
  - [SOME 3C settings (snippet)](https://doc.some3c.com/iphone-farm-setup/iphone-farm-settings)
  - [iMouse phone settings (snippet)](http://www.feiyunjs.com/3327.html)
  - [iMouse author on the Fn key (snippet)](https://blog.csdn.net/qq_41057894/article/details/127928033)
  - [Apple Fn usage 0xFF/0x03 (snippet)](https://github.com/qmk/qmk_firmware/issues/2179)

### 2.5 Relative-mode refinements from devicehub and iMouse (for BLE, if 2.2 fails)
- **Edge anchoring that avoids the rounded corner** [Confirmed, code]. The firmware reset (`'0'`) runs these steps,
  after which the host sets the position to `(14 × single_step, 0)`:
  1. `move(-127,-127)` into the corner;
  2. `move(0,+14)` down, "to a position where there is no corner";
  3. `move(-127,0)` to the left edge;
  4. 14 × `move(+1,0)`;
  5. `move(0,-127)` to the top edge.

  The anchor is defined by two **straight edges**, not by the curved corner, where the pointer's clamp point is
  ambiguous. **Adopt this instead of a pure corner slam.** It addresses A4 and T4 directly.
- **A discrete step table at a fixed cadence** [Confirmed, code]:
  - only ±1, ±4, ±8-count reports, one axis per report, at 15–20 ms intervals, max Tracking Speed;
  - measured displacements 13/3, 70/3 and 178/3 units, a **1 : 5.4 : 13.7** ratio;
  - so the per-count gain rises about 1.0 → 1.35 → 1.7 from 1 to 8 counts: acceleration is present but
    tabulated.

  This matches the `iphone-hid` "same-size reports at fixed pace" design and suggests keeping 2–3 step sizes
  rather than one.
- **Tracking-speed setting: the vendors disagree.**
  - devicehub, SOME 3C and iMouse set **max** Tracking Speed and max AssistiveTouch Tracking Sensitivity.
  - EasyClick's BLE guide sets AssistiveTouch Tracking Sensitivity to **slowest**.

  Test both extremes in T4. Max means fewer reports and faster anchoring; min probably means finer resolution.
- **Swipe "brake"** [Confirmed, SDK parameter]: `mouse_swipe(..., step_sleep, steping, brake)`, where brake means
  "stop immediately when the swipe ends". This is presumably a pause before release to kill fling, which fits
  Aiden's fling-velocity observation. Adopt a hold-still-before-release option for precise scrolls.
- **Sources:**
  - [ESP32Mouse.ino](https://github.com/VKCOM/devicehub/blob/master/lib/units/ios-device/plugins/touch/ESP32Mouse/ESP32Mouse.ino), [esp32touch.js](https://github.com/VKCOM/devicehub/blob/master/lib/units/ios-device/plugins/touch/esp32touch.js), [esp32.md](https://github.com/VKCOM/devicehub/blob/master/doc/ios-docs/esp32.md)
  - iMouse SDK `api/mouse_api.py`
  - [EasyClick BLE (snippet)](https://ieasyclick.com/en/iosdocs/advance/ios-usb-ble/)

### 2.6 Shared calibration profiles per (model, iOS version, settings), collected with a Home-Screen web app
- **Evidence:**
  - iMouse and SOME 3C: "each device needs mouse parameters for accurate positioning; by default they are
    auto-matched from a general library; if not, collect once".
  - Collection: open a page served by the PC kernel (`http://{kernel}:9911/api?fun=collection`), **add it to the
    Home Screen and run it full-screen**, press start, then save to the public library.
  - "For the same model and system version, collect only once."
  - The SDK has `/config/devicemodel/get`, which returns, per `device_name / model / scale`, a `ver_list[].cfg_list[]`
    with `location`, `crc`, uploader and time; `/device/collection/mouse` (start, stop, status); and
    `.../save`.
- **Meaning:**
  - The iMouse and SOME 3C calibration page is the same design as the `iphone-hid` Safari calibration and the
    feasibility §3.3 web-app advice.
  - More importantly, the vendor ships tables shared **between different phones**. That is indirect evidence that
    iOS acceleration is deterministic per model and iOS version at fixed settings, which bears on R2.
- **Confidence:** [Confirmed] the API. [Likely] reproducibility; theirs may be coarser than 4 pt.
- **Adopt:** a profile cache keyed by `(ProductType, iOS build, Tracking Speed, Sensitivity, orientation)`. A new
  phone then runs only a short validation (a few taps) instead of the full relative calibration.
- **Sources:**
  - iMouse SDK `models/config_model.py`, `api/device_api.py`
  - [iMouse collection doc (snippet)](https://www.imouse.cc/%E5%B8%AE%E5%8A%A9%E6%96%87%E6%A1%A3/%E5%8A%9F%E8%83%BD%E4%BB%8B%E7%BB%8D/%E9%BC%A0%E6%A0%87%E5%8F%82%E6%95%B0%E9%87%87%E9%9B%86/)
  - [SOME 3C API (snippet)](https://doc.some3c.com/iphone-farm-setup/api-documentation)

### 2.7 iOS Shortcuts as a built-in side channel (no third-party app)
- **Evidence:** the iMouse SDK exposes `/shortcut/*`, each call with an `outtime` timeout, meaning the phone answers
  back:
  - clipboard set and get;
  - album and file upload, download, delete;
  - open URL;
  - brightness, flashlight, airplane mode, cellular, WLAN;
  - shut down or restart;
  - get IP.

  Their docs say Chinese text input "must bind a Shortcut": set the clipboard, then paste. AssistiveTouch lets any
  pointer-device button run a Shortcut. Since iOS 17, personal automations (for example "When app X is opened")
  can run immediately, with a notification banner.
- **Uses for `iphone-hid`:**
  - non-ASCII typing: clipboard, then paste;
  - reading state such as clipboard or IP;
  - an **independent phone-side acknowledgement**: an automation "App opened → Get Contents of URL
    `http://box/...`" confirms that an app launch really happened;
  - a recovery restart.
- **Trigger:** map a spare HID mouse button (4 or 5) to a Shortcut in AssistiveTouch › Devices. No pointer
  positioning is needed.
- **Confidence:** [Confirmed] that iMouse does this (API). [Likely] mechanism and trigger. **[Unknown]** Shortcuts
  local-network permission prompts, and whether the owner treats user-made Shortcuts as "no app installed".
- **Sources:**
  - iMouse SDK `api/shortcut_api.py`
  - [iMouse (snippet)](https://www.iosautot.cn/python-xp/)
  - [iDownloadBlog mouse buttons (snippet)](https://www.idownloadblog.com/2023/10/10/how-to-use-mouse-with-iphone/)
  - [Cassinelli: automations run immediately (snippet)](https://matthewcassinelli.com/automations-run-immediately-shortcuts-notifications/)

### 2.8 Confirming that a command reached iOS (cheap, no vision)
- **Caps Lock LED round trip.** Send a Caps Lock tap, then watch the LED byte in CH9329 `GET_INFO` (already parsed
  in `ihc/hid/ch9329.py`), or the ESP32's output-report callback. Then toggle it back.
  - It proves that the iOS HID stack processed a keyboard report end to end, which is stronger than a chip ack.
  - iPad keyboards' Caps Lock LEDs do light, except when "Caps Lock switches language" is on. Turn that off.
  - **[Unknown]** on iPhone; test in T3. Sources: [Apple Community (snippet)](https://discussions.apple.com/thread/251390352),
    `docs/ch9329-protocol.md`.
- **Frame-difference "something changed"** in a region after a tap. This is not recognition. NanoKVM-Go ships frame
  difference detection with about 0.2 s reaction and about 2.5 % CPU. The vendors all verify through video. [Likely]
  ([CNX (snippet)](https://www.cnx-software.com/2026/07/01/sipeed-nanokvm-go-an-4k-usb-c-kvm-with-recall-like-function-ai-integration/)).
- **Shortcuts automation callbacks** (2.7), and the existing calibration-page heartbeat.

### 2.9 Off-the-shelf per-phone unit for USB-C phones (Phase 5 reference)
- **NanoKVM-Go:**
  - 45×40×15 mm; dual A53, with an NPU on Go+;
  - one USB-C cable to the iPhone (DP Alt + HID + PD passthrough via an auxiliary port);
  - **about 60 ms at 1080p60**, Wi-Fi 6, Tailscale, an MCP server, about 1.6 W;
  - US$59–89.
- It is essentially the `iphone-hid` USB-C rig on one board with on-board H.264. Use it as a **benchmark and
  possible Phase 5 hardware**: buy one and compare its absolute accuracy and latency against the MS2109 + CH9329
  rig.
- Openterface Mini-KVM (MS2109 + CH9329 + hubs, under 140 ms (snippet)) is the same parts `iphone-hid` uses,
  pre-packaged.
- **Confidence:** [Confirmed] vendor specs; [Unknown] programmatic API depth and long-run robustness.
- **Sources:** NanoKVM-Go wiki (above);
  [Openterface hardware (snippet)](https://github.com/TechxArtisanStudio/Openterface_Mini-KVM_Hardware).

### 2.10 Multi-phone patterns (what scales in practice)
- **Every vendor uses one HID board per phone.**
  - iMouse and SOME 3C: one "双头" board per phone, in a **20-slot chassis with a 20-port USB backplane and a
    550 W PSU**.
  - EasyClick and devicehub: one ESP32 per phone.
  - EasyClick's own marketing calls Bluetooth boards "一机一板，还要刷固件、配对" (one board per phone, plus
    flashing and pairing).
- **devicehub gives each board a unique BLE identity:** a `N<name>` command sets the advertised name and
  randomizes 3 MAC bytes, so iOS never confuses boards. Worth copying for ESP32 pairing hygiene. [Confirmed, code]
- **One ESP32 serving several iPhones** (NimBLE allows up to about 9 connections) is theoretically possible, but no
  vendor does it, and per-link connection-event timing would hurt relative-mode pacing. **Not recommended**
  [Unknown] ([ESP32 forum (snippet)](https://www.esp32.com/viewtopic.php?t=4026)).
- **Video scales by receiver instances** (AirPlay) or by USB 2 buses (HDMI capture, R7). The iMouse config also
  shows the image-analysis worker pools (`opencv_num`, `ocr_num`).

---

## 3. Looked promising, but dead ends for this project

| Idea | Why not | Evidence |
|---|---|---|
| **HID digitizer / touchscreen** (real touches) | BLE digitizer and stylus descriptors worked up to **iOS 13.3.1** and stopped in **13.4**. GameSir and Flydigi "Bluetooth touch mapping" broke at the same time (Apple "closed the touch-screen interface while optimizing CarPlay"). USB digitizer on current iOS: Aiden says it is not converted to a cursor, and whether it produces touches is untested. NanoKVM's "Multi-touch Screen" mode is documented for controlling from phones, not for iPhone targets. Low prior; only worth a 1-hour ESP32-S3 USB test if everything else fails | [Confirmed] [forum 699205](https://developer.apple.com/forums/thread/699205); [Apple Community (snippet)](https://discussions.apple.com/thread/251260646); [GameSir G6 13.4 guide (snippet)](https://doc.xiaoji.com/en/g6/detail/544.html) |
| **Game converters' "exact taps"** | After iOS 13.4 they use mouse clicks through AssistiveTouch (GameSir fw 1.25; **broken again from iOS 14.2**, cause unknown), game-integrated SDKs (Flydigi 智联, only in partner games), modified IPAs (改包), or physical capacitive emitters | [GameSir (snippet)](https://gamesir.com/pages/g6-solution); [Flydigi 智联 (snippet)](https://zhuanlan.zhihu.com/p/57406933); [Flydigi capacitive (snippet)](http://www.gamelook.com.cn/2018/06/333423/) |
| **Disabling pointer acceleration** | No setting exists (iPadOS 26 still has none); vendors either go absolute or tabulate it | [Apple Community (snippet)](https://discussions.apple.com/thread/256143470) |
| **Voice Control grid** ("Show grid", "Tap 22", recursive sub-grid) | Deterministic positions, but needs spoken audio into the mic: slow and fragile. Only as a manual fallback | [BBC a11y (snippet)](https://bbc.github.io/accessibility-news-and-you/assistive-technology/testing-steps/voice-control-ios.html) |
| **Switch Control point scanning** | Timing-based gliding crosshair; slow, and accuracy depends on the scan speed | [Apple 119835 (snippet)](https://support.apple.com/en-us/119835) |
| **Physical capacitive tappers / robot arms** | Fixed positions or expensive; at most about 10 taps/s; no keyboard | See table rows 8–9 |
| **QuickTime USB capture (qvh, 3uAirPlayer USB)** | Needs host iPhone tooling and Trust pairing (forbidden by the project rules); status bar forced to demo mode. Low latency, and it would free Lightning phones from the AV adapter, so revisit only if the rules change | [qvh README](https://github.com/danielpaulus/quicktime_video_hack); [3uAirPlayer (snippet)](https://www.3u.com/tutorial/articles/14739/3uairplayer-ios-device-user-guide-dual-wireless-and-usb-cable-solution) |
| **Cloud phones / proxy IPA / EasyClick "USB_HID 免硬件"** | Install a signed app, a runner or WDA, or need Developer Mode | [testerhome (snippet)](https://testerhome.com/topics/20866); [EasyClick (snippet)](https://juejin.cn/post/7683016577462288419) |
| **Host Bluetooth as the HID** (Wormhole style) | One radio identity per host; no gain over one ESP32 per phone | [sspai (snippet)](https://sspai.com/post/60970) |

---

## 4. Answers to the specific questions

- **Digitizer HID treated as real touches or absolute positions?** Only up to iOS 13.3.1 over BLE [Confirmed].
  Today, absolute positioning comes from an **absolute mouse descriptor + AssistiveTouch** over USB (three vendors)
  and, reportedly, over BLE on iOS 17+ (EasyClick). The pre-2020 key-mapping converters used the digitizer; the
  post-2020 ones use AssistiveTouch clicks, where "R1 is fixed at the top-left corner" is a corner-slam artefact.
- **AirPlay as a video path.** It is what iMouse, SOME 3C and Wormhole use.
  - Latency is about 100–200 ms: AirServer about 150 ms (snippet), a vendor SDK claims 120 ms (snippet). The
    DP-Alt path (NanoKVM-Go) is about 60 ms.
  - UxPlay supports one phone per instance and H.264 passthrough (`-vrtp`) [Confirmed].
  - It needs a network per phone (Wi-Fi, or USB Ethernet through OTG) and a HID tap to start mirroring.
- **Neutralising acceleration or snapping.** There is no OS switch.
  - Absolute mode (2.1, 2.2) is the real fix.
  - Otherwise: a fixed step table at a fixed cadence with max tracking speed (devicehub), edge anchoring away from
    the rounded corner, and per-(model, iOS) shared tables (iMouse).
  - EasyClick's relative firmware applies a single "compensation rate" (gain) and still drifts on long swipes.
- **Confirming delivery.** The vendors check through video. Cheap additions: the Caps Lock LED round trip, frame
  difference, and Shortcuts callbacks (2.8).
- **Several phones per box.** One HID board per phone behind USB hubs or backplanes, with a unique BLE name and MAC
  per board; video through one AirPlay receiver per phone, or one capture card per USB 2 bus. Nobody multiplexes
  one HID radio across phones.

---

## 5. Suggested test-plan deltas

- **T1:** also test after enumeration with the "release all + idle absolute report" repair (NanoKVM). Enable
  Orientation Lock. Record whether 0..4095 maps to the full panel or to a cropped area; EasyClick hints at
  notch-dependent scale.
- **T3:** add the Caps Lock LED round trip as an end-to-end delivery probe.
- **T4:** compare corner-slam anchoring with **devicehub edge anchoring**. Run Tracking Speed/Sensitivity at
  **max and min**. Use a 3-size step table (1/4/8 counts) at 15–20 ms.
- **T5:** add Tab+letter FKA chords (remapped commands) against Cmd chords.
- **T9 (b0, new, first):** ESP32 BLE with an **absolute** descriptor on iOS 17+/26/27.
- **T9 (e, extended):** Lightning Camera Adapter + hub with CH9329 (absolute) + USB Ethernet, with AirPlay (UxPlay
  `-vrtp`) latency measured glass to glass. This needs owner approval.
- **Optional:** buy one NanoKVM-Go (about US$60–90) as a reference for accuracy and latency on an iPhone 15.
