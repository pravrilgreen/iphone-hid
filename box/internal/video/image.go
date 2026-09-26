// Package video reads the iPhone's screen from the board's HDMI input, keeps the part of each
// frame that shows the phone, and encodes it as JPEG for the live view, screenshots and streams.
package video

import (
	"errors"
	"fmt"
)

// Subsampling of the chroma planes.
type Subsampling int

// Chroma subsampling ratios.
const (
	Sub420 Subsampling = iota // chroma planes are half width, half height
	Sub422                    // half width, full height
	Sub444                    // full size
)

func (s Subsampling) String() string {
	return [...]string{"4:2:0", "4:2:2", "4:4:4"}[s]
}

// Image is a picture in planar YCbCr with full-range (JPEG) levels, or packed BGR.
type Image struct {
	W, H      int
	Sub       Subsampling
	Y, Cb, Cr []byte
	YStride   int
	CStride   int
	BGR       []byte // packed 3-byte pixels instead of the planes, when set
	BGRStride int
}

// ChromaSize returns the size of the chroma planes.
func (s Subsampling) ChromaSize(w, h int) (int, int) {
	switch s {
	case Sub420:
		return (w + 1) / 2, (h + 1) / 2
	case Sub422:
		return (w + 1) / 2, h
	default:
		return w, h
	}
}

// NewPlanar allocates a planar image.
func NewPlanar(w, h int, sub Subsampling) *Image {
	cw, ch := sub.ChromaSize(w, h)
	return &Image{W: w, H: h, Sub: sub, Y: make([]byte, w*h), Cb: make([]byte, cw*ch), Cr: make([]byte, cw*ch),
		YStride: w, CStride: cw}
}

// Rect is a pixel rectangle in a frame.
type Rect struct{ X, Y, W, H int }

// Align shrinks r so that it starts and ends on the chroma grid of sub, inside a w x h frame.
func (r Rect) Align(sub Subsampling, w, h int) Rect {
	if r.X < 0 {
		r.W += r.X
		r.X = 0
	}
	if r.Y < 0 {
		r.H += r.Y
		r.Y = 0
	}
	if r.X+r.W > w {
		r.W = w - r.X
	}
	if r.Y+r.H > h {
		r.H = h - r.Y
	}
	if sub != Sub444 {
		if r.X%2 == 1 {
			r.X++
			r.W--
		}
		r.W &^= 1
	}
	if sub == Sub420 {
		if r.Y%2 == 1 {
			r.Y++
			r.H--
		}
		r.H &^= 1
	}
	if r.W < 0 {
		r.W = 0
	}
	if r.H < 0 {
		r.H = 0
	}
	return r
}

// Levels maps video-range (16..235 luma, 16..240 chroma) samples to full range. HDMI receivers
// deliver YCbCr in video range; JPEG expects full range, so without this the picture is grey.
type Levels struct{ y, c [256]byte }

// VideoLevels expands video range; FullLevels leaves samples as they are.
var (
	VideoLevels = func() *Levels {
		var l Levels
		for i := 0; i < 256; i++ {
			l.y[i] = clampByte((float64(i) - 16) * 255 / 219)
			l.c[i] = clampByte((float64(i)-128)*255/224 + 128)
		}
		return &l
	}()
	FullLevels = func() *Levels {
		var l Levels
		for i := 0; i < 256; i++ {
			l.y[i], l.c[i] = byte(i), byte(i)
		}
		return &l
	}()
)

func clampByte(v float64) byte {
	v += 0.5
	if v < 0 {
		return 0
	}
	if v > 255 {
		return 255
	}
	return byte(v)
}

// Raw is one captured frame as the receiver delivered it.
type Raw struct {
	Format string // V4L2 fourcc: NV12, NV21, NV16, NV61, NV24, NV42, YUYV, UYVY, BGR3, RGB3
	W, H   int
	Planes [][]byte // one per V4L2 plane (semi-planar formats in one plane keep chroma after luma)
	Stride []int    // bytes per line of each plane
	Full   bool     // YCbCr already in full range
}

// Formats this package converts, most preferred first (NV12 is the smallest to move around).
var Formats = []string{"NV12", "NV16", "NV24", "BGR3", "RGB3", "NV21", "NV61", "NV42", "YUYV", "UYVY"}

// SubsamplingOf returns the chroma subsampling a crop of the format keeps.
func SubsamplingOf(format string) Subsampling {
	switch format {
	case "NV12", "NV21":
		return Sub420
	case "NV16", "NV61", "YUYV", "UYVY":
		return Sub422
	}
	return Sub444
}

// ErrFormat is returned for a pixel format the package cannot convert.
var ErrFormat = errors.New("unsupported pixel format")

// Crop copies the rectangle r of a raw frame into dst (reallocated when its size differs) as
// planar full-range YCbCr, or as packed BGR for RGB sources. r must lie inside the frame; it is
// aligned to the chroma grid first.
func Crop(raw *Raw, r Rect, dst *Image) (*Image, error) {
	switch raw.Format {
	case "BGR3", "RGB3":
		return cropRGB(raw, r, dst)
	case "NV12", "NV21":
		return cropSemi(raw, r, dst, Sub420)
	case "NV16", "NV61":
		return cropSemi(raw, r, dst, Sub422)
	case "NV24", "NV42":
		return cropSemi(raw, r, dst, Sub444)
	case "YUYV", "UYVY":
		return cropPacked(raw, r, dst)
	}
	return nil, fmt.Errorf("%w: %s", ErrFormat, raw.Format)
}

