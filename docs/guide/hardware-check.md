# Hardware check

Every touch and button of the box, tried on the real iPhone. Run it on a new box, after an iOS
update, and when a button stops working. It takes about ten minutes.

Before starting: the phone set up as in [iPhone setup](iphone-setup.md), plugged in, unlocked, on
its home screen. Stop the service while using `ihcd hid` (`sudo systemctl stop ihcd`), and start it
again for the console part (`sudo systemctl start ihcd`).

## On the board

| # | Check | Command | Expected |
|---|---|---|---|
| 1 | USB link | `ihcd gadget status` | `"state": "configured"` |
| 2 | Absolute pointer | `ihcd hid corners` | The pointer visits the four corners, then the centre |
| 3 | Tap | `ihcd hid tap 0.5 0.95` | The app under the point opens (the dock) |
| 4 | Home | `ihcd hid button home` | Back to the home screen (keyboard shortcut Cmd+H) |
| 5 | Home, second way | `ihcd hid click secondary` | Home, if AssistiveTouch maps the secondary button to Home |
| 6 | App Switcher | `ihcd hid button app_switcher` | The app switcher opens (middle button mapped to App Switcher) |
| 7 | Search | `ihcd hid button spotlight` | Search opens (Cmd+Space) |
| 8 | Typing | `ihcd hid type "Hello 123!"` with Search open | `Hello 123!` in the search field |
| 9 | Volume | `ihcd hid button volume_up`, then `volume_down` | The volume indicator moves up, then down |
| 10 | Mute | `ihcd hid button mute` | The volume indicator goes to zero |
| 11 | Media | `ihcd hid button play_pause` with music playing | The music pauses |
| 12 | Video | `ihcd check` | The HDMI input receives 1920x1080 at about 60 Hz |

If 4 fails and 5 works, start the service with `IHCD_ARGS="--home button"` in `/etc/default/ihc`.

## In the console

| # | Check | How | Expected |
|---|---|---|---|
| 13 | Picture | Open `http://<board>:8000` | The live screen; the top bar shows about 60 fps |
| 14 | Hover | Move the mouse over the screen | The phone's pointer follows without trailing |
| 15 | Swipe | Drag the home screen sideways | The next page follows the hand and snaps |
| 16 | Scroll | Open Settings, drag the list, then use the wheel | The list follows the drag and keeps its speed when released; the wheel scrolls |
| 17 | Drag and drop | Hold an app icon until it lifts, drag it to another place | The icon moves there |
| 18 | Keyboard | Click the screen, open Notes, type | The text appears as typed; Cmd/Ctrl+V types the clipboard |
| 19 | Buttons | The volume keys and the side button of the drawn phone, Home, App Switcher, Search | Each does what its name says |
| 20 | Sleep and wake | Let the phone lock (or press its side button), wait until the console says Asleep, press Wake | The screen lights up, the console says Ready |
| 21 | Latency | Read the top bar while moving the mouse | Touch under 1 ms; picture age and round trip |

## Report

Note the iPhone model, the iOS version, the box version (`ihcd version`) and each failing number
with what happened instead.
