package server

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/png"
	"net/http"
	"strconv"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// screenshot returns the newest picture of the phone screen: JPEG (quality=, default 90) or PNG
// (format=png). With wait=true it waits for a frame captured after the request.
func (s *Server) screenshot(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	f := s.hub.Latest()
	if q.Get("wait") == "true" || f == nil || time.Since(f.At) > 2*time.Second {
		var seq uint64
		if f != nil {
			seq = f.Seq
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		next, err := s.hub.Next(ctx, seq)
		if err != nil {
			st := s.hub.Status()
			writeError(w, http.StatusServiceUnavailable, "no_video", "no picture from the phone: "+orNone(st.Error))
			return
		}
		f = next
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Frame-Age-Ms", strconv.FormatInt(time.Since(f.At).Milliseconds(), 10))
	if q.Get("format") == "png" {
		var buf bytes.Buffer
		if err := png.Encode(&buf, toImage(f.Image)); err != nil {
			writeError(w, http.StatusInternalServerError, "failed", err.Error())
			return
		}
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(buf.Bytes())
		return
	}
	b, err := s.hub.JPEG(f, clampQuery(q.Get("quality"), 90, 30, 100))
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed", err.Error())
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	_, _ = w.Write(b)
}

// mjpeg streams the screen as multipart JPEG, for viewers that cannot use the WebSocket stream.
func (s *Server) mjpeg(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	quality := clampQuery(q.Get("quality"), 75, 30, 95)
	fps := clampQuery(q.Get("fps"), 30, 1, 60)
	frames := clampQuery(q.Get("frames"), 0, 0, 1<<30)
	s.mu.Lock()
	if s.viewers >= maxViewers {
		s.mu.Unlock()
		writeError(w, http.StatusTooManyRequests, "too_many", "too many viewers")
		return
	}
	s.viewers++
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.viewers--
		s.mu.Unlock()
	}()
	flusher, _ := w.(http.Flusher)
	w.Header().Set("Content-Type", "multipart/x-mixed-replace; boundary=frame")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	var seq uint64
	gap := time.Second / time.Duration(fps)
	for n := 0; frames == 0 || n < frames; n++ {
		f, err := s.hub.Next(r.Context(), seq)
		if err != nil {
			return
		}
		seq = f.Seq
		b, err := s.hub.JPEG(f, quality)
		if err != nil {
			continue
		}
		if _, err := fmt.Fprintf(w, "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n", len(b)); err != nil {
			return
		}
		if _, err := w.Write(append(b, '\r', '\n')); err != nil {
			return
		}
		if flusher != nil {
			flusher.Flush()
		}
		select {
		case <-time.After(gap):
		case <-r.Context().Done():
			return
		}
	}
}

func toImage(img *video.Image) image.Image {
	if img.BGR != nil {
		rgba := image.NewRGBA(image.Rect(0, 0, img.W, img.H))
		for y := 0; y < img.H; y++ {
			src := img.BGR[y*img.BGRStride:]
			dst := rgba.Pix[y*rgba.Stride:]
			for x := 0; x < img.W; x++ {
				dst[4*x], dst[4*x+1], dst[4*x+2], dst[4*x+3] = src[3*x+2], src[3*x+1], src[3*x], 255
			}
		}
		return rgba
	}
	ratio := map[video.Subsampling]image.YCbCrSubsampleRatio{video.Sub420: image.YCbCrSubsampleRatio420,
		video.Sub422: image.YCbCrSubsampleRatio422, video.Sub444: image.YCbCrSubsampleRatio444}[img.Sub]
	return &image.YCbCr{Y: img.Y, Cb: img.Cb, Cr: img.Cr, YStride: img.YStride, CStride: img.CStride,
		SubsampleRatio: ratio, Rect: image.Rect(0, 0, img.W, img.H)}
}
