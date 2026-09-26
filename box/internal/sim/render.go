package sim

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"math"
	"strings"
	"time"

	"golang.org/x/image/font"
	"golang.org/x/image/font/gofont/gobold"
	"golang.org/x/image/font/gofont/goregular"
	"golang.org/x/image/font/opentype"
	"golang.org/x/image/math/fixed"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// renderer draws the phone screen and mirrors it into 1080p NV12 frames (video range, as an HDMI
// receiver delivers them).
type renderer struct {
	area   video.Rect // where the screen sits in the frame
	scale  float64    // pixels per point
	canvas *image.RGBA
	wall   *image.RGBA
	faces  map[string]font.Face
	buf    []byte // NV12: luma then interleaved chroma
}

func newRenderer() *renderer {
	rect := video.Layout{}.Rect(FrameW, FrameH)
	r := &renderer{area: rect, scale: float64(rect.H) / ScreenH,
		canvas: image.NewRGBA(image.Rect(0, 0, rect.W, rect.H)), faces: map[string]font.Face{}}
	for name, spec := range map[string]struct {
		ttf  []byte
		size float64
	}{
		"label": {goregular.TTF, 11}, "body": {goregular.TTF, 17}, "title": {gobold.TTF, 30},
		"status": {gobold.TTF, 15}, "icon": {gobold.TTF, 28}, "small": {goregular.TTF, 13},
	} {
		f, _ := opentype.Parse(spec.ttf)
		face, _ := opentype.NewFace(f, &opentype.FaceOptions{Size: spec.size * r.scale, DPI: 72, Hinting: font.HintingFull})
		r.faces[name] = face
	}
	r.wall = image.NewRGBA(r.canvas.Bounds())
	for y := 0; y < rect.H; y++ {
		t := float64(y) / float64(rect.H)
		for x := 0; x < rect.W; x++ {
			u := float64(x) / float64(rect.W)
			c := color.RGBA{uint8(40 + 60*t + 30*u), uint8(60 + 20*u), uint8(140 + 60*(1-t)), 255}
			r.wall.SetRGBA(x, y, c)
		}
	}
	r.buf = make([]byte, FrameW*FrameH*3/2)
	for i := 0; i < FrameW*FrameH; i++ {
		r.buf[i] = 16 // black bars, video range
	}
	for i := FrameW * FrameH; i < len(r.buf); i++ {
		r.buf[i] = 128
	}
	return r
}

func (r *renderer) px(v float64) int { return int(math.Round(v * r.scale)) }

// frame draws the phone's screen and returns it as a captured frame.
// view is what the renderer draws: a copy of the phone's display state.
type view struct {
	screen   screenKind
	app      int
	page     float64
	scroll   float64
	notes    string
	search   string
	volume   float64
	hudUntil time.Time
	recent   []int
	ptr      hid.Pointer
	ptrKnown bool
}

func (r *renderer) frame(p *view, now time.Time) *video.Raw {
	c := r.canvas
	switch p.screen {
	case screenHome:
		r.home(p)
	case screenApp:
		r.app(p)
	case screenSwitcher:
		r.switcher(p)
	case screenSearch:
		r.search(p)
	}
	dark := p.screen == screenApp
	r.statusBar(now, dark)
	if now.Before(p.hudUntil) {
		r.volumeHUD(p.volume)
	}
	if p.ptrKnown {
		x := float64(p.ptr.X) / 32767 * ScreenW
		y := float64(p.ptr.Y) / 32767 * ScreenH
		rad, a := 13.0, uint8(110)
		if p.ptr.Buttons&1 != 0 {
			rad, a = 10.0, 170
		}
		r.circle(x, y, rad, color.RGBA{60, 60, 67, a})
		r.ring(x, y, rad, color.RGBA{255, 255, 255, 140})
	}
	r.toNV12(c)
	return &video.Raw{Format: "NV12", W: FrameW, H: FrameH, Planes: [][]byte{r.buf}, Stride: []int{FrameW}}
}

