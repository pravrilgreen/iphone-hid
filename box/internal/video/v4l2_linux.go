package video

import (
	"encoding/binary"
	"fmt"
	"runtime"
	"strings"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/unix"
)

// ioctl numbers and structures follow linux/videodev2.h for 64-bit kernels (aarch64, x86_64); the
// tests pin each structure's size to the kernel's ioctl number.

func ioc(dir, nr, size uintptr) uintptr { return dir<<30 | size<<16 | 'V'<<8 | nr }

const (
	iocWrite = 1
	iocRead  = 2
)

type v4l2Capability struct {
	Driver       [16]byte
	Card         [32]byte
	BusInfo      [32]byte
	Version      uint32
	Capabilities uint32
	DeviceCaps   uint32
	Reserved     [3]uint32
}

type v4l2Format struct {
	Type uint32
	_    uint32
	Fmt  [200]byte // union: pix / pix_mp
}

type v4l2RequestBuffers struct {
	Count        uint32
	Type         uint32
	Memory       uint32
	Capabilities uint32
	Flags        uint8
	Reserved     [3]uint8
}

type v4l2Buffer struct {
	Index     uint32
	Type      uint32
	BytesUsed uint32
	Flags     uint32
	Field     uint32
	_         uint32
	Timestamp [2]int64
	Timecode  [16]byte
	Sequence  uint32
	Memory    uint32
	M         uint64 // offset (single-planar) or pointer to the planes (multi-planar)
	Length    uint32
	Reserved2 uint32
	RequestFD int32
	_         uint32
}

type v4l2Plane struct {
	BytesUsed  uint32
	Length     uint32
	M          uint64 // mem_offset in the low 32 bits
	DataOffset uint32
	Reserved   [11]uint32
}

type v4l2DVTimings struct { // packed: 4 + 128
	Type uint32
	BT   [128]byte
}

type v4l2EDID struct {
	Pad        uint32
	StartBlock uint32
	Blocks     uint32
	Reserved   [5]uint32
	EDID       uintptr
}

var (
	vidiocQuerycap       = ioc(iocRead, 0, unsafe.Sizeof(v4l2Capability{}))
	vidiocGFmt           = ioc(iocRead|iocWrite, 4, unsafe.Sizeof(v4l2Format{}))
	vidiocSFmt           = ioc(iocRead|iocWrite, 5, unsafe.Sizeof(v4l2Format{}))
	vidiocReqbufs        = ioc(iocRead|iocWrite, 8, unsafe.Sizeof(v4l2RequestBuffers{}))
	vidiocQuerybuf       = ioc(iocRead|iocWrite, 9, unsafe.Sizeof(v4l2Buffer{}))
	vidiocQbuf           = ioc(iocRead|iocWrite, 15, unsafe.Sizeof(v4l2Buffer{}))
	vidiocDqbuf          = ioc(iocRead|iocWrite, 17, unsafe.Sizeof(v4l2Buffer{}))
	vidiocStreamon       = ioc(iocWrite, 18, 4)
	vidiocStreamoff      = ioc(iocWrite, 19, 4)
	vidiocSEDID          = ioc(iocRead|iocWrite, 41, unsafe.Sizeof(v4l2EDID{}))
	vidiocSDVTimings     = ioc(iocRead|iocWrite, 87, unsafe.Sizeof(v4l2DVTimings{}))
	vidiocQueryDVTimings = ioc(iocRead, 99, unsafe.Sizeof(v4l2DVTimings{}))
)

const (
	bufTypeCapture       = 1
	bufTypeCaptureMplane = 9
	memoryMmap           = 1
	capVideoCapture      = 0x00000001
	capVideoCaptureMp    = 0x00001000
	capStreaming         = 0x04000000
	capDeviceCaps        = 0x80000000
	quantFullRange       = 1
)

func ioctl(fd int, req uintptr, arg unsafe.Pointer) error {
	for {
		_, _, e := unix.Syscall(unix.SYS_IOCTL, uintptr(fd), req, uintptr(arg))
		if e == unix.EINTR {
			continue
		}
		if e != 0 {
			return e
		}
		return nil
	}
}

func fourcc(s string) uint32 { return binary.LittleEndian.Uint32([]byte(s)) }