func (raw *Raw) levels() *Levels {
	if raw.Full {
		return FullLevels
	}
	return VideoLevels
}

func ensure(dst *Image, w, h int, sub Subsampling) *Image {
	if dst == nil || dst.W != w || dst.H != h || dst.Sub != sub || dst.BGR != nil {
		return NewPlanar(w, h, sub)
	}
	return dst
}

func cropSemi(raw *Raw, r Rect, dst *Image, sub Subsampling) (*Image, error) {
	r = r.Align(sub, raw.W, raw.H)
	if r.W <= 0 || r.H <= 0 {
		return nil, errors.New("empty crop")
	}
	yPlane, yStride := raw.Planes[0], raw.Stride[0]
	var uv []byte
	uvStride := yStride
	if sub == Sub444 {
		uvStride = yStride * 2
	}
	if len(raw.Planes) > 1 {
		uv = raw.Planes[1]
		if len(raw.Stride) > 1 {
			uvStride = raw.Stride[1]
		}
	} else {
		if len(yPlane) < yStride*raw.H {
			return nil, errors.New("short frame")
		}
		uv = yPlane[yStride*raw.H:]
	}
	cw, ch := sub.ChromaSize(r.W, r.H)
	cx, cy := r.X/2, r.Y/2
	if sub == Sub444 {
		cx = r.X
	}
	if sub != Sub420 {
		cy = r.Y
	}
	if len(yPlane) < yStride*(r.Y+r.H-1)+r.X+r.W || len(uv) < uvStride*(cy+ch-1)+2*(cx+cw) {
		return nil, errors.New("short frame")
	}
	dst = ensure(dst, r.W, r.H, sub)
	lv := raw.levels()
	for row := 0; row < r.H; row++ {
		src := yPlane[(r.Y+row)*yStride+r.X:][:r.W]
		out := dst.Y[row*dst.YStride:][:r.W]
		for i, v := range src {
			out[i] = lv.y[v]
		}
	}
	swap := raw.Format == "NV21" || raw.Format == "NV61" || raw.Format == "NV42"
	for row := 0; row < ch; row++ {
		src := uv[(cy+row)*uvStride+2*cx:][:2*cw]
		cb := dst.Cb[row*dst.CStride:][:cw]
		cr := dst.Cr[row*dst.CStride:][:cw]
		for i := 0; i < cw; i++ {
			u, v := src[2*i], src[2*i+1]
			if swap {
				u, v = v, u
			}
			cb[i], cr[i] = lv.c[u], lv.c[v]
		}
	}
	return dst, nil
}

func cropPacked(raw *Raw, r Rect, dst *Image) (*Image, error) {
	r = r.Align(Sub422, raw.W, raw.H)
	if r.W <= 0 || r.H <= 0 {
		return nil, errors.New("empty crop")
	}
	p, stride := raw.Planes[0], raw.Stride[0]
	if len(p) < stride*(r.Y+r.H-1)+2*(r.X+r.W) {
		return nil, errors.New("short frame")
	}
	dst = ensure(dst, r.W, r.H, Sub422)
	lv := raw.levels()
	yi, ui, vi := 0, 1, 3 // YUYV: Y0 U Y1 V
	if raw.Format == "UYVY" {
		yi, ui, vi = 1, 0, 2 // U Y0 V Y1
	}
	for row := 0; row < r.H; row++ {
		src := p[(r.Y+row)*stride+2*r.X:][:2*r.W]
		y := dst.Y[row*dst.YStride:][:r.W]
		cb := dst.Cb[row*dst.CStride:][:r.W/2]
		cr := dst.Cr[row*dst.CStride:][:r.W/2]
		for i := 0; i < r.W/2; i++ {
			q := src[4*i:][:4]
			y[2*i], y[2*i+1] = lv.y[q[yi]], lv.y[q[yi+2]]
			cb[i], cr[i] = lv.c[q[ui]], lv.c[q[vi]]
		}
	}
	return dst, nil
}

func cropRGB(raw *Raw, r Rect, dst *Image) (*Image, error) {
	r = r.Align(Sub444, raw.W, raw.H)
	if r.W <= 0 || r.H <= 0 {
		return nil, errors.New("empty crop")
	}
	p, stride := raw.Planes[0], raw.Stride[0]
	if len(p) < stride*(r.Y+r.H-1)+3*(r.X+r.W) {
		return nil, errors.New("short frame")
	}
	if dst == nil || dst.BGR == nil || dst.W != r.W || dst.H != r.H {
		dst = &Image{W: r.W, H: r.H, BGR: make([]byte, 3*r.W*r.H), BGRStride: 3 * r.W}
	}
	rgb := raw.Format == "RGB3"
	for row := 0; row < r.H; row++ {
		src := p[(r.Y+row)*stride+3*r.X:][:3*r.W]
		out := dst.BGR[row*dst.BGRStride:][:3*r.W]
		if !rgb {
			copy(out, src)
			continue
		}
		for i := 0; i < len(src); i += 3 {
			out[i], out[i+1], out[i+2] = src[i+2], src[i+1], src[i]
		}
	}
	return dst, nil
}
