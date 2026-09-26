//go:build !turbojpeg

package video

import (
	"bytes"
	"image"
	"image/jpeg"
)

const encoderName = "Go image/jpeg"

// stdEncoder is the portable encoder: slower than libjpeg-turbo, used by development builds.
type stdEncoder struct{ buf bytes.Buffer }

func newEncoder() Encoder { return &stdEncoder{} }

func (e *stdEncoder) Encode(img *Image, quality int) ([]byte, error) {
	e.buf.Reset()
	var m image.Image
	if img.BGR != nil {
		rgba := image.NewRGBA(image.Rect(0, 0, img.W, img.H))
		for y := 0; y < img.H; y++ {
			src := img.BGR[y*img.BGRStride:][:3*img.W]
			dst := rgba.Pix[y*rgba.Stride:][:4*img.W]
			for x := 0; x < img.W; x++ {
				dst[4*x], dst[4*x+1], dst[4*x+2], dst[4*x+3] = src[3*x+2], src[3*x+1], src[3*x], 255
			}
		}
		m = rgba
	} else {
		ratio := map[Subsampling]image.YCbCrSubsampleRatio{Sub420: image.YCbCrSubsampleRatio420,
			Sub422: image.YCbCrSubsampleRatio422, Sub444: image.YCbCrSubsampleRatio444}[img.Sub]
		m = &image.YCbCr{Y: img.Y, Cb: img.Cb, Cr: img.Cr, YStride: img.YStride, CStride: img.CStride,
			SubsampleRatio: ratio, Rect: image.Rect(0, 0, img.W, img.H)}
	}
	if err := jpeg.Encode(&e.buf, m, &jpeg.Options{Quality: quality}); err != nil {
		return nil, err
	}
	return append([]byte(nil), e.buf.Bytes()...), nil
}

func (e *stdEncoder) Close() {}
