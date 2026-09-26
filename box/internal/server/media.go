package server

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/draw"
	"image/png"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// screenshot returns the newest picture of the phone screen: JPEG (quality=, default 90) or PNG
// (format=png). With wait=true it waits for a frame captured after the request.
func (s *Server) screenshot(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	format := strings.ToLower(q.Get("format"))
	switch format {
	case "", "jpeg", "jpg":
		format = "jpeg"
	case "png":
	default:
		writeError(w, http.StatusBadRequest, "bad_request", "format is jpeg or png, not "+q.Get("format"))
		return
	}
	quality := 90
	if v := q.Get("quality"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 30 || n > 100 {
			writeError(w, http.StatusBadRequest, "bad_request", "quality is a whole number from 30 to 100, not "+v)
			return
		}
		quality = n
	}
	wait, err := queryBool(q, "wait", false)
	if err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	st := s.hub.Status()
	if st.State != "ok" && st.State != "starting" {
		writeError(w, http.StatusServiceUnavailable, "no_video", noPicture(st))
		return
	}
	f := s.hub.Latest()
	if wait || f == nil {
		var seq uint64
		if f != nil {
			seq = f.Seq
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		next, err := s.hub.Next(ctx, seq)
		if err != nil {
			writeError(w, http.StatusServiceUnavailable, "no_video", noPicture(s.hub.Status()))
			return
		}
		f = next
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Frame-Age-Ms", strconv.FormatInt(time.Since(f.At).Milliseconds(), 10))
	if format == "png" {
		var buf bytes.Buffer
		enc := png.Encoder{CompressionLevel: png.BestSpeed}
		if err := enc.Encode(&buf, toRGBA(f.Image)); err != nil {
			writeError(w, http.StatusInternalServerError, "failed", err.Error())
			return
		}
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(buf.Bytes())
		return
	}
	b, err := s.hub.JPEG(f, quality)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed", err.Error())
		return
	}
	w.Header().Set("Content-Type", "image/jpeg")
	_, _ = w.Write(b)
}

// noPicture says why there is no picture and what to do.
func noPicture(st video.Status) string {
	return "no picture from the phone (" + orNone(st.Error) + "): unlock the iPhone, and check that the hub's " +
		"HDMI cable goes into the board's HDMI IN port"
}

// queryBool reads a true/false query parameter (true, 1, false, 0, ...).
func queryBool(q url.Values, name string, def bool) (bool, error) {
	v := q.Get(name)
	if v == "" {
		return def, nil
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return def, fmt.Errorf("%s is true or false, not %s", name, v)
	}
	return b, nil
}

// viewer takes one of the box's viewer places; release gives it back. false: all taken.
func (s *Server) viewer() (release func(), ok bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.viewers >= s.maxViewers() {
		return nil, false
	}
	s.viewers++
	return func() {
		s.mu.Lock()
		s.viewers--
		s.mu.Unlock()
	}, true
}

func (s *Server) maxViewers() int {
	if s.cfg.MaxViewers > 0 {
		return s.cfg.MaxViewers
	}
	return maxViewers
}

// mjpeg streams the screen as multipart JPEG, for viewers that cannot use the WebSocket stream.
func (s *Server) mjpeg(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	quality := clampQuery(q.Get("quality"), 75, 30, 95)
	fps := clampQuery(q.Get("fps"), 30, 1, 60)
	frames := clampQuery(q.Get("frames"), 0, 0, 1<<30)
	if st := s.hub.Status(); st.State != "ok" && st.State != "starting" {
		writeError(w, http.StatusServiceUnavailable, "no_video", noPicture(st))
		return
	}
	release, ok := s.viewer()
	if !ok {
		writeError(w, http.StatusTooManyRequests, "too_many", fmt.Sprintf("this box streams to %d viewers at most", s.maxViewers()))
		return
	}
	defer release()
	rc := http.NewResponseController(w)
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
		_ = rc.SetWriteDeadline(time.Now().Add(writeDeadline)) // a reader that stalls gives its place back
		if _, err := fmt.Fprintf(w, "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n", len(b)); err != nil {
			return
		}
		// the JPEG is shared with the other viewers: never append to it
		if _, err := w.Write(b); err != nil {
			return
		}
		if _, err := io.WriteString(w, "\r\n"); err != nil {
			return
		}
		if rc.Flush() != nil {
			return
		}
		select {
		case <-time.After(gap):
		case <-r.Context().Done():
			return
		}
	}
}

// toRGBA converts a frame for the PNG encoder: 8 bits a channel (from YCbCr it would write 16).
func toRGBA(img *video.Image) *image.RGBA {
	src := toImage(img)
	if rgba, ok := src.(*image.RGBA); ok {
		return rgba
	}
	rgba := image.NewRGBA(src.Bounds())
	draw.Draw(rgba, rgba.Rect, src, image.Point{}, draw.Src)
	return rgba
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
