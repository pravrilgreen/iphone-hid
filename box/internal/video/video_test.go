package video

import (
	"bytes"
	"context"
	"encoding/hex"
	"image/jpeg"
	"sync"
	"testing"
	"time"
	"unsafe"
)

func TestIoctlNumbersMatchTheKernel(t *testing.T) {
	want := map[string][2]uintptr{
		"QUERYCAP":         {vidiocQuerycap, 0x80685600},
		"G_FMT":            {vidiocGFmt, 0xC0D05604},
		"S_FMT":            {vidiocSFmt, 0xC0D05605},
		"REQBUFS":          {vidiocReqbufs, 0xC0145608},
		"QUERYBUF":         {vidiocQuerybuf, 0xC0585609},
		"QBUF":             {vidiocQbuf, 0xC058560F},
		"DQBUF":            {vidiocDqbuf, 0xC0585611},
		"STREAMON":         {vidiocStreamon, 0x40045612},
		"STREAMOFF":        {vidiocStreamoff, 0x40045613},
		"S_EDID":           {vidiocSEDID, 0xC0285629},
		"S_DV_TIMINGS":     {vidiocSDVTimings, 0xC0845657},
		"QUERY_DV_TIMINGS": {vidiocQueryDVTimings, 0x80845663},
	}
	for name, v := range want {
		if v[0] != v[1] {
			t.Errorf("VIDIOC_%s = %#x, the kernel's is %#x", name, v[0], v[1])
		}
	}
	if unsafe.Sizeof(v4l2Plane{}) != 64 {
		t.Errorf("v4l2_plane is %d bytes, not 64", unsafe.Sizeof(v4l2Plane{}))
	}
}

func TestEDIDMatchesThePreviousBoxSoftware(t *testing.T) {
	// the EDID the box has always written: 1080p60 only, stereo LPCM
	want := "00ffffffffffff0025030100000000000124010380351e780eee91a3544c99260f505420000001010101010101010101010101010101023a801871382d40582c45000f282100001e000000fd00384b1e530f000a202020202020000000fc006970686f6e652d6869640a202000000010000000000000000000000000000001c8020313c123090407411065030c001000e2004a023a801871382d40582c45000f282100001e000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000c6"
	if got := hex.EncodeToString(EDID1080p60("iphone-hid")); got != want {
		t.Fatalf("EDID differs:\n got %s\nwant %s", got, want)
	}
}

func TestLayoutCentresThePhoneScreen(t *testing.T) {
	r := Layout{}.Rect(1920, 1080)
	if r.H != 1080 || r.W != 498 || r.X != 710 || r.Y != 0 {
		t.Fatalf("portrait %+v", r)
	}
	r = Layout{Landscape: true}.Rect(1920, 1080)
	if r.W != 1920 || r.H != 886 || r.Y != 96 {
		t.Fatalf("landscape %+v", r)
	}
	fixed := Rect{X: 1, Y: 2, W: 3, H: 4}
	if (Layout{Override: &fixed}).Rect(1920, 1080) != fixed {
		t.Fatal("override ignored")
	}
}

// nv12 builds a w x h NV12 frame with constant Y, U, V and a stride wider than the picture.
func nv12(w, h int, y, u, v byte) *Raw {
	stride := w + 32
	buf := make([]byte, stride*h+stride*h/2)
	for i := 0; i < stride*h; i++ {
		buf[i] = y
	}
	for i := stride * h; i < len(buf); i += 2 {
		buf[i], buf[i+1] = u, v
	}
	return &Raw{Format: "NV12", W: w, H: h, Planes: [][]byte{buf}, Stride: []int{stride}}
}

