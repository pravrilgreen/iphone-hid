//go:build turbojpeg

package video

/*
#cgo LDFLAGS: -lturbojpeg
#include <stdlib.h>
#include <turbojpeg.h>

static int ihc_encode_yuv(tjhandle h, const unsigned char *y, const unsigned char *cb, const unsigned char *cr,
		int w, int ys, int cs, int hgt, int sub, unsigned char **buf, unsigned long *size, int q) {
	const unsigned char *planes[3] = {y, cb, cr};
	int strides[3] = {ys, cs, cs};
	return tjCompressFromYUVPlanes(h, planes, w, strides, hgt, sub, buf, size, q, TJFLAG_FASTDCT | TJFLAG_NOREALLOC);
}

static int ihc_encode_bgr(tjhandle h, const unsigned char *src, int w, int pitch, int hgt,
		unsigned char **buf, unsigned long *size, int q) {
	return tjCompress2(h, src, w, pitch, hgt, TJPF_BGR, buf, size, TJSAMP_420, q, TJFLAG_FASTDCT | TJFLAG_NOREALLOC);
}
*/
import "C"

import (
	"errors"
	"unsafe"
)

const encoderName = "libjpeg-turbo"

type turboEncoder struct {
	h    C.tjhandle
	buf  *C.uchar
	size C.ulong // capacity of buf
}

func newEncoder() Encoder { return &turboEncoder{h: C.tjInitCompress()} }

func (e *turboEncoder) Encode(img *Image, quality int) ([]byte, error) {
	if e.h == nil {
		return nil, errors.New("libjpeg-turbo: no compressor")
	}
	sub := map[Subsampling]C.int{Sub420: C.TJSAMP_420, Sub422: C.TJSAMP_422, Sub444: C.TJSAMP_444}[img.Sub]
	if img.BGR != nil {
		sub = C.TJSAMP_420
	}
	need := C.tjBufSize(C.int(img.W), C.int(img.H), sub)
	if e.buf == nil || e.size < need {
		if e.buf != nil {
			C.tjFree(e.buf)
		}
		e.buf = C.tjAlloc(C.int(need))
		e.size = need
	}
	size := e.size
	var rc C.int
	if img.BGR != nil {
		rc = C.ihc_encode_bgr(e.h, (*C.uchar)(unsafe.Pointer(&img.BGR[0])), C.int(img.W), C.int(img.BGRStride),
			C.int(img.H), &e.buf, &size, C.int(quality))
	} else {
		rc = C.ihc_encode_yuv(e.h, (*C.uchar)(unsafe.Pointer(&img.Y[0])), (*C.uchar)(unsafe.Pointer(&img.Cb[0])),
			(*C.uchar)(unsafe.Pointer(&img.Cr[0])), C.int(img.W), C.int(img.YStride), C.int(img.CStride), C.int(img.H),
			sub, &e.buf, &size, C.int(quality))
	}
	if rc != 0 {
		return nil, errors.New("libjpeg-turbo: " + C.GoString(C.tjGetErrorStr2(e.h)))
	}
	return C.GoBytes(unsafe.Pointer(e.buf), C.int(size)), nil
}

func (e *turboEncoder) Close() {
	if e.buf != nil {
		C.tjFree(e.buf)
		e.buf = nil
	}
	if e.h != nil {
		C.tjDestroy(e.h)
		e.h = nil
	}
}