func (r *renderer) home(p *view) {
	draw.Draw(r.canvas, r.canvas.Bounds(), r.wall, image.Point{}, draw.Src)
	for pg := 0; pg < pages(); pg++ {
		off := (float64(pg) - p.page) * ScreenW
		if math.Abs(off) >= ScreenW {
			continue
		}
		for i := 0; i < appsPerPage; i++ {
			idx := pg*appsPerPage + i
			if idx >= len(Apps) {
				break
			}
			a := Apps[idx]
			x, y := iconPos(i)
			x += off
			r.roundRect(x, y, iconSize, iconSize, 14, color.RGBA{a.Color[0], a.Color[1], a.Color[2], 255})
			fg := color.RGBA{255, 255, 255, 255}
			if a.Name == "Reminders" {
				fg = color.RGBA{255, 59, 48, 255}
			}
			r.textCentered("icon", string([]rune(a.Name)[0]), x+iconSize/2, y+iconSize/2+10, fg)
			r.textCentered("label", a.Name, x+iconSize/2, y+iconSize+15, color.RGBA{255, 255, 255, 255})
		}
	}
	for pg := 0; pg < pages(); pg++ {
		a := uint8(110)
		if int(math.Round(p.page)) == pg {
			a = 255
		}
		r.circle(ScreenW/2-8+float64(pg)*16, 700, 3.5, color.RGBA{255, 255, 255, a})
	}
	// dock
	r.roundRect(12, 730, ScreenW-24, 96, 30, color.RGBA{255, 255, 255, 60})
	for i, name := range []string{"Safari", "Mail", "Music", "Photos"} {
		for _, a := range Apps {
			if a.Name == name {
				x := 27 + float64(i)*(iconSize+27)
				r.roundRect(x, 747, iconSize, iconSize, 14, color.RGBA{a.Color[0], a.Color[1], a.Color[2], 255})
				r.textCentered("icon", string([]rune(a.Name)[0]), x+iconSize/2, 747+iconSize/2+10, color.RGBA{255, 255, 255, 255})
			}
		}
	}
	r.homeIndicator(color.RGBA{255, 255, 255, 220})
}

func (r *renderer) app(p *view) {
	a := Apps[p.app]
	bg := color.RGBA{242, 242, 247, 255}
	draw.Draw(r.canvas, r.canvas.Bounds(), &image.Uniform{bg}, image.Point{}, draw.Src)
	top := 140.0
	switch a.Name {
	case "Notes":
		draw.Draw(r.canvas, r.canvas.Bounds(), &image.Uniform{color.RGBA{255, 255, 255, 255}}, image.Point{}, draw.Src)
		lines := strings.Split(p.notes+"|", "\n")
		for i, line := range lines {
			r.text("body", line, 20, top+30+float64(i)*26, color.RGBA{28, 28, 30, 255})
		}
		if p.notes == "" {
			r.text("body", "Type on the keyboard", 32, top+30, color.RGBA{174, 174, 178, 255})
		}
	case "Photos":
		size := (ScreenW - 6) / 4
		for i := 0; i < 120; i++ {
			x := float64(i%4) * (size + 2)
			y := top + float64(i/4)*(size+2) - p.scroll
			if y > ScreenH || y+size < top {
				continue
			}
			h := float64((i*37)%360) / 360
			cr, cg, cb := hsv(h, 0.45, 0.9)
			r.rect(x, math.Max(y, top), size, math.Min(size, y+size-top), color.RGBA{cr, cg, cb, 255})
		}
	default:
		for i := 0; i < 40; i++ {
			y := top + float64(i)*64 - p.scroll
			if y > ScreenH || y+64 < top {
				continue
			}
			y0 := math.Max(y, top)
			r.rect(16, y0, ScreenW-32, math.Min(63, y+63-y0), color.RGBA{255, 255, 255, 255})
			if y+22 > top {
				r.roundRect(28, y+14, 34, 34, 8, iconColor(i))
				r.text("body", fmt.Sprintf("%s item %d", a.Name, i+1), 76, y+38, color.RGBA{28, 28, 30, 255})
				r.text("body", "›", ScreenW-44, y+38, color.RGBA{199, 199, 204, 255})
			}
		}
	}
	// navigation bar, over the content
	r.rect(0, 0, ScreenW, top, color.RGBA{242, 242, 247, 250})
	if a.Name == "Notes" {
		r.rect(0, 0, ScreenW, top, color.RGBA{255, 255, 255, 250})
	}
	r.text("body", "‹ Back", 12, 88, color.RGBA{0, 122, 255, 255})
	r.text("title", a.Name, 20, 128, color.RGBA{0, 0, 0, 255})
	r.homeIndicator(color.RGBA{0, 0, 0, 200})
}

func (r *renderer) switcher(p *view) {
	draw.Draw(r.canvas, r.canvas.Bounds(), r.wall, image.Point{}, draw.Src)
	r.rect(0, 0, ScreenW, ScreenH, color.RGBA{0, 0, 0, 140})
	if len(p.recent) == 0 {
		r.textCentered("body", "No recent apps", ScreenW/2, ScreenH/2, color.RGBA{255, 255, 255, 255})
	}
	for n, i := range p.recent {
		a := Apps[i]
		x := 40 + float64(n%2)*170
		y := 160 + float64(n/2)*300
		r.roundRect(x, y, 150, 270, 18, color.RGBA{242, 242, 247, 255})
		r.roundRect(x, y, 150, 44, 18, color.RGBA{a.Color[0], a.Color[1], a.Color[2], 255})
		r.textCentered("small", a.Name, x+75, y+150, color.RGBA{28, 28, 30, 255})
	}
}

