# iPhone setup

Once per phone. Menu names may differ slightly between iOS versions.

## Required

1. **Settings > Accessibility > Touch > AssistiveTouch: on.** iOS shows the pointer only with
   AssistiveTouch. Turn off **Always Show Menu** if the floating button covers content.
2. **Settings > Accessibility > Pointer Control:**
   - **Automatically Hide Pointer: off**;
   - **Pointer Size** about two thirds, and a visible colour, so the pointer shows in the console.
3. **AssistiveTouch > Devices > iphone-hid touch + keyboard > Customize Additional Buttons:**
   - middle button (Button 3) = **App Switcher**;
   - secondary button (Button 2) = **Home** (Home also works through Cmd+H; this makes a right
     click useful too).

   The device appears in the list once the box has been plugged in. iOS may list it more than once
   (one entry per pointer interface): set the buttons on each.
4. **Settings > Display & Brightness > Auto-Lock: Never.** A sleeping iPhone suspends the USB bus
   and stops taking input, and its video output stops. If Never is missing, **Low Power Mode** is
   on (it forces 30 seconds) or a management profile sets a limit.
5. **Settings > Privacy & Security > Wired Accessories:** **Always Allow** for a dedicated phone, so
   the box keeps working after the phone locks or restarts. Check it again after iOS updates.
6. **Settings > General > Keyboard > Hardware Keyboard:** layout **U.S.**; turn off
   Auto-Capitalization, Auto-Correction and the "." shortcut, so typed text arrives as sent.

## Recommended

- **Passcode off** on a phone dedicated to automation, where its apps allow it: USB Restricted Mode
  and the restart after 72 hours locked then do not apply. With a passcode, keep Wired Accessories
  on Always Allow.
- **Notifications off** or a Focus mode, so banners do not cover the screen.
- **Automatic iOS updates off**, so the phone does not restart in the middle of a run.
- **Optimized charging** on: the phone stays on the charger all day.
- Screen brightness low: a bright static picture for months can mark an OLED screen.
- **AssistiveTouch Dwell off and Zoom off** (Settings > Accessibility > Zoom): Dwell clicks when the
  pointer rests, and Zoom moves the picture under the pointer, so touches would land elsewhere.

## Check

With the box installed and the phone plugged in, open the console: the phone's screen appears, the
pointer follows the mouse over it, and a click opens what it lands on. The
[hardware check](hardware-check.md) tries every button.