func fourccString(v uint32) string {
	b := make([]byte, 4)
	binary.LittleEndian.PutUint32(b, v)
	return string(b)
}

// multiBuffer: the same layouts with each plane in a buffer of its own.
var multiBuffer = map[string]string{"NM12": "NV12", "NM21": "NV21", "NM16": "NV16", "NM61": "NV61"}

// Signal errors of an HDMI receiver with nothing usable to lock on.
var noSignal = map[syscall.Errno]string{
	unix.ENOLINK: "no signal: nothing plugged in, or the phone sends no picture",
	unix.ENOLCK:  "the signal is not stable yet",
	unix.ERANGE:  "the signal is out of the receiver's range",
}

// Timings are the video timings the receiver detected.
type Timings struct {
	Width, Height int
	Interlaced    bool
	PixelClock    uint64
	FPS           float64
}

func (t Timings) String() string {
	scan := "p"
	if t.Interlaced {
		scan = "i"
	}
	return fmt.Sprintf("%dx%d%s%.2f (pixel clock %.2f MHz)", t.Width, t.Height, scan, t.FPS, float64(t.PixelClock)/1e6)
}

func parseTimings(dv *v4l2DVTimings) Timings {
	b := dv.BT[:]
	u32 := func(off int) uint32 { return binary.LittleEndian.Uint32(b[off:]) }
	t := Timings{Width: int(u32(0)), Height: int(u32(4)), Interlaced: u32(8) != 0,
		PixelClock: binary.LittleEndian.Uint64(b[16:])}
	htotal := uint64(u32(0) + u32(24) + u32(28) + u32(32))
	vtotal := uint64(u32(4) + u32(36) + u32(40) + u32(44))
	if htotal*vtotal > 0 {
		t.FPS = float64(t.PixelClock) / float64(htotal*vtotal)
	}
	return t
}

// QuerySignal asks an HDMI receiver what it gets. err wraps ErrNoSignal when there is no usable
// signal, and ENOTTY-like errors for a node that is not an HDMI receiver.
func QuerySignal(device string) (Timings, error) {
	fd, err := unix.Open(device, unix.O_RDWR|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return Timings{}, err
	}
	defer unix.Close(fd)
	var dv v4l2DVTimings
	if err := ioctl(fd, vidiocQueryDVTimings, unsafe.Pointer(&dv)); err != nil {
		if msg, ok := noSignal[err.(syscall.Errno)]; ok {
			return Timings{}, fmt.Errorf("%w: %s", ErrNoSignal, msg)
		}
		return Timings{}, err
	}
	return parseTimings(&dv), nil
}

// SetEDID writes an EDID to an HDMI input; the source sees the display unplugged and plugged again.
func SetEDID(device string, edid []byte) error {
	fd, err := unix.Open(device, unix.O_RDWR|unix.O_CLOEXEC, 0)
	if err != nil {
		return err
	}
	defer unix.Close(fd)
	buf := append([]byte(nil), edid...)
	req := v4l2EDID{Blocks: uint32(len(buf) / 128), EDID: uintptr(unsafe.Pointer(&buf[0]))}
	err = ioctl(fd, vidiocSEDID, unsafe.Pointer(&req))
	runtime.KeepAlive(buf)
	return err
}

// Device is an open V4L2 capture node streaming into mapped buffers.
type Device struct {
	Path    string
	Driver  string
	Card    string
	Format  string // fourcc as delivered
	W, H    int
	Stride  []int
	Full    bool // YCbCr in full range
	Timings *Timings

	fd     int
	typ    uint32
	mplane bool
	maps   [][][]byte // buffer -> planes
}