func (r *renderer) search(p *view) {
	draw.Draw(r.canvas, r.canvas.Bounds(), r.wall, image.Point{}, draw.Src)
	r.rect(0, 0, ScreenW, ScreenH, color.RGBA{20, 20, 30, 170})
	r.roundRect(16, 80, ScreenW-32, 44, 12, color.RGBA{255, 255, 255, 60})
	q := p.search
	if q == "" {
		r.text("body", "Search", 48, 109, color.RGBA{235, 235, 245, 140})
	} else {
		r.text("body", q+"|", 48, 109, color.RGBA{255, 255, 255, 255})
	}
	row := 0
	for _, a := range Apps {
		if q != "" && !strings.HasPrefix(strings.ToLower(a.Name), strings.ToLower(q)) {
			continue
		}
		if row >= 6 {
			break
		}
		y := 160 + float64(row)*60
		r.roundRect(24, y, 44, 44, 10, color.RGBA{a.Color[0], a.Color[1], a.Color[2], 255})
		r.text("body", a.Name, 84, y+29, color.RGBA{255, 255, 255, 255})
		row++
	}
}

func (r *renderer) statusBar(now time.Time, dark bool) {
	fg := color.RGBA{255, 255, 255, 255}
	if dark {
		fg = color.RGBA{0, 0, 0, 255}
	}
	r.textCentered("status", Clock(now), 70, 36, fg)
	r.roundRect(ScreenW/2-62, 11, 124, 36, 18, color.RGBA{0, 0, 0, 255}) // Dynamic Island
	r.roundRect(ScreenW-62, 25, 27, 13, 4, color.RGBA{fg.R, fg.G, fg.B, 110})
	r.roundRect(ScreenW-60, 27, 19, 9, 2, fg)
}

func (r *renderer) homeIndicator(c color.RGBA) {
	r.roundRect(ScreenW/2-67, ScreenH-13, 134, 5, 2.5, c)
}

func (r *renderer) volumeHUD(v float64) {
	x, y, w, h := 10.0, 250.0, 14.0, 150.0
	r.roundRect(x, y, w, h, 7, color.RGBA{60, 60, 67, 200})
	fill := h * v
	if fill > 1 {
		r.roundRect(x, y+h-fill, w, fill, 7, color.RGBA{255, 255, 255, 240})
	}
}

// -- drawing primitives, in points ------------------------------------------------------------------------

// rect fills a rectangle; c is straight (not premultiplied) alpha, like every colour here.
func (r *renderer) rect(x, y, w, h float64, c color.RGBA) {
	rc := image.Rect(r.px(x), r.px(y), r.px(x+w), r.px(y+h)).Intersect(r.canvas.Bounds())
	if c.A == 255 {
		draw.Draw(r.canvas, rc, &image.Uniform{c}, image.Point{}, draw.Src)
		return
	}
	for py := rc.Min.Y; py < rc.Max.Y; py++ {
		for px := rc.Min.X; px < rc.Max.X; px++ {
			r.blend(px, py, c, 1)
		}
	}
}

// roundRect fills a rounded rectangle, anti-aliased at the corners.
func (r *renderer) roundRect(x, y, w, h, rad float64, c color.RGBA) {
	X, Y, W, H, R := x*r.scale, y*r.scale, w*r.scale, h*r.scale, rad*r.scale
	x0, y0 := int(math.Floor(X)), int(math.Floor(Y))
	x1, y1 := int(math.Ceil(X+W)), int(math.Ceil(Y+H))
	b := r.canvas.Bounds()
	for py := max(y0, b.Min.Y); py < min(y1, b.Max.Y); py++ {
		for px := max(x0, b.Min.X); px < min(x1, b.Max.X); px++ {
			cx, cy := float64(px)+0.5, float64(py)+0.5
			dx := math.Max(math.Max(X+R-cx, cx-(X+W-R)), 0)
			dy := math.Max(math.Max(Y+R-cy, cy-(Y+H-R)), 0)
			cov := 1.0
			if dx > 0 && dy > 0 {
				cov = math.Min(1, math.Max(0, R-math.Hypot(dx, dy)+0.5))
			} else {
				cov = math.Min(1, math.Max(0, math.Min(math.Min(cx-X, X+W-cx), math.Min(cy-Y, Y+H-cy))+0.5))
			}
			if cov > 0 {
				r.blend(px, py, c, cov)
			}
		}
	}
}