func TestCropExpandsVideoLevels(t *testing.T) {
	img, err := Crop(nv12(64, 32, 16, 128, 240), Rect{X: 9, Y: 3, W: 21, H: 11}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if img.W != 20 || img.H != 10 {
		t.Fatalf("crop %dx%d: must be aligned to the chroma grid (20x10)", img.W, img.H)
	}
	if img.Y[0] != 0 || img.Cb[0] != 128 || img.Cr[0] != 255 {
		t.Fatalf("levels: Y %d Cb %d Cr %d, want 0 128 255", img.Y[0], img.Cb[0], img.Cr[0])
	}
	white, _ := Crop(nv12(8, 8, 235, 128, 128), Rect{W: 8, H: 8}, nil)
	if white.Y[0] != 255 {
		t.Fatalf("video white %d, want 255", white.Y[0])
	}
	full := nv12(8, 8, 16, 128, 128)
	full.Full = true
	img, _ = Crop(full, Rect{W: 8, H: 8}, nil)
	if img.Y[0] != 16 {
		t.Fatal("full-range input must be left as it is")
	}
}

func TestCropReadsEveryLayout(t *testing.T) {
	w, h := 8, 4
	// NV21: V before U
	nv21 := nv12(w, h, 100, 10, 20)
	nv21.Format, nv21.Full = "NV21", true
	img, err := Crop(nv21, Rect{W: w, H: h}, nil)
	if err != nil || img.Cb[0] != 20 || img.Cr[0] != 10 {
		t.Fatalf("NV21: %v Cb %d Cr %d", err, img.Cb[0], img.Cr[0])
	}
	// NV16: chroma at full height, in a second buffer
	y := bytes.Repeat([]byte{50}, w*h)
	uv := bytes.Repeat([]byte{60, 70}, w*h/2)
	img, err = Crop(&Raw{Format: "NV16", W: w, H: h, Planes: [][]byte{y, uv}, Stride: []int{w, w}, Full: true}, Rect{W: w, H: h}, nil)
	if err != nil || img.Sub != Sub422 || len(img.Cb) != w/2*h || img.Cb[len(img.Cb)-1] != 60 || img.Cr[0] != 70 {
		t.Fatalf("NV16: %v %+v", err, img)
	}
	// NV24: a U and a V byte per pixel
	uv = bytes.Repeat([]byte{61, 71}, w*h)
	img, err = Crop(&Raw{Format: "NV24", W: w, H: h, Planes: [][]byte{append(y[:w*h:w*h], uv...)}, Stride: []int{w}, Full: true},
		Rect{X: 1, Y: 1, W: 3, H: 2}, nil)
	if err != nil || img.Sub != Sub444 || img.W != 3 || img.Cb[0] != 61 || img.Cr[2] != 71 {
		t.Fatalf("NV24: %v %+v", err, img)
	}
	// YUYV and UYVY
	yuyv := bytes.Repeat([]byte{11, 22, 33, 44}, w*h/2)
	img, err = Crop(&Raw{Format: "YUYV", W: w, H: h, Planes: [][]byte{yuyv}, Stride: []int{2 * w}, Full: true}, Rect{W: w, H: h}, nil)
	if err != nil || img.Y[0] != 11 || img.Y[1] != 33 || img.Cb[0] != 22 || img.Cr[0] != 44 {
		t.Fatalf("YUYV: %v %+v", err, img)
	}
	img, err = Crop(&Raw{Format: "UYVY", W: w, H: h, Planes: [][]byte{yuyv}, Stride: []int{2 * w}, Full: true}, Rect{W: w, H: h}, nil)
	if err != nil || img.Y[0] != 22 || img.Y[1] != 44 || img.Cb[0] != 11 || img.Cr[0] != 33 {
		t.Fatalf("UYVY: %v %+v", err, img)
	}
	// RGB3 becomes BGR
	rgb := bytes.Repeat([]byte{1, 2, 3}, w*h)
	img, err = Crop(&Raw{Format: "RGB3", W: w, H: h, Planes: [][]byte{rgb}, Stride: []int{3 * w}}, Rect{W: w, H: h}, nil)
	if err != nil || !bytes.Equal(img.BGR[:3], []byte{3, 2, 1}) {
		t.Fatalf("RGB3: %v", err)
	}
	if _, err := Crop(&Raw{Format: "MJPG", W: w, H: h}, Rect{W: w, H: h}, nil); err == nil {
		t.Fatal("MJPEG cannot be cropped")
	}
	short := nv12(64, 32, 1, 2, 3)
	short.Planes[0] = short.Planes[0][:100]
	if _, err := Crop(short, Rect{W: 64, H: 32}, nil); err == nil {
		t.Fatal("a short frame must be refused, not read past its end")
	}
}

func TestEncodeGivesAJPEGOfTheScreen(t *testing.T) {
	img, err := Crop(nv12(96, 64, 180, 90, 200), Rect{W: 96, H: 64}, nil)
	if err != nil {
		t.Fatal(err)
	}
	enc := NewEncoder()
	defer enc.Close()
	b, err := enc.Encode(img, 85)
	if err != nil {
		t.Fatal(err)
	}
	m, err := jpeg.Decode(bytes.NewReader(b))
	if err != nil {
		t.Fatal(err)
	}
	if m.Bounds().Dx() != 96 || m.Bounds().Dy() != 64 {
		t.Fatalf("decoded %v", m.Bounds())
	}
	r, g, bl, _ := m.At(40, 30).RGBA()
	// Y 180, Cb 90, Cr 200 in video range is a warm orange-pink
	if r>>8 < 200 || bl>>8 > 150 || g>>8 > 190 {
		t.Fatalf("colour %d %d %d", r>>8, g>>8, bl>>8)
	}
}

type fakeSource struct {
	frames int
	mu     sync.Mutex
	runs   int
}

func (s *fakeSource) Describe() string { return "fake" }

func (s *fakeSource) Run(ctx context.Context, emit func(*Raw)) error {
	s.mu.Lock()
	s.runs++
	s.mu.Unlock()
	for i := 0; i < s.frames && ctx.Err() == nil; i++ {
		emit(nv12(1920, 1080, byte(16+i), 128, 128))
		time.Sleep(5 * time.Millisecond)
	}
	return ErrNoSignal
}

func TestHubHandsOutTheNewestFrameAndRestartsTheSource(t *testing.T) {
	src := &fakeSource{frames: 3}
	hub := NewHub(src, Layout{})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go hub.Run(ctx)
	f, err := hub.Next(ctx, 0)
	if err != nil {
		t.Fatal(err)
	}
	if f.Image.W != 498 || f.Image.H != 1080 || f.Source.X != 710 || f.Source.W != f.Image.W {
		t.Fatalf("frame %dx%d at %+v", f.Image.W, f.Image.H, f.Source)
	}
	b1, err := hub.JPEG(f, 70)
	if err != nil {
		t.Fatal(err)
	}
	b2, _ := hub.JPEG(f, 70)
	if &b1[0] != &b2[0] {
		t.Fatal("the same frame and quality must be encoded once")
	}
	deadline := time.Now().Add(3 * time.Second)
	for {
		src.mu.Lock()
		runs := src.runs
		src.mu.Unlock()
		if runs >= 2 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("the source was not restarted after it failed")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if st := hub.Status(); st.Source != "fake" || st.Frames < 3 {
		t.Fatalf("status %+v", st)
	}
}
