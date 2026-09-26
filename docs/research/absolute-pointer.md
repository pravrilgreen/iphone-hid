# Absolute positioning on iPhone over USB: research notes

- **Date:** 2026-09-24; updated 2026-09-25; revised 2026-09-26 to match the current box
  ([ADR 0002](../dev/adr/0002-box-in-go.md)). It built on the feasibility study (`docs/research/feasibility.md`,
  assumption A1 and section 3.1). That study was removed with the Python box software in commit `efe4582` and is
  in the git history.
- **Scope:** USB-C iPhones only. One Orange Pi 5 Plus box per phone runs `ihcd`. Its USB-C port is a Linux USB
  gadget: the phone's absolute pointer, keyboard and media keys. The box drives the absolute pointer only, and an
  iPhone 15 follows it (§0). The project has no CH9329 backend and no Bluetooth. Bluetooth findings stay in §2
  only as evidence of how iOS treats absolute pointers. CH9329 findings stay as market facts. Earlier versions
  used test IDs (B1–B8, D1–D3) from the phase-0 checklist, removed in the same commit. §7 now uses `ihcd hid`,
  `ihcd gadget`, `ihcd doctor` and the [hardware check](../guide/hardware-check.md).
- **Method:** read source code and git history directly (Aiden, glassbox, mirrordeck, JetKVM, PiKVM
  kvmd, blacktop/ipsw-diffs, pcairplay), read Apple Developer Forums threads and the Accessory Design
  Guidelines R31 PDF directly, and used web search for the rest. Many sites are blocked from this
  environment (support.apple.com, discussions.apple.com, CSDN, esp32.com, forum.arduino.cc, oshwhub,
  wch.cn, ascript.cn, abilitynet, Nordic DevZone, macrumors). Claims taken only from search-result
  snippets are marked **(snippet)**. Re-open those pages before relying on them.
- **Labels:** [Confirmed] = primary source (source code, Apple document, the author's own commit or
  test notes) says it plainly. [Likely] = credible but indirect, or not yet run on our hardware.
  [Unknown] = no reliable source, so it needs a hardware test. [Contradicted] = the evidence goes
  against a current assumption.
- **Licensing:** Aiden is AGPL-3.0 and mirrordeck is GPL-3.0; glassbox, pcairplay and JetKVM were
  read for behaviour only. No code from any of them was copied. Descriptor bytes are quoted only to
  compare structure. All of them follow the HID 1.11 spec.

---

## 0. Verdicts

| # | Question | Verdict | Main evidence |
|---|---|---|---|
| V1 | Does an iPhone follow a USB HID **absolute mouse** (Generic Desktop Mouse, X/Y `Input(Abs)`, 0..32767)? | **[Confirmed]** by two independent 2026 projects. This upgrades feasibility A1 from [Likely]. | Aiden, the iOS default since April 2026 (§1.1). glassbox on an iPhone 17 Pro Max with iOS 26.5 fits a linear map from logical 0..32767 to screen pixels (§1.2). |
| V2 | Is AssistiveTouch required on iPhone? | **[Confirmed]**, yes, for absolute and relative alike | Aiden, glassbox, mirrordeck, AScript (snippet), HijelHID (tested on iOS 26.3) |
| V3 | Does the pointer jump straight to the target? | **[Likely]** no. It glides there quickly, so a click must wait 80–250 ms. The box waits 80 ms (`--settle`). | Aiden: 80 ms "iOS cursor animation". glassbox: settle of at least 250 ms, "proved". mirrordeck: iOS queues reports and replays the path (§5). |
| V4 | Absolute over **Bluetooth**? (out of scope; iOS evidence only) | **[Likely]** works on iOS 18–26 over both Classic and BLE. BLE is the weaker case (snippet only). The 2020 "does not move" report was a flawed test. | mirrordeck (Classic, iPhone 15 Pro, iOS 26.5). Forum 770639 (Classic, iPad, 2024). AScript's BLE firmware "abs mode recommended for iOS 18+" (snippet) (§2). |
| V5 | Are **digitizers** (touch screen 0x0D/0x04, pen, touchpad, precision touchpad) treated as touches or as a pointer? | **[Contradicted]** as a way in. Since iOS 13.4 they are ignored or blocked. Adding a Touch Screen collection next to the mouse **breaks mouse clicks**. | iOS 13.4 blocked BLE touch-screen devices. Aiden: "iOS do not automatically convert Digitizer input". glassbox: "digitizer/touchpad … ignored by iOS". mirrordeck: a digitizer made iOS stop honouring mouse buttons (§3). |
| V6 | iPhone vs iPad | **[Confirmed]** they differ. iPad drives its native pointer from an absolute mouse without AssistiveTouch. An accessory trackpad using the Apple descriptor is still ignored on iPad without MFi. | glassbox (iPad mini 7), PiKVM #1202, ADG §15, glassbox's trackpad experiment (§1, §3) |
| V7 | Does the **CH9329** `0x04` absolute command work on iPhone? (market question; the project has no CH9329 backend) | **[Unknown]**. There are no reports either way. The chip's single mouse interface carries relative (ID 1) and absolute (ID 2) reports; that layout has not been tested on iOS. | A seller or blog line says CH9329 absolute works "only on Windows" (snippet). A composite of absolute and relative works over USB (separate interfaces) and over Bluetooth (one map) (§4). |
| V8 | What does the descriptor need? | **[Likely]**: Mouse, then Pointer, then Physical collection, with buttons and 16-bit X/Y `Abs` over 0..32767. Report ID and physical range are optional. A relative mouse may coexist. **No digitizer collection.** Always send the real position. | Comparison of four working descriptors (§4) |
| V9 | iOS 26 and iOS 27 changes | iOS 26.x: absolute works (V1). **[Unknown]** for iOS 27. Its backboardd pointer and digitizer code was refactored and iPhone gained new "Pointers" settings strings. Run the hardware check and §7.2 again on 27.0. | blacktop/ipsw-diffs 26.5 vs 27.0 (iPhone18,1) (§6) |