func (r *renderer) circle(x, y, rad float64, c color.RGBA) {
	r.roundRect(x-rad, y-rad, 2*rad, 2*rad, rad, c)
}

func (r *renderer) ring(x, y, rad float64, c color.RGBA) {
	X, Y, R := x*r.scale, y*r.scale, rad*r.scale
	b := r.canvas.Bounds()
	for py := max(int(Y-R-2), b.Min.Y); py < min(int(Y+R+2), b.Max.Y); py++ {
		for px := max(int(X-R-2), b.Min.X); px < min(int(X+R+2), b.Max.X); px++ {
			d := math.Abs(math.Hypot(float64(px)+0.5-X, float64(py)+0.5-Y) - R)
			if cov := 1.2 - d; cov > 0 {
				r.blend(px, py, c, math.Min(1, cov))
			}
		}
	}
}

func (r *renderer) blend(px, py int, c color.RGBA, cov float64) {
	a := float64(c.A) / 255 * cov
	i := r.canvas.PixOffset(px, py)
	p := r.canvas.Pix[i : i+3]
	p[0] = uint8(float64(p[0])*(1-a) + float64(c.R)*a)
	p[1] = uint8(float64(p[1])*(1-a) + float64(c.G)*a)
	p[2] = uint8(float64(p[2])*(1-a) + float64(c.B)*a)
}

func (r *renderer) text(face, s string, x, y float64, c color.RGBA) {
	d := font.Drawer{Dst: r.canvas, Src: &image.Uniform{c}, Face: r.faces[face],
		Dot: fixed.P(r.px(x), r.px(y))}
	d.DrawString(s)
}

func (r *renderer) textCentered(face, s string, x, y float64, c color.RGBA) {
	w := font.MeasureString(r.faces[face], s).Round()
	d := font.Drawer{Dst: r.canvas, Src: &image.Uniform{c}, Face: r.faces[face],
		Dot: fixed.P(r.px(x)-w/2, r.px(y))}
	d.DrawString(s)
}

// toNV12 converts the canvas into the frame's phone area, in video range (BT.601).
func (r *renderer) toNV12(c *image.RGBA) {
	W := FrameW
	yPlane, uv := r.buf[:W*FrameH], r.buf[W*FrameH:]
	for y := 0; y < r.area.H; y++ {
		row := c.Pix[y*c.Stride:]
		out := yPlane[(r.area.Y+y)*W+r.area.X:]
		for x := 0; x < r.area.W; x++ {
			R, G, B := float64(row[4*x]), float64(row[4*x+1]), float64(row[4*x+2])
			out[x] = uint8(16 + (65.481*R+128.553*G+24.966*B)/255 + 0.5)
		}
	}
	for y := 0; y < r.area.H; y += 2 {
		r0, r1 := c.Pix[y*c.Stride:], c.Pix[(y+1)*c.Stride:]
		out := uv[((r.area.Y+y)/2)*W+r.area.X:]
		for x := 0; x < r.area.W; x += 2 {
			var R, G, B float64
			for _, px := range [][]uint8{r0[4*x:], r0[4*x+4:], r1[4*x:], r1[4*x+4:]} {
				R += float64(px[0])
				G += float64(px[1])
				B += float64(px[2])
			}
			R, G, B = R/4, G/4, B/4
			out[x] = uint8(128 + (-37.797*R-74.203*G+112.0*B)/255 + 0.5)
			out[x+1] = uint8(128 + (112.0*R-93.786*G-18.214*B)/255 + 0.5)
		}
	}
}

func iconColor(i int) color.RGBA {
	cols := []color.RGBA{{255, 59, 48, 255}, {255, 149, 0, 255}, {52, 199, 89, 255}, {0, 122, 255, 255},
		{88, 86, 214, 255}, {255, 45, 85, 255}, {142, 142, 147, 255}}
	return cols[i%len(cols)]
}

func hsv(h, s, v float64) (uint8, uint8, uint8) {
	i := math.Floor(h * 6)
	f := h*6 - i
	p, q, t := v*(1-s), v*(1-f*s), v*(1-(1-f)*s)
	var r, g, b float64
	switch int(i) % 6 {
	case 0:
		r, g, b = v, t, p
	case 1:
		r, g, b = q, v, p
	case 2:
		r, g, b = p, v, t
	case 3:
		r, g, b = p, q, v
	case 4:
		r, g, b = t, p, v
	default:
		r, g, b = v, p, q
	}
	return uint8(r * 255), uint8(g * 255), uint8(b * 255)
}