// OpenDevice opens a capture node, locks the HDMI timings it detects, picks a pixel format and
// starts streaming into `buffers` mapped buffers.
func OpenDevice(path string, buffers int) (*Device, error) {
	fd, err := unix.Open(path, unix.O_RDWR|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	d := &Device{Path: path, fd: fd}
	if err := d.start(buffers); err != nil {
		d.Close()
		return nil, err
	}
	return d, nil
}

func (d *Device) start(count int) error {
	var c v4l2Capability
	if err := ioctl(d.fd, vidiocQuerycap, unsafe.Pointer(&c)); err != nil {
		return fmt.Errorf("%s: not a V4L2 device: %w", d.Path, err)
	}
	caps := c.Capabilities
	if caps&capDeviceCaps != 0 {
		caps = c.DeviceCaps
	}
	switch {
	case caps&capVideoCaptureMp != 0:
		d.typ, d.mplane = bufTypeCaptureMplane, true
	case caps&capVideoCapture != 0:
		d.typ = bufTypeCapture
	default:
		return fmt.Errorf("%s is not a video capture device", d.Path)
	}
	if caps&capStreaming == 0 {
		return fmt.Errorf("%s does not stream", d.Path)
	}
	d.Driver = cstr(c.Driver[:])
	d.Card = cstr(c.Card[:])

	var dv v4l2DVTimings
	if err := ioctl(d.fd, vidiocQueryDVTimings, unsafe.Pointer(&dv)); err == nil {
		t := parseTimings(&dv)
		d.Timings = &t
		_ = ioctl(d.fd, vidiocSDVTimings, unsafe.Pointer(&dv)) // some drivers follow the source themselves
	} else if msg, ok := noSignal[err.(syscall.Errno)]; ok {
		return fmt.Errorf("%w: %s", ErrNoSignal, msg)
	} // otherwise not an HDMI receiver (a USB capture card): nothing to lock

	if err := d.chooseFormat(); err != nil {
		return err
	}
	req := v4l2RequestBuffers{Count: uint32(count), Type: d.typ, Memory: memoryMmap}
	if err := ioctl(d.fd, vidiocReqbufs, unsafe.Pointer(&req)); err != nil {
		return fmt.Errorf("%s: cannot get buffers: %w", d.Path, err)
	}
	if req.Count < 2 {
		return fmt.Errorf("%s: the driver gave %d buffer(s)", d.Path, req.Count)
	}
	for i := uint32(0); i < req.Count; i++ {
		buf, planes := d.buffer(i)
		if err := ioctl(d.fd, vidiocQuerybuf, unsafe.Pointer(buf)); err != nil {
			return err
		}
		var maps [][]byte
		if d.mplane {
			for p := uint32(0); p < buf.Length; p++ {
				m, err := unix.Mmap(d.fd, int64(uint32(planes[p].M)), int(planes[p].Length), unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
				if err != nil {
					return err
				}
				maps = append(maps, m)
			}
		} else {
			m, err := unix.Mmap(d.fd, int64(uint32(buf.M)), int(buf.Length), unix.PROT_READ|unix.PROT_WRITE, unix.MAP_SHARED)
			if err != nil {
				return err
			}
			maps = append(maps, m)
		}
		d.maps = append(d.maps, maps)
		if err := ioctl(d.fd, vidiocQbuf, unsafe.Pointer(buf)); err != nil {
			return err
		}
		runtime.KeepAlive(planes)
	}
	typ := d.typ
	if err := ioctl(d.fd, vidiocStreamon, unsafe.Pointer(&typ)); err != nil {
		return fmt.Errorf("%s: cannot start streaming: %w", d.Path, err)
	}
	return nil
}

func (d *Device) chooseFormat() error {
	f := v4l2Format{Type: d.typ}
	if err := ioctl(d.fd, vidiocGFmt, unsafe.Pointer(&f)); err != nil {
		return err
	}
	if fourccString(binary.LittleEndian.Uint32(f.Fmt[8:])) != Formats[0] {
		want := f
		binary.LittleEndian.PutUint32(want.Fmt[8:], fourcc(Formats[0]))
		_ = ioctl(d.fd, vidiocSFmt, unsafe.Pointer(&want)) // the receiver may offer only its own format
		if err := ioctl(d.fd, vidiocGFmt, unsafe.Pointer(&f)); err != nil {
			return err
		}
	}
	b := f.Fmt[:]
	u32 := func(off int) int { return int(binary.LittleEndian.Uint32(b[off:])) }
	d.W, d.H = u32(0), u32(4)
	d.Format = fourccString(uint32(u32(8)))
	if d.mplane {
		n := int(b[180])
		if n < 1 {
			n = 1
		}
		for i := 0; i < n; i++ {
			d.Stride = append(d.Stride, u32(20+20*i+4))
		}
		d.Full = b[183] == quantFullRange
	} else {
		d.Stride = []int{u32(16)}
		d.Full = u32(40) == quantFullRange
	}
	if base, ok := multiBuffer[d.Format]; ok {
		d.Format = base
	}
	for _, f := range Formats {
		if f == d.Format {
			return nil
		}
	}
	if d.Format == "MJPG" {
		return fmt.Errorf("%s delivers MJPEG; this box reads uncompressed HDMI input", d.Path)
	}
	return fmt.Errorf("%s delivers %s, which this box cannot convert", d.Path, d.Format)
}

func (d *Device) buffer(i uint32) (*v4l2Buffer, []v4l2Plane) {
	buf := &v4l2Buffer{Index: i, Type: d.typ, Memory: memoryMmap}
	if !d.mplane {
		return buf, nil
	}
	planes := make([]v4l2Plane, 8)
	buf.M = uint64(uintptr(unsafe.Pointer(&planes[0])))
	buf.Length = 8
	return buf, planes
}

// Read waits up to timeout for the next frame and passes it to use while the driver's buffer is
// held; the buffer goes back to the driver when use returns.
func (d *Device) Read(timeout time.Duration, use func(*Raw)) error {
	deadline := time.Now().Add(timeout)
	for {
		left := time.Until(deadline)
		if left <= 0 {
			return fmt.Errorf("%s: no frame in %v", d.Path, timeout)
		}
		fds := []unix.PollFd{{Fd: int32(d.fd), Events: unix.POLLIN | unix.POLLPRI}}
		n, err := unix.Poll(fds, int(left/time.Millisecond)+1)
		if err == unix.EINTR {
			continue
		}
		if err != nil {
			return err
		}
		if n == 0 {
			continue
		}
		if fds[0].Revents&(unix.POLLERR|unix.POLLHUP) != 0 {
			return fmt.Errorf("%s: the stream stopped (signal lost or format changed)", d.Path)
		}
		buf, planes := d.buffer(0)
		if err := ioctl(d.fd, vidiocDqbuf, unsafe.Pointer(buf)); err != nil {
			if err == unix.EAGAIN {
				continue
			}
			return fmt.Errorf("%s: %w", d.Path, err)
		}
		raw := &Raw{Format: d.Format, W: d.W, H: d.H, Stride: d.Stride, Full: d.Full}
		maps := d.maps[buf.Index]
		if d.mplane {
			for p := uint32(0); p < buf.Length && int(p) < len(maps); p++ {
				pl := planes[p]
				end := int(pl.BytesUsed)
				if end == 0 || end > len(maps[p]) {
					end = len(maps[p])
				}
				start := int(pl.DataOffset)
				if start > end {
					start = end
				}
				raw.Planes = append(raw.Planes, maps[p][start:end])
			}
		} else {
			end := int(buf.BytesUsed)
			if end == 0 || end > len(maps[0]) {
				end = len(maps[0])
			}
			raw.Planes = [][]byte{maps[0][:end]}
		}
		if len(raw.Stride) < len(raw.Planes) {
			for len(raw.Stride) < len(raw.Planes) {
				raw.Stride = append(raw.Stride, raw.Stride[len(raw.Stride)-1])
			}
		}
		use(raw)
		runtime.KeepAlive(planes)
		back, bplanes := d.buffer(buf.Index)
		if err := ioctl(d.fd, vidiocQbuf, unsafe.Pointer(back)); err != nil {
			return fmt.Errorf("%s: %w", d.Path, err)
		}
		runtime.KeepAlive(bplanes)
		return nil
	}
}

// Close stops streaming and releases the buffers.
func (d *Device) Close() error {
	if d.fd < 0 {
		return nil
	}
	typ := d.typ
	_ = ioctl(d.fd, vidiocStreamoff, unsafe.Pointer(&typ))
	for _, planes := range d.maps {
		for _, m := range planes {
			_ = unix.Munmap(m)
		}
	}
	d.maps = nil
	err := unix.Close(d.fd)
	d.fd = -1
	return err
}

func cstr(b []byte) string {
	if i := strings.IndexByte(string(b), 0); i >= 0 {
		b = b[:i]
	}
	return string(b)
}
