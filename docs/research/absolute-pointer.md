# Absolute positioning on iPhone over USB: research notes

- **Date:** 2026-09-24. This builds on `docs/feasibility.md` (A1, section 3.1) and does not repeat it.
- **Updated 2026-09-25** for the project's current scope: USB-C iPhones only, one Orange Pi 5 Plus box per
  phone whose USB-C port runs as a Linux USB gadget (the phone's keyboard and mouse), with a CH9329 cable as the
  fallback. The project no longer uses Bluetooth. Bluetooth findings stay in §2 only as evidence of how iOS treats
  absolute pointers. Test IDs (B1–B8, D1–D3) refer to `docs/phase0-checklist.md`.
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
| V3 | Does the pointer jump straight to the target? | **[Likely]** no. It glides there quickly, so a click must wait 80–250 ms. | Aiden: 80 ms "iOS cursor animation". glassbox: settle of at least 250 ms, "proved". mirrordeck: iOS queues reports and replays the path (§5). |
| V4 | Absolute over **Bluetooth**? (out of scope; iOS evidence only) | **[Likely]** works on iOS 18–26 over both Classic and BLE. BLE is the weaker case (snippet only). The 2020 "does not move" report was a flawed test. | mirrordeck (Classic, iPhone 15 Pro, iOS 26.5). Forum 770639 (Classic, iPad, 2024). AScript's BLE firmware "abs mode recommended for iOS 18+" (snippet) (§2). |
| V5 | Are **digitizers** (touch screen 0x0D/0x04, pen, touchpad, precision touchpad) treated as touches or as a pointer? | **[Contradicted]** as a way in. Since iOS 13.4 they are ignored or blocked. Adding a Touch Screen collection next to the mouse **breaks mouse clicks**. | iOS 13.4 blocked BLE touch-screen devices. Aiden: "iOS do not automatically convert Digitizer input". glassbox: "digitizer/touchpad … ignored by iOS". mirrordeck: a digitizer made iOS stop honouring mouse buttons (§3). |
| V6 | iPhone vs iPad | **[Confirmed]** they differ. iPad drives its native pointer from an absolute mouse without AssistiveTouch. An accessory trackpad using the Apple descriptor is still ignored on iPad without MFi. | glassbox (iPad mini 7), PiKVM #1202, ADG §15, glassbox's trackpad experiment (§1, §3) |
| V7 | Does the **CH9329** `0x04` absolute command work on iPhone? (fallback only) | **[Unknown]**. There are no reports either way. The chip's single mouse interface carries relative (ID 1) and absolute (ID 2) reports; that layout has not been tested on iOS. | A seller or blog line says CH9329 absolute works "only on Windows" (snippet). A composite of absolute and relative works over USB (separate interfaces) and over Bluetooth (one map) (§4). |
| V8 | What does the descriptor need? | **[Likely]**: Mouse, then Pointer, then Physical collection, with buttons and 16-bit X/Y `Abs` over 0..32767. Report ID and physical range are optional. A relative mouse may coexist. **No digitizer collection.** Always send the real position. | Comparison of four working descriptors (§4) |
| V9 | iOS 26 and iOS 27 changes | iOS 26.x: absolute works (V1). **[Unknown]** for iOS 27. Its backboardd pointer and digitizer code was refactored and iPhone gained new "Pointers" settings strings. Re-run B4 and B5 on 27.0. | blacktop/ipsw-diffs 26.5 vs 27.0 (iPhone18,1) (§6) |

**Consequence for the design:** keep absolute as the primary mode. The box's USB gadget
(`ihc/hid/gadget.py`) already exposes an absolute pointer in the layout that works (§4): its own
interface, no report ID, 0..32767, pointer interface last. Test it first (B4). Do not add a digitizer
collection to any production descriptor.

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
  The box works the same way (`ihc/hid/gadget.py`).
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

| Aspect | Aiden USB (iPhone) | JetKVM / PicoKVM USB (iPhone 17 PM) | mirrordeck Classic (iPhone 15 Pro) | Ours: gadget `DESC_ABS_POINTER` |
|---|---|---|---|---|
| Collections | Mouse > Pointer > Physical | Mouse > [ID 1] Pointer > Physical | Mouse [ID 2], buttons at application level, Pointer > Physical around X/Y only | Mouse > Pointer > Physical |
| Report ID | none (own interface) | 1 (wheel is ID 2 in the same collection) | 2 (keyboard ID 1, relative mouse ID 3 in the same map) | none (own interface) |
| Buttons | 8 | 5 + pad | 2 + pad | 3 + pad |
| X/Y | 16-bit, 0..32767, Abs | 16-bit, 0..32767, physical 0..32767 | 16-bit, 0..32767 | 16-bit, 0..32767 |
| Wheel | relative, in the collection | separate report ID | none (uses the relative report) | relative, in the collection |
| Relative mouse in the same device | no | yes (separate interface) | yes (same map) | yes in profile `RA` (the default, separate interface); none in profile `A` |

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
     one mouse interface with IDs 1 and 2. Nobody has dumped the CH9329 report descriptor publicly (D1).
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
  - Aiden waits 80 ms; glassbox proved 250 ms is safe.
  - backboardd has a `BKMousePointerAnimationDriver`
    ([ipsw-diffs backboardd](https://github.com/blacktop/ipsw-diffs/blob/HEAD/26_5_23F77_vs_27_0_24A5355q/MACHOS/filesystem/usr/libexec/backboardd.md)).
  - Too many reports **queue up and iOS replays them** after the source stops (mirrordeck). Send one
    target report (glassbox sends it twice), not a stream.
- **Every report is a position.** [Likely] A report with X=Y=0 moves the pointer to the top-left
  (mirrordeck; the same thing happened on PiKVM's Linux target,
  [#1437](https://github.com/pikvm/pikvm/issues/1437)).
  - Press and release must repeat the current X/Y. The host already does this: `PointerModel.release_all`.
  - Keep-alives and initial values must never be (0,0) (§7.3).
- **Release reliability.** [Likely] Send the release 2–3 times at the same position and hold the press
  for at least 60–100 ms (Aiden, glassbox). There is an iPad report of a stuck release (770639).
- **AssistiveTouch.**
  - [Confirmed] It is required on iPhone.
  - [Unknown] whether iOS 26 on iPhone shows a native **arrow** pointer without AssistiveTouch that
    moves but does not click. AbilityNet's iOS 26 guide describes an arrow with a red border when
    AssistiveTouch is off
    ([AbilityNet](https://mcmw.abilitynet.org.uk/how-to-use-a-mouse-or-trackpad-with-your-iphone-or-ipad-in-ios-26), snippet).
    AScript says the pointer moves without AssistiveTouch but clicks fail (snippet). mirrordeck saw
    the arrow plus ignored buttons when the device was reclassified.
  - **If B4 sees an arrow pointer, the setup is wrong. A round dot is expected.**
- **Tracking Speed / Sensitivity.** [Unknown] whether they affect absolute mode (glassbox pins them
  anyway). B5 checks at two slider values.
- **Pointer Control, Dwell, Hot Corners, Zoom.** As in feasibility §3.1. Also keep Eye/Head Tracking and
  any "Snap to Item" option off. Snap to Item moves the pointer to the nearest element (eye tracking,
  snippet) and would corrupt absolute taps.
- **Landscape.** [Unknown] for absolute. The relative path has a landscape axis bug (forum 786963) and
  jailbroken HID injection uses a fixed-orientation space
  ([ios-mcp #14](https://github.com/witchan/ios-mcp/issues/14)). Stay portrait-only.
- **"Devices" page.** Button remapping lives under *AssistiveTouch > Devices > [device]*. Each USB
  identity is its own entry. With separate interfaces, iOS may list the relative and absolute
  interfaces separately; check which one gets the Home/App Switcher mapping (B4).

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
  - **[Unknown]** what changes for users. This is a refactor plus possibly new settings, so **B4 and B5
    must run on 27.0**, as feasibility R10 already says.
- **[Likely]** Earlier regressions: iOS/iPadOS 18.4.1 degraded external HID input (lag, dropped
  modifiers) on iPhone 16 Pro Max and iPads
  ([forum 781674](https://developer.apple.com/forums/thread/781674)). Pin iOS versions in the farm.

---

## 7. Recommendations

### 7.1 D1 (CH9329 fallback, Linux PC, before the iPhone)
1. Dump the CH9329 descriptors in mode 0x00 and 0x02 (`lsusb -v -d 1a86:`,
   `usbhid-dump -d 1a86 -e descriptor`, decoded with `hid-decode` or hidrdd). Record:
   - where the absolute collection sits and its usages;
   - its report ID and logical maximum (4095 or 32767?);
   - whether it has buttons;
   - whether the mouse interface is **boot subclass 1 / protocol 2**. A host in boot protocol would
     drop report IDs and the absolute report. iOS is expected to use report protocol, but record it.
2. `evtest`: send `0x04` at (0,0), (2048,2048) and (4095,4095). Check ABS_X/Y and the range.
3. Save the dump to `docs/test-logs/`. It is the first public CH9329 descriptor and settles V7's
   descriptor half.

### 7.2 B4 and B5 (iPhone 15+ over the USB-C hub, iOS 26.x, then 27.0): runs and checks

Run the configurations in this order. **Always run B, even if A passes.**

| Run | HID | Why |
|---|---|---|
| A | Box gadget, profile `RA` (default): keyboard, extras, relative mouse, absolute pointer | What the box ships with. It matches the glassbox/JetKVM case (absolute next to a separate relative interface). |
| B | Box gadget, profile `A`: no relative mouse | The reference layout (own interface, no ID, 0..32767), as in Aiden. If B passes and A fails, the relative interface next to it is the problem, not iOS. |
| C | Fallback: CH9329 mode 0x00, `0x04` (D3) | Only if the box cannot run the gadget |
| D | Fallback: CH9329 mode 0x02 (mouse only, power-cycle) (D3) | Isolates the keyboard composite |

Checks for each run (`hidtest --gadget "abstest"` or `--port` first, then the calibration page, which
logs `pointerdown`/`click` with `clientX/Y` and `pointerType`):
1. The pointer is a **round dot** (AssistiveTouch). An arrow means reclassification or AssistiveTouch
   is off: stop and fix.
2. **Mapping:** 3×3 grid plus 4 near-corner points. Fit an affine map and report its residuals. Check
   where logical 0 and max land, including the status-bar and home-indicator zones, and whether they
   clamp.
3. **Settle time:** move, wait {0, 30, 60, 100, 150, 250} ms, then click, 10 times each. Record the
   first delay at which 100% land within 2 pt. The default until measured is 250 ms (glassbox);
   calibration measures it (`abs_settle`).
4. **Clicks through the absolute report:** 50 taps at one point with press ≥60 ms and release ×1, then
   release ×3. Count stuck presses. The page shows a drag in progress.
5. **Zero report:** send (0,0) with no buttons and confirm the pointer goes top-left. This confirms
   absolute semantics; Hot Corners must be off. Also note where the pointer sits right after the
   phone enumerates the device: it must not start at (0,0).
6. **Tracking Speed** at minimum and maximum: repeat 5 points and expect no change. Record either way.
7. **Mixing (A only):** a relative move after an absolute one. Does it continue from the absolute
   position, which would allow a hybrid mode?
8. **Descriptor cache hygiene:** each gadget profile has its own USB serial (`ihc-RA`, `ihc-A`...),
   with the same PID. Record whether iOS treats a profile switch as a new device (a new Allow prompt,
   a new entry under Devices). If it reuses the old descriptor, change the PID too
   (`ihc gadget up --pid`), as Aiden does. Otherwise the result is not trustworthy (mirrordeck's lesson).
9. **AssistiveTouch Devices:** which entry or entries appear (two pointer interfaces in `RA`?). Does
   the button 2/3 → Home/App Switcher mapping apply when buttons are sent through the absolute report
   (`abs X Y buttons=2`)?
10. Optional: rotate to landscape and test 3 points (informational only).

Pass as in `docs/phase0-checklist.md` (≤ 2 pt after the fit), plus: settle time known, 0 stuck releases
with release ×3, and results the same at both Tracking Speed values.

### 7.3 The gadget descriptor (`ihc/hid/gadget.py`)
1. **Keep `DESC_ABS_POINTER` as it is.** Mouse > Pointer > Physical, 3 buttons, X/Y 0..32767 `Abs`,
   relative wheel is structurally what works over USB (Aiden, JetKVM) and over Bluetooth (mirrordeck).
   - Only if B4 shows positions but ignored clicks, add a variant with the buttons at application
     level and the Physical collection around X/Y only (the layout of forum 770639 and mirrordeck),
     under its own serial.
   - Physical ranges, units and more buttons are not needed.
2. **Do not add a digitizer (0x0D) collection to any profile.**
   - If anyone wants to close the question, build a **separate** experimental gadget with its own
     serial and PID that exposes only a single-touch Touch Screen collection.
   - Test it with AssistiveTouch on and off. The expected result is ignored input or 0,0 taps.
   - It is the lowest priority, and must never ship next to the mouse.
3. **Never emit a zero absolute report.** The host already repeats the current X/Y on press and
   release, and never sends (0,0) by accident. Keep it that way for any keep-alive or "neutral" report.
4. **Descriptor identity.** Every profile already has its own serial. Any future descriptor variant
   needs its own serial as well (and a new PID if B4 check 8 shows iOS ignores the serial).
5. **USB interface order** is already keyboard → consumer → system → relative mouse → absolute pointer,
   with the pointer last, as Aiden found necessary for soft-keyboard stability after re-enumeration.
   Keep any new interface ahead of the pointer.
6. **Host defaults.** Use settle 250 ms until calibration measures it, press ≥60–100 ms, release ×3 at
   the same X/Y, and one target report per move (no stream) to avoid iOS replaying a backlog.

---

## 8. Remaining unknowns, ranked
1. Whether the box's USB-C port runs the gadget at all (B1, B2). This is a board question, not an iOS
   one, but everything above depends on it.
2. iOS 27.0 behaviour after the backboardd refactor (B4 and B5 on 27.0).
3. Settle time and release reliability on our hub (B4/B5 checks 3–4).
4. Whether Tracking Speed or Sensitivity affect absolute mode (check 6).
5. CH9329's own absolute descriptor and whether iOS accepts it (fallback only: D1 dump, then D3).
