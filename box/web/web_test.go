package web

import (
	"encoding/xml"
	"io"
	"io/fs"
	"strings"
	"testing"
)

// The guide's drawings are built in, well-formed, and keep the classes the console colours by state.
func TestGuideDrawings(t *testing.T) {
	for name, hooks := range map[string][]string{
		"guide/hub.svg": {"g-link-usb", "g-link-video", "g-link-net", "g-alert-usb", "g-alert-video", "g-alert-net", "g-flow"},
		"guide/box.svg": {"g-flow"},
		"guide/use.svg": {"g-cursor"},
	} {
		b, err := fs.ReadFile(Files, name)
		if err != nil {
			t.Fatalf("%s is not built in: %v", name, err)
		}
		d := xml.NewDecoder(strings.NewReader(string(b)))
		for {
			if _, err := d.Token(); err == io.EOF {
				break
			} else if err != nil {
				t.Fatalf("%s: %v", name, err)
			}
		}
		for _, h := range hooks {
			if !strings.Contains(string(b), h) {
				t.Errorf("%s lost %s", name, h)
			}
		}
	}
}

// Every icon the page uses is in its sprite.
func TestIconsExist(t *testing.T) {
	page, _ := fs.ReadFile(Files, "index.html")
	script, _ := fs.ReadFile(Files, "app.js")
	for _, src := range []string{string(page), string(script)} {
		for _, part := range strings.Split(src, `href="#i-`)[1:] {
			id := "i-" + part[:strings.IndexAny(part, `"`)]
			if strings.HasPrefix(id, "i-${") { // iconButton's template: its names are checked below
				continue
			}
			if !strings.Contains(string(page), `<symbol id="`+id+`"`) {
				t.Errorf("no icon %s", id)
			}
		}
	}
	for _, part := range strings.Split(string(script), `iconButton("`)[1:] {
		id := "i-" + part[:strings.Index(part, `"`)]
		if !strings.Contains(string(page), `<symbol id="`+id+`"`) {
			t.Errorf("app.js uses a missing icon %s", id)
		}
	}
}
