# ADR 0001: Languages for the product

- **Status:** accepted, 2026-09-25; the box daemon part is superseded by [ADR 0002](0002-box-in-go.md)
- **Scope:** which language each part of iphone-hid is written in, now and for the purpose-built box

## Context

Today one Python package does everything:

- the box server (FastAPI, web console, WebSocket);
- the pointer model and calibration;
- the HID backends (Linux USB gadget, CH9329);
- video (OpenCV, V4L2 through ctypes);
- the simulator, the lab tools and the SDK.

It runs on an Orange Pi 5 Plus (RK3588, 4–16 GB RAM) from a 75 MB self-contained bundle: CPython plus
ARM64 wheels. That is fine on that board.

The product is the purpose-built box of [custom-box.md](../../research/custom-box.md):

| Part | Chip | Resources |
|---|---|---|
| SoC | RV1106G3 | one Cortex-A7 at 1.2 GHz, 256 MB RAM in the package |
| Video bridge | LT7911D | |
| Keyboard and mouse | CH32V305 MCU | |
| Video encoder | hardware H.264, low-delay | |

It must boot fast, stay up 24/7, update over the air, and keep the latency from the iPhone's screen to the
viewer's screen (glass to glass) within 35–60 ms.

## Decision

| Part | Language | Why |
|---|---|---|
| On-box daemon: API, WebSocket, WebRTC, mDNS, OTA, configuration, pointer model, health | **Go** | See below. |
| Media plane: V4L2 capture, RGA crop, MPP/Rockit H.264 encoder | **C**, a small library the Go daemon calls through cgo | The vendor SDKs are C. Frames stay in DMA-buf from capture through crop to the encoder, with no copies. |
| Keyboard/mouse MCU firmware (CH32V305) | **C** (WCH SDK + TinyUSB) | Vendor support, deterministic timing, no runtime. |
| SDK for test frameworks | **Python** (primary) | Test and QA teams script in Python. Clients in other languages come later, generated from the OpenAPI contract. |
| Web console | **TypeScript**, built to static files that the daemon embeds (`go:embed`) | Types for a growing UI. Today's plain JavaScript moves over gradually. |
| Simulator, lab tools, calibration research | **Python** (stays) | Fast iteration. Not shipped on the product box. |

Why Go for the daemon:

- **Deployment:** one static binary, and one command cross-compiles it for ARMv7 or ARM64.
- **Footprint:** about 20–40 MB RSS, and it starts in a fraction of a second.
- **Concurrency:** goroutines handle many sockets.
- **WebRTC:** pion is a mature WebRTC stack written in pure Go.
- **Proven:** JetKVM runs this same stack on the same RV1106G3.

## Rejected

- **Python on the product box.**
  - CPython with FastAPI, NumPy and OpenCV takes about 100 MB of disk and 60–100 MB of RAM.
  - It needs several seconds to start on one A7 core.
  - The GIL makes the video loop compete with HTTP.
  - Frames handled in Python are copied.

  It is right for the RK3588 board, not for the RV1106.
- **Rust.**
  - It has the best raw efficiency and no garbage collector.
  - But its WebRTC stack and its Rockchip bindings are less mature than pion and cgo, and it is slower to
    write.
  - The hot paths run in hardware, C or the MCU: encoding, and the keyboard/mouse timing. So Go's
    sub-millisecond GC pauses are not the bottleneck.
- **C++ for the whole daemon.** It is fast, but the HTTP, WebRTC and JSON code costs much more code and
  risk.

## Consequences

1. **The API is the product boundary.** It is the HTTP/WebSocket contract, which the Python server
   describes as OpenAPI today. The Go daemon must pass the same API tests (contract tests) before it
   replaces the Python server anywhere.
2. **No big-bang rewrite.** The Python server stays the reference implementation, and the software of the
   Orange Pi box, until the Go daemon reaches parity.
3. **Order of work:**
   1. MCU firmware in C, plus a Python `mcu` backend (stage 1 of the custom-box roadmap).
   2. The Go daemon, on the RV1106 platform (JetKVM hardware).
   3. The console, moved to TypeScript.
4. **Repository layout.**
   - `src/ihc` stays the Python package: SDK, reference server, simulator and lab tools.
   - `firmware/` (C) and `box/` (Go) appear at the top level when their code starts.