**Consequence for the design:** the box uses the absolute pointer only (ADR 0002). Its USB gadget
(`box/internal/hid`, descriptor `DescAbsolute`) exposes it in the layout that works (§4): its own
interface, no report ID, 0..32767, pointer interface last in the default profile `RA`. Do not add a
digitizer collection to any production descriptor.

**Field result (2026-09-25):** on this project's box, an iPhone 15 follows the absolute pointer of the
default gadget profile `RA` (relative mouse, then absolute pointer, as separate interfaces): `abstest`, a
command of the since-removed `hidtest` tool, reached the corners. `ihcd hid corners` is the current check.
The iOS version was not recorded.

---

## 1. Absolute mouse over USB: field evidence

### 1.1 Aiden (AidenAI-IO/aiden-firmware, Linux USB gadget)
- **[Confirmed]** (current source, read 2026-09-24, HEAD `9de02f5`).
  - `device_type = "iOS"` gives `pointer_mode = "absolute"`. Android uses `"touchscreen"`, a digitizer.
  - The iOS pointer is a separate HID interface (protocol 0, subclass 0) with no report ID. It
    declares Mouse > Pointer > Physical, 8 buttons, X/Y 16-bit `Abs` 0..32767 and a relative wheel.
  - No physical minimum or maximum is declared.
  - Sources: [aiden-usb-gadget](https://github.com/AidenAI-IO/aiden-firmware/blob/main/overlay-debian/usr/lib/aiden/aiden-usb-gadget),
    [usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md).
- **[Confirmed]** The interface order matters: `keyboard → Consumer Control → pointer → ECM`.
  - With the pointer right after the keyboard, the soft keyboard came back only about 80% of the time
    after a re-enumeration; with this order it came back 10 of 10 times.
  - Changing the descriptor needs a new PID and serial, "so iOS does not reuse the pointer-bearing
    descriptor".
  - Source: [usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md), commit `c001083`.
- **[Confirmed]** iOS timing constants in
  [hid_provider.go](https://github.com/AidenAI-IO/aiden-firmware/blob/main/src/agent/internal/agent/mnk/hid_provider.go):
  - `defaultCursorSettleMs = 80 // iOS cursor animation`;
  - `defaultTapHoldMs = 60 // iOS drops faster events`;
  - release sent 3 times, 15 ms apart.
- **[Confirmed]** Aiden writes its reports to `/dev/hidg0`, the node of the Linux HID gadget function
  ([usb-hid.md](https://github.com/AidenAI-IO/aiden-firmware/blob/main/docs/03-services/usb-hid.md)).
  The box works the same way (`box/internal/hid`).
- The iPhone model and iOS version are not stated. A protocol example uses `iPhone16,2` (15 Pro Max).

### 1.2 glassbox (yoyicue/glassbox): the closest analogue to this project
- **[Confirmed]** Same approach as ours: HDMI capture plus USB HID. The iPhone sits on an Apple USB-C
  Digital AV Multiport Adapter; a Luckfox PicoKVM USB gadget plugs into its USB-A port.
  - AssistiveTouch is mandatory on iPhone.
  - "PicoKVM logical coordinates (absolute, max 32767) are mapped to decoded frame pixels via a
    calibrated linear fit."
  - Bring-up on an **iPhone 17 Pro Max** on 2026-05-21. The keyboard doc names **iOS 26.5**.
  - Sources: [README](https://github.com/yoyicue/glassbox/blob/main/README.md),
    [ios_full_keyboard_access_commands.md](https://github.com/yoyicue/glassbox/blob/main/docs/reference/ios_full_keyboard_access_commands.md).
- **[Confirmed]** The calibration numbers in
  [config.py](https://github.com/yoyicue/glassbox/blob/main/glassbox/effectors/picokvm/config.py)
  are `scale_x 0.01363`, `scale_y 0.02968`, `offset (736.4, 53.8)`.
  - So 0..32767 covers about 447×973 px of a 1920×1080 frame.
  - The aspect ratio of 0.459 matches the 17 Pro Max (440×956 pt).
  - The **whole range maps linearly onto the whole mirrored screen.**
- **[Confirmed]** Tap timing, same file:
  - a tap is two absolute reports, then **settle of at least 250 ms**, then press for 100 ms, then release;
  - "move → settle ≥250ms → down 100ms → up" was "proved" on a PicoKVM matrix on 2026-05-21;
  - a long press needs at least 1500 ms (900 ms was inconsistent).
- **[Likely]** glassbox also fixes AssistiveTouch **Tracking Speed = slowest** and **Tracking
  Sensitivity = highest** "so the calibrated logical→pixel fit reproduces" (README). No experiment
  says these sliders affect absolute positioning, but no experiment rules it out either (§5).
- **[Likely]** The descriptor was not read directly. glassbox describes it as "keyboard + absolute
  mouse + relative mouse + report-ID-2 wheel"
  ([ipad_mini_migration.md](https://github.com/yoyicue/glassbox/blob/main/docs/design/ipad_mini_migration.md)).
  - That matches JetKVM's gadget: absolute pointer as **report ID 1** with 5 buttons, X/Y 0..32767
    and physical 0..32767; relative wheel and AC Pan as ID 2; and a separate relative-mouse
    interface ([hid_mouse_absolute.go](https://github.com/jetkvm/kvm/blob/HEAD/internal/usbgadget/hid_mouse_absolute.go)).
  - The PicoKVM RPC names (`absMouseReport`, `wheelReport`) are JetKVM's.
  - So a **report-ID'd** absolute pointer next to a relative mouse works over USB.

### 1.3 iPad data points (a different input path)
- **[Confirmed]** iPadOS drives its **native** pointer from the same absolute mouse without
  AssistiveTouch. Taps landed on Settings rows and Home icons on an iPad mini 7
  ([ipad_mini_migration.md](https://github.com/yoyicue/glassbox/blob/main/docs/design/ipad_mini_migration.md)).
- **[Likely]** PiKVM's absolute mouse on an iPad target (iPad Pro 11 and iPad mini, January 2024):
  movement, right click and keyboard worked, but left click was ignored. The issue is closed as
  "fixed" and the fix is not visible
  ([#1202](https://github.com/pikvm/pikvm/issues/1202); descriptor in
  [kvmd mouse.py](https://github.com/pikvm/kvmd/blob/master/kvmd/apps/otg/hid/mouse.py)).
- **[Likely]** JetKVM (absolute by default) was controlling an iPad until release 0.3.8 "killed mouse
  right click" in March 2025 ([jetkvm #278](https://github.com/jetkvm/kvm/issues/278)).
- Taken together: iPad-native pointer paths have had **button bugs** with absolute pointers. The
  iPhone path (AssistiveTouch) is a different code path; §1.1 and §1.2 show clicks work there.

---

## 2. Absolute over Bluetooth (out of scope; iOS evidence only)

The project is wired USB only. This section is kept because it shows how iOS handles absolute pointers
and cached descriptors in general; §4 and §5 draw on it.

| Source | Link | Device / iOS | Result |
|---|---|---|---|
| **mirrordeck** spike + commits `007a52c`, `0159877` ([spike README](https://github.com/egarl004/mirrordeck/blob/main/spikes/bluetooth-keyboard/README.md), [commit](https://github.com/egarl004/mirrordeck/commit/0159877)) | **Classic** HIDP from a Mac | iPhone 15 Pro, iOS 26.5, 2026-08-30 | **[Likely] works:** "16-bit X/Y over 0...32767 with Input(Data,Var,Abs), so the phone's pointer lands where the cursor is". "A zeroed report flings the pointer to the corner." These are the author's notes; there is no test log. |
| Apple forum [770639](https://developer.apple.com/forums/thread/770639) | Classic (`A1 03` header) | iPad, December 2024 | Absolute 0..32767: "mouse coordinates are updated well" and press works, but the **release is ignored**. No answer. |
| AScript ESP32 HID firmware ([docs](http://ascript.cn/docs/ios/esp32/)) **(snippet)** | **BLE** (supports ESP32-C3, which is BLE-only) | iOS 18+ | "abs mode (recommended for iOS 18+) only needs AssistiveTouch on". Switching modes requires "Forget This Device", because iOS caches the HID descriptor; otherwise "connects but clicks do nothing". Without AssistiveTouch "the pointer can move but clicks don't work". |
| Apple forum [652700](https://developer.apple.com/forums/thread/652700) | BLE, nRF52 | iPad, iOS 13, 2020 | "The mouse no longer moves." **The test was flawed**: only the `Input` flag was changed from Rel to Abs, and the logical range stayed −127..127, so small "deltas" park the pointer near the centre. It is weak evidence against absolute. |
| Forum [712996](https://developer.apple.com/forums/thread/712996) (2022), [ESP32-BLE-Abs-Mouse](https://github.com/sobrinho/ESP32-BLE-Abs-Mouse), [microbit-pxt-blehid](https://github.com/bsiever/microbit-pxt-blehid) (iOS 15.1 on iPhone 11 Pro: absolute mouse not marked for iOS) | BLE | iOS 13–15 era | Unanswered, not ticked, or not supported. These are old, untested negatives. |
| [pcairplay](https://github.com/gbulog/pcairplay), [CommandAGI relay](https://github.com/CommandAGI/commandagi-firmware-ESP32-BLE-relay/blob/HEAD/NOTES.md), [TouchMirror](https://github.com/thexch/TouchMirror) | BLE | none | Designs with a relative and an absolute collection in one BLE report map, **explicitly untested** on hardware ("à tester", "compile- and parser-tested"). Not evidence. |

- **[Confirmed]** iOS caches the report map for the life of the bond. Any change needs "Forget" on the
  phone and re-pairing. mirrordeck: "Several conclusions during this investigation were measured
  against a stale cached descriptor and were wrong as a result." AScript says the same (snippet). Over
  USB the equivalent is a new serial or PID (§4 rule 6).

---

## 3. Digitizers, touchpads and "real touches"

- **[Likely]** Up to **iOS 13.3.1**, Bluetooth HID touch-screen and stylus digitizers produced touches
  on iPhone. Game key-mapping adapters (Flydigi and "Gtouch"-style boxes) relied on this.
  - **iOS 13.4 (March 2020) blocked it**: "Bluetooth low energy(BLE) HID device is invalid on iOS
    13.4 or iOS 13.5, but it's ok on iOS 13.3.1 and below". That device's descriptor was a digitizer
    (Usage Page 0x0D, Stylus 0x20).
  - Sources: [forum 699205](https://developer.apple.com/forums/thread/699205);
    [Apple Community 251260646](https://discussions.apple.com/thread/251260646) (snippet);
    Flydigi: "苹果在iOS 13.4的更新中关闭了触屏接口" ([bbs.flydigi.com/detail/7520](http://bbs.flydigi.com/detail/7520), snippet).
  - iPadOS 13.4 is also the release that added the native iPad pointer.
- **[Confirmed]** Aiden commit `d65e498` (April 2026) replaced a Touch Screen digitizer with an absolute
  mouse because "macOS/iOS do not automatically convert Digitizer input to cursor movement"
  ([commit](https://github.com/AidenAI-IO/aiden-firmware/commit/d65e498)).
- **[Confirmed]** glassbox (May 2026): "HID digitizer / touchpad / Magic-Trackpad input is ignored by
  iOS — only Generic-Desktop mouse works."
  - On iPad they loaded the **ADG chapter 15 multi-touch trackpad descriptor** in four variants
    (descriptor, VID/PID, interface conflicts). "iPadOS silently dropped every INPUT report."
  - They concluded native multi-touch HID is "gated on MFi-IC or USBDriverKit-app".
  - Sources: [scroll_overshoot_efficiency.md](https://github.com/yoyicue/glassbox/blob/main/docs/goals/scroll_overshoot_efficiency.md),
    [ipad_mini_migration.md](https://github.com/yoyicue/glassbox/blob/main/docs/design/ipad_mini_migration.md).
  - ADG R31 §15 documents accessory trackpads (Digitizer page, Touch Pad) "starting in iPadOS 14.5",
    for iPad only ([ADG PDF](https://developer.apple.com/accessories/Accessory-Design-Guidelines.pdf), pp. 107–113).
- **[Likely]** A digitizer **harms** the mouse in the same device (mirrordeck `0159877`, iPhone 15
  Pro, iOS 26.5, Classic):
  - "Usage (Touch Screen) makes iOS treat the whole service as display-integrated, which reclassifies
    the device: the AssistiveTouch pointer changes from a round dot to an arrow and the mouse button
    bits stop being honoured. Removing the collection restores both."
  - This matches backboardd strings on iOS 26.5 (`_displayIntegrated`, "Not supported: accessibility
    digitizer for external display").
- **[Likely]** (snippet) A USB STM32 test in 2022, probably iOS 15
  ([CSDN MADCODING](https://blog.csdn.net/MADCODING/article/details/124124655)):
  - a single-point touch-screen digitizer: iOS "recognised clicks but coordinates stuck at 0,0";
  - an absolute mouse "moved in small amounts with unclear calculation".
  - The cause was not explained. It is the only USB negative report found and predates iOS 26.
- **Mouse Keys** (AssistiveTouch moves the pointer from the keypad) is not a way around this.
  - mirrordeck found "iOS never attaches an accessibility keyboard filter to this device", so the
    keys typed digits.
  - mirrordeck also says Apple documents that Mouse Keys disables text entry while it is on.

---

## 4. What the descriptor needs (Q3)

**Working absolute descriptors on iPhone or iPad, compared (structure only):**

| Aspect | Aiden USB (iPhone) | JetKVM / PicoKVM USB (iPhone 17 PM) | mirrordeck Classic (iPhone 15 Pro) | Ours: gadget `DescAbsolute` (`box/internal/hid`) |
|---|---|---|---|---|
| Collections | Mouse > Pointer > Physical | Mouse > [ID 1] Pointer > Physical | Mouse [ID 2], buttons at application level, Pointer > Physical around X/Y only | Mouse > Pointer > Physical |
| Report ID | none (own interface) | 1 (wheel is ID 2 in the same collection) | 2 (keyboard ID 1, relative mouse ID 3 in the same map) | none (own interface) |
| Buttons | 8 | 5 + pad | 2 + pad | 3 + pad |
| X/Y | 16-bit, 0..32767, Abs | 16-bit, 0..32767, physical 0..32767 | 16-bit, 0..32767 | 16-bit, 0..32767 |
| Wheel | relative, in the collection | separate report ID | none (uses the relative report) | relative, in the collection |
| Relative mouse in the same device | no | yes (separate interface) | yes (same map) | yes in profile `RA` (the default; separate interface, present but never used); none in profile `A` |

**Rules drawn from the comparison:**
1. **[Likely]** Use Generic Desktop **Mouse (0x02)** with an `Input(Data,Var,Abs)` X/Y pair. The
   logical range should be **positive**, 0..32767 in every working case.
   - Never use a signed or −127..127 absolute range (the 2020 BLE failure).
   - Linear mapping over the full screen (glassbox fit, Aiden's normalisation).
   - The CH9329's 0..4095 grid should work the same way, but that is **[Unknown]**.
2. **[Likely]** Report ID, physical minimum/maximum and units are **optional**. Aiden uses none;
   JetKVM uses both. Buttons may sit inside or outside the Physical collection. mirrordeck's claim
   that buttons must sit outside it was measured with the digitizer confound.
3. **[Likely]** Include buttons. Every working case has them, and AssistiveTouch clicks with button 1.
   Nobody has reported an absolute pointer without buttons working.
4. **[Likely]** The absolute collection **need not be the only pointer**. It worked next to a relative
   mouse over USB (glassbox) and over Bluetooth (mirrordeck).
   - **[Unknown]** whether the CH9329 layout works, where a relative and an absolute report live in
     one mouse interface with IDs 1 and 2. Nobody has dumped the CH9329 report descriptor publicly.
5. **[Contradicted]** "Adding a digitizer as a second route is harmless." It is not; see §3.
6. **[Confirmed]** iOS caches descriptors.
   - Over USB, change the PID or serial when the descriptor changes (Aiden).
   - Over Bluetooth, forget the device and re-pair (mirrordeck, AScript snippet).
7. CH9329 note (snippet): Chinese blogs and a seller page say CH9329 "绝对鼠标和多媒体键盘功能只支持
   Windows" ([oshwhub](https://oshwhub.com/zhqsoft/CH9329-M01),
   [CSDN](https://blog.csdn.net/qishi3250/article/details/130596635)).
   - The official datasheet V1.0 text says only that Windows, Android and Apple systems have built-in
     drivers ([WCH](https://www.wch.cn/products/ch9329.html)).
   - Read the "Windows only" line as a lack of testing, not as proof about iOS.

---

## 5. Behaviour and settings to plan for

- **Glide, not teleport.** [Likely] iOS animates the pointer to the new absolute position.
  - Aiden waits 80 ms; glassbox proved 250 ms is safe. The box waits 80 ms after a jump before a press
    (`--settle`); §7.2 check 3 measures what a phone needs.
  - backboardd has a `BKMousePointerAnimationDriver`
    ([ipsw-diffs backboardd](https://github.com/blacktop/ipsw-diffs/blob/HEAD/26_5_23F77_vs_27_0_24A5355q/MACHOS/filesystem/usr/libexec/backboardd.md)).
  - Too many reports **queue up and iOS replays them** after the source stops (mirrordeck). For a jump,
    send one target report (glassbox sends it twice), not a stream. The box does this for scripted
    actions. Live input and gestures are a stream, but the box keeps one report in flight: it writes the
    next one after the phone has taken the last, and a waiting move is replaced by the newest
    ([architecture](../dev/architecture.md)). Hardware check 14 looks for a trailing pointer.
- **Every report is a position.** [Likely] A report with X=Y=0 moves the pointer to the top-left
  (mirrordeck; the same thing happened on PiKVM's Linux target,
  [#1437](https://github.com/pikvm/pikvm/issues/1437)).
  - Press and release must repeat the current X/Y. `ihcd` does this: each press and release carries the
    last position it sent. §7.3 item 3 notes the one gap.
  - Keep-alives and initial values must never be (0,0) (§7.3).
- **Release reliability.** [Likely] Send the release 2–3 times at the same position and hold the press
  for at least 60–100 ms (Aiden, glassbox). There is an iPad report of a stuck release (770639). The box
  holds a tap 80 ms and sends the release three times, 15 ms apart.
- **AssistiveTouch.**
  - [Confirmed] It is required on iPhone.
  - [Unknown] whether iOS 26 on iPhone shows a native **arrow** pointer without AssistiveTouch that
    moves but does not click. AbilityNet's iOS 26 guide describes an arrow with a red border when
    AssistiveTouch is off
    ([AbilityNet](https://mcmw.abilitynet.org.uk/how-to-use-a-mouse-or-trackpad-with-your-iphone-or-ipad-in-ios-26), snippet).
    AScript says the pointer moves without AssistiveTouch but clicks fail (snippet). mirrordeck saw
    the arrow plus ignored buttons when the device was reclassified.
  - **If the hardware check shows an arrow pointer, the setup is wrong. A round dot is expected.**
- **Tracking Speed / Sensitivity.** [Unknown] whether they affect absolute mode (glassbox pins them
  anyway). §7.2 check 6 compares two slider values.
- **Pointer Control, Dwell, Hot Corners, Zoom.** The feasibility study (§3.1) found, [Likely], that Hot
  Corners run only with Dwell on, and that Zoom with Pan set to Edges moves the screen when the pointer
  reaches an edge. Keep Dwell and Zoom off. Also keep Eye/Head Tracking and any "Snap to Item" option
  off. Snap to Item moves the pointer to the nearest element (eye tracking, snippet) and would corrupt
  absolute taps.
- **Landscape.** [Unknown] for absolute. The relative path has a landscape axis bug (forum 786963) and
  jailbroken HID injection uses a fixed-orientation space
  ([ios-mcp #14](https://github.com/witchan/ios-mcp/issues/14)). The box can take a landscape mirror
  (`--landscape`, or the console's Landscape switch). That changes only the video crop: touch
  coordinates go out as fractions of the view, not rotated. Landscape taps land only if iOS turns the
  absolute axes with the screen. No test of this is recorded (§7.2 check 10).
- **"Devices" page.** Button remapping lives under *AssistiveTouch > Devices > [device]*. Each USB
  identity is its own entry. With separate interfaces, iOS may list the relative and absolute
  interfaces separately; check which one gets the Home/App Switcher mapping (§7.2 check 9).

---

## 6. iOS 26 and iOS 27 (Q4)

- **[Confirmed]** Absolute works on iOS 26.x: glassbox on 26.5 (USB), mirrordeck on 26.5 (Classic),
  Aiden through September 2026 (USB).
- **[Likely]** iOS 26 changed pointer UI settings (Pointer Style, Tracking Sensitivity, a "Pointer
  Devices" section) ([AbilityNet](https://mcmw.abilitynet.org.uk/how-to-make-it-easier-to-use-a-mouse-or-trackpad-with-your-iphone-or-ipad-in-ios-26), snippet).
  The iOS 26.0 `assistivetouchd` diff adds pointer and eye/head-tracking code
  (`_handleHeadTrackingMovementWithAbsolutePoint:`) but nothing about HID absolute
  ([ipsw-diffs 18.5 vs 26.0](https://github.com/blacktop/ipsw-diffs/blob/HEAD/18_5_22F76__vs_26_0_23A5260n/MACHOS/assistivetouchd.md)).
- **[Confirmed]** The iOS 26.5 `backboardd` on iPhone18,1 contains an absolute-pointer model:
  - `pointerAbsolutePosition`, `absolutePositionIsValid`, `observeMouseAbsolutePointDidChange:`;
  - "`_setPointerPosition: cannot reposition model point when the user is using a pointing device
    with absolute positioning`".
  - So iOS supports absolute pointing devices by design, not by accident.
  - In the **iOS 27.0 beta** (24A5355q) these strings and most mouse-pointer and digitizer symbols
    left backboardd. There is a new `BKHIDMousePointerEventProcessor` class and a new
    `BackBoardHIDTouchEventProcessor.framework`.
  - The iPhone build also gains `GeneralSettingsUI.framework/Pointers.loctable`, possibly a native
    pointer settings page on iPhone.
  - Sources: [backboardd diff](https://github.com/blacktop/ipsw-diffs/blob/HEAD/26_5_23F77_vs_27_0_24A5355q/MACHOS/filesystem/usr/libexec/backboardd.md),
    [diff README](https://github.com/blacktop/ipsw-diffs/blob/HEAD/26_5_23F77_vs_27_0_24A5355q/README.md).
  - **[Unknown]** what changes for users. This is a refactor plus possibly new settings, so **the
    hardware check and the §7.2 checks must run on 27.0**, as the feasibility study's risk R10 already
    said.
- **[Likely]** Earlier regressions: iOS/iPadOS 18.4.1 degraded external HID input (lag, dropped
  modifiers) on iPhone 16 Pro Max and iPads
  ([forum 781674](https://developer.apple.com/forums/thread/781674)). Pin iOS versions in the farm.

---

## 7. Recommendations

### 7.1 CH9329 descriptor dump (dropped)

Dropped: the box's own gadget drives the absolute pointer on an iPhone 15, so the CH9329 fallback is not
needed, and the project has no CH9329 backend (ADR 0002). The plan was to dump the CH9329's descriptors
with `lsusb -v` and `usbhid-dump` and check its `0x04` absolute report with `evtest`. Nobody has published
that descriptor, so V7 stays [Unknown] as a market question.

### 7.2 Checks on the iPhone (iPhone 15+ over the USB-C hub, iOS 26.x, then 27.0)

The [hardware check](../guide/hardware-check.md) is the routine test: run it on each new box, after
each iOS update, and on iOS 27.0. The checks below go further and answer the open questions of this
document. `ihcd doctor` first confirms the board, the gadget and the USB link. Stop the service while
using `ihcd hid` (`sudo systemctl stop ihcd`). Run both gadget profiles. **Always run B, even if A
passes.**

| Run | Gadget | Set up with | Why |
|---|---|---|---|
| A | Profile `RA` (default): keyboard, consumer control, relative mouse (present, unused), absolute pointer | `sudo systemctl restart ihcd-gadget` (the boot default) | What the box ships with; confirmed on an iPhone 15. It matches the glassbox/JetKVM case (absolute next to a separate relative interface). |
| B | Profile `A`: no relative mouse | `sudo ihcd gadget up --replace --profile A` | The reference layout (own interface, no ID, 0..32767), as in Aiden. If B passes and A fails on some phone or iOS version, the relative interface next to it is the problem, not iOS. |

Runs C and D (the CH9329 in modes 0x00 and 0x02) are dropped: the absolute pointer of the box's own
gadget is confirmed on an iPhone 15, so the CH9329 fallback is not needed.

Checks for each run:
1. The pointer is a **round dot** (AssistiveTouch). An arrow means reclassification or AssistiveTouch
   is off: stop and fix.
2. **Mapping:** `ihcd hid corners` must reach the four corners and the centre. Then tap targets near
   the edges with `ihcd hid tap X Y`, including the status-bar and home-indicator zones and the dock
   (`0.5 0.95`), and note any that miss. The box maps 0..32767 linearly onto the whole screen, with no
   calibration.
3. **Settle time:** set `IHCD_ARGS="--settle <d>"` in `/etc/default/ihc` and restart the service, for d
   in {0, 30, 60, 80, 150, 250} ms. At each value, tap two small targets far apart in turn, 10 times
   each, through the API (`tap`). Record the first value at which every tap lands. The default is 80 ms.
4. **Clicks through the absolute report:** 50 taps at one point with `ihcd hid tap` (press held 80 ms,
   release sent three times, 15 ms apart). Count stuck presses: a press that stays down shows as a drag
   or a lifted icon.
5. **Zero report:** `ihcd hid corners` starts with a report at (0,0) and no buttons. The pointer must go
   top-left. This confirms absolute semantics; Hot Corners must be off. Also note where the pointer sits
   right after the phone enumerates the gadget.
6. **Tracking Speed** at minimum and maximum: repeat `ihcd hid corners` and 5 taps and expect no change.
   Record either way.
7. Mixing a relative move after an absolute one: dropped. The box uses the absolute pointer only and
   sends no relative reports, so a hybrid mode is not planned.
8. **Descriptor cache hygiene:** each gadget profile has its own USB serial (`ihc-RA`, `ihc-A`,
   `ihc-AR`), with the same VID and PID (1d6b:0104). Record whether iOS treats a profile switch as a new
   device (a new Allow prompt, a new entry under Devices). If it reuses the old descriptor, the PID must
   change too, as Aiden does; `ihcd gadget up` has no option for that today. Otherwise the result is not
   trustworthy (mirrordeck's lesson).
9. **AssistiveTouch Devices:** which entry or entries appear (two pointer interfaces in `RA`?). The box
   sends buttons 2 and 3 through the absolute report: `ihcd hid click secondary` (Home) and
   `ihcd hid button app_switcher` show whether the mapping applies (hardware check 5 and 6).
10. Optional: rotate to landscape, set the landscape layout (`--landscape` or the console's switch),
    and tap 3 points (informational only; see §5).

Pass: the hardware check passes, taps land on their targets, the settle time is known, 0 stuck
presses, and the results are the same at both Tracking Speed values. Afterwards, restore the default
gadget with `sudo systemctl restart ihcd-gadget` and start the service (`sudo systemctl start ihcd`).

### 7.3 The gadget descriptor (`box/internal/hid/descriptors.go`)
1. **Keep `DescAbsolute` as it is.** Mouse > Pointer > Physical, 3 buttons, X/Y 0..32767 `Abs`,
   relative wheel is structurally what works over USB (Aiden, JetKVM) and over Bluetooth (mirrordeck).
   - Only if a phone shows positions but ignores clicks (hardware check 3), add a variant with the
     buttons at application level and the Physical collection around X/Y only (the layout of forum
     770639 and mirrordeck), as a new profile with its own serial.
   - Physical ranges, units and more buttons are not needed.
2. **Do not add a digitizer (0x0D) collection to any profile.**
   - If anyone wants to close the question, build a **separate** experimental gadget with its own
     serial and PID that exposes only a single-touch Touch Screen collection.
   - Test it with AssistiveTouch on and off. The expected result is ignored input or 0,0 taps.
   - It is the lowest priority, and must never ship next to the mouse.
3. **Never emit a zero absolute report.** `ihcd` repeats the last position it sent on every press and
   release. One gap: a new engine (the service after a start, or each `ihcd hid` command) has sent no
   position yet, and its release report then carries X=Y=0. So a first action without a move
   (`ihcd hid key`, `type`, `button`, `click`) moves the pointer to the top-left corner, and `click`
   presses there. A release with no known position should go to a neutral point such as the centre,
   never to (0,0). The same applies to any keep-alive or "neutral" report.
4. **Descriptor identity.** Every profile has its own serial (`ihc-<profile>`). Any future descriptor
   variant needs its own serial as well (and a new PID if §7.2 check 8 shows iOS ignores the serial).
5. **USB interface order.** `RA` is keyboard → consumer → relative mouse → absolute pointer, and `A` is
   keyboard → consumer → absolute pointer. The pointer is last, as Aiden found necessary for
   soft-keyboard stability after re-enumeration (`AR` puts the relative mouse last instead). Keep any
   new interface ahead of the pointer. `RA` already uses the four HID functions the kernel allows
   (`HIDG_MINORS` in f_hid), so a new interface fits only next to `A`.
6. **Host defaults.** The box waits 80 ms after a jump before a press (`--settle`; Aiden's value,
   glassbox found 250 ms safe), holds a tap 80 ms, sends the release three times 15 ms apart at the
   same X/Y, and sends one report per jump. If taps turn into short drags on some phone or iOS version,
   raise `--settle` first (§7.2 check 3).

---

## 8. Remaining unknowns, ranked
1. iOS 27.0 behaviour after the backboardd refactor (the hardware check and §7.2 on 27.0).
2. Settle time and release reliability on the box's hub (§7.2 checks 3–4). The default settle, 80 ms,
   is Aiden's value; no measurement on the box is recorded.
3. Whether Tracking Speed or Sensitivity affect absolute mode (check 6).
4. Whether the absolute axes turn with the screen in landscape (§5, check 10).
5. CH9329's own absolute descriptor and whether iOS accepts it. This is a market question only; the
   project has no CH9329 fallback.

The board question that led the earlier list, whether the USB-C port runs the gadget at all, is settled
for the Orange Pi 5 Plus: the box's gadget runs there and an iPhone 15 follows its absolute pointer.
`ihcd doctor` checks the port on each board.
