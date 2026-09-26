# ADR 0002: The box software is Go on every box

- **Status:** accepted, 2026-09-26
- **Supersedes:** the part of [ADR 0001](0001-languages.md) that kept the Python server on the Orange
  Pi box until a Go daemon reached parity

## Context

The Orange Pi 5 Plus box ran a Python server: FastAPI, OpenCV, the pointer model and the HID
backends in one process. Touch from the console went through the event loop, a per-connection
queue, a worker ticking every 16 ms and a thread pool before reaching `/dev/hidg`, and drags in the
console were sent as one swipe after the mouse was released. The iPhone follows the absolute
pointer, so the relative-pointer model and its calibration no longer carry their weight.

## Decision

- `ihcd` (Go, `box/`) is the box software on the Orange Pi box now, and on the purpose-built box
  later. It holds the touch engine, the USB gadget, the video capture and JPEG encoding, the API and
  the console, in one static binary.
- Touch is the absolute pointer only. Live input is streamed per pointer event over a binary
  WebSocket protocol and sent by one goroutine as soon as the phone can take it.
- JPEG encoding is libjpeg-turbo through cgo; the binary is linked statically against musl with
  `zig cc`, so one file runs on any ARM64 Linux image.
- Python keeps the SDK and the `ihc` command for test frameworks. Its tests run against
  `ihcd serve --sim`, so the SDK and the box are checked together.

## Consequences

- The relative pointer, the Safari calibration, the CH9329 backend and the Python simulator are
  gone. The API keeps its shape (`/api/devices/{id}/tap` and so on), without the calibration
  endpoints and the `space` parameter.
- The bundle shrinks from 72 MB to 3.4 MB and starts in milliseconds.
- The purpose-built box (RV1106) runs the same binary; its MCU firmware (C) comes next.
