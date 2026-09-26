package hid

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestDescriptorsMatchTheReportLengths(t *testing.T) {
	// report bits declared by each descriptor (main Input items), against the report lengths
	for name, fn := range Functions {
		bits := inputBits(t, fn.Descriptor)
		if bits != fn.ReportLength*8 {
			t.Errorf("%s: descriptor declares %d input bits, report length is %d bytes", name, bits, fn.ReportLength)
		}
	}
}

// inputBits sums report size x report count over the Input items of a descriptor.
func inputBits(t *testing.T, d []byte) int {
	t.Helper()
	size, count, total := 0, 0, 0
	for i := 0; i < len(d); {
		prefix := d[i]
		n := int(prefix & 3)
		if n == 3 {
			n = 4
		}
		if i+1+n > len(d) {
			t.Fatalf("truncated item at %d", i)
		}
		v := 0
		for k := 0; k < n; k++ {
			v |= int(d[i+1+k]) << (8 * k)
		}
		switch prefix &^ 3 {
		case 0x74:
			size = v
		case 0x94:
			count = v
		case 0x80:
			total += size * count
		}
		i += 1 + n
	}
	return total
}

func TestPointerReport(t *testing.T) {
	r := Pointer{Buttons: ButtonPrimary, X: 0x1234, Y: AbsMax, Wheel: -1}.Report()
	want := []byte{1, 0x34, 0x12, 0xFF, 0x7F, 0xFF}
	if !bytes.Equal(r, want) {
		t.Fatalf("report % x, want % x", r, want)
	}
	if AbsCoord(0) != 0 || AbsCoord(1) != AbsMax || AbsCoord(0.5) != 16384 || AbsCoord(-3) != 0 || AbsCoord(7) != AbsMax {
		t.Fatal("AbsCoord maps the edges and centre wrong")
	}
}

func TestCombosAndText(t *testing.T) {
	ks, err := ParseCombo("cmd+space")
	if err != nil || ks.Mods != ModCmd || len(ks.Keys) != 1 || ks.Keys[0] != 0x2C {
		t.Fatalf("cmd+space: %+v %v", ks, err)
	}
	ks, err = ParseCombo("Cmd+Shift+3")
	if err != nil || ks.Mods != ModCmd|ModShift || ks.Keys[0] != 0x20 {
		t.Fatalf("cmd+shift+3: %+v %v", ks, err)
	}
	if _, err := ParseCombo("cmd+bogus"); err == nil {
		t.Fatal("an unknown key must be refused")
	}
	presses, err := TextPresses("Hi!\n")
	if err != nil {
		t.Fatal(err)
	}
	want := []KeyState{{ModShift, []uint8{0x0B}}, {0, []uint8{0x0C}}, {ModShift, []uint8{0x1E}}, {0, []uint8{0x28}}}
	for i, p := range presses {
		if p.Mods != want[i].Mods || p.Keys[0] != want[i].Keys[0] {
			t.Fatalf("press %d: %+v, want %+v", i, p, want[i])
		}
	}
	if _, err := TextPresses("naïve"); err == nil || !strings.Contains(err.Error(), "ï") {
		t.Fatalf("non-ASCII text must be refused, naming the character: %v", err)
	}
}

func TestConsumerKeysFollowTheDescriptorOrder(t *testing.T) {
	if ConsumerKeys["volume_up"] != 1 || ConsumerKeys["volume_down"] != 2 || ConsumerKeys["mute"] != 4 {
		t.Fatal("volume keys must be the first bits (usages 0xE9, 0xEA, 0xE2)")
	}
	if ConsumerUsages[0] != 0xE9 || ConsumerUsages[1] != 0xEA || ConsumerUsages[2] != 0xE2 {
		t.Fatal("consumer usages out of order")
	}
	if len(ConsumerKeyNames()) != 24 {
		t.Fatal("24 consumer keys")
	}
}

// fakeKernel builds the directories the kernel would provide.
func fakeKernel(t *testing.T, udcs ...string) Paths {
	t.Helper()
	root := t.TempDir()
	p := Paths{ConfigFS: filepath.Join(root, "configfs"), SysFS: filepath.Join(root, "sys"), Dev: filepath.Join(root, "dev")}
	for _, d := range []string{p.ConfigFS, p.Dev} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	for _, u := range udcs {
		dir := filepath.Join(p.SysFS, "class", "udc", u)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "state"), []byte("not attached\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return p
}

func TestGadgetUpWritesTheConfirmedLayout(t *testing.T) {
	p := fakeKernel(t, "fc000000.usb")
	udc, err := p.GadgetUp(GadgetOptions{RemoteWakeup: true})
	if err != nil {
		t.Fatal(err)
	}
	if udc != "fc000000.usb" {
		t.Fatalf("udc %q", udc)
	}
	g := filepath.Join(p.ConfigFS, GadgetName)
	read := func(rel string) string { b, _ := os.ReadFile(filepath.Join(g, rel)); return string(b) }
	if read("UDC") != "fc000000.usb" || read("configs/c.1/bmAttributes") != "0xa0" ||
		read("strings/0x409/serialnumber") != "ihc-RA" || read("idVendor") != "0x1d6b" {
		t.Fatalf("attributes: UDC=%q bmAttributes=%q serial=%q", read("UDC"), read("configs/c.1/bmAttributes"),
			read("strings/0x409/serialnumber"))
	}
	if desc := read("functions/hid.absolute/report_desc"); desc != string(DescAbsolute) {
		t.Fatal("absolute pointer descriptor not written")
	}
	if got := p.GadgetFunctions(GadgetName); strings.Join(got, ",") != "keyboard,consumer,mouse,absolute" {
		t.Fatalf("functions %v", got)
	}
	if p.GadgetProfile(GadgetName) != "RA" {
		t.Fatal("profile not read back from the serial")
	}
	if _, err := p.GadgetUp(GadgetOptions{}); err == nil {
		t.Fatal("a second gadget of the same name must be refused")
	}
	// configfs removes a directory with its attribute files; a plain directory tree keeps them, so
	// here only the unbinding and the unlinking are checked
	if ok, _ := p.GadgetDown(GadgetName); !ok {
		t.Fatal("down: the gadget was not found")
	}
	if read("UDC") != "\n" {
		t.Fatalf("not unbound: UDC=%q", read("UDC"))
	}
	links, _ := filepath.Glob(filepath.Join(g, "configs", "c.1", "hid.*"))
	if len(links) != 0 {
		t.Fatalf("functions still linked: %v", links)
	}
}

func TestGadgetUpNeedsExactlyOneController(t *testing.T) {
	if _, err := fakeKernel(t).GadgetUp(GadgetOptions{}); err == nil || !strings.Contains(err.Error(), "host mode") {
		t.Fatalf("no controller: %v", err)
	}
	if _, err := fakeKernel(t, "a.usb", "b.usb").GadgetUp(GadgetOptions{}); err == nil || !strings.Contains(err.Error(), "--udc") {
		t.Fatalf("two controllers: %v", err)
	}
	p := fakeKernel(t, "a.usb")
	if err := os.MkdirAll(filepath.Join(p.ConfigFS, "adb"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(p.ConfigFS, "adb", "UDC"), []byte("a.usb\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := p.GadgetUp(GadgetOptions{}); err == nil || !strings.Contains(err.Error(), "adb") {
		t.Fatalf("controller in use: %v", err)
	}
}

func TestNodesFollowTheDeviceNumbers(t *testing.T) {
	p := fakeKernel(t, "u")
	if _, err := p.GadgetUp(GadgetOptions{Profile: "A"}); err != nil {
		t.Fatal(err)
	}
	for i, fn := range Profiles["A"] {
		majmin := "239:" + string(rune('0'+i))
		if err := os.WriteFile(filepath.Join(p.ConfigFS, GadgetName, "functions", "hid."+fn, "dev"), []byte(majmin+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
		dir := filepath.Join(p.SysFS, "dev", "char", majmin)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "uevent"), []byte("MAJOR=239\nDEVNAME=hidg"+string(rune('0'+i))+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	nodes := p.Nodes(GadgetName)
	if nodes["absolute"] != filepath.Join(p.Dev, "hidg2") || nodes["keyboard"] != filepath.Join(p.Dev, "hidg0") {
		t.Fatalf("nodes %v", nodes)
	}
}

// A gadget set up again makes new nodes: the sink must leave the old ones and open the new.
func TestReopeningFollowsAGadgetSetUpAgain(t *testing.T) {
	root := t.TempDir()
	p := Paths{ConfigFS: filepath.Join(root, "cfg"), SysFS: filepath.Join(root, "sys"), Dev: "/dev"}
	g := filepath.Join(p.ConfigFS, GadgetName)
	for dir, files := range map[string]map[string]string{
		filepath.Join(g, "configs", "c.1", "hid.absolute"): nil,
		filepath.Join(g, "strings", "0x409"):               {"serialnumber": "ihc-RA"},
		filepath.Join(g, "functions", "hid.absolute"):      {"dev": "9:9\n"},
		filepath.Join(p.SysFS, "dev", "char", "9:9"):       {"uevent": "DEVNAME=full\n"}, // every write fails
	} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		for name, content := range files {
			if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	r := NewReopening(p, GadgetName, 50*time.Millisecond)
	if err := r.Pointer(Pointer{}); err == nil {
		t.Fatal("a write to /dev/full succeeded")
	}
	// set up again: the function's node is now /dev/null, which takes every write
	if err := os.WriteFile(filepath.Join(p.SysFS, "dev", "char", "9:9", "uevent"), []byte("DEVNAME=null\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	var err error
	for i := 0; i < 3; i++ {
		if err = r.Pointer(Pointer{}); err == nil {
			return
		}
		time.Sleep(600 * time.Millisecond)
	}
	t.Fatalf("still writing to the old node: %v", err)
}

func TestMatchesComparesTheProfile(t *testing.T) {
	p := fakeKernel(t, "fc000000.usb")
	if p.Matches(GadgetOptions{}) {
		t.Fatal("no gadget yet")
	}
	if _, err := p.GadgetUp(GadgetOptions{RemoteWakeup: true}); err != nil {
		t.Fatal(err)
	}
	if !p.Matches(GadgetOptions{RemoteWakeup: true}) || !p.Matches(GadgetOptions{Profile: "RA", UDC: "fc000000.usb", RemoteWakeup: true}) {
		t.Fatal("the same gadget must match")
	}
	for _, o := range []GadgetOptions{{Profile: "A", RemoteWakeup: true}, {UDC: "other.usb", RemoteWakeup: true}, {}} {
		if p.Matches(o) {
			t.Errorf("%+v must not match", o)
		}
	}
}

func TestGadgetUpRefusesALegacyGadget(t *testing.T) {
	p := fakeKernel(t, "fc000000.usb")
	if err := os.WriteFile(filepath.Join(p.SysFS, "class", "udc", "fc000000.usb", "function"), []byte("g_ether\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := p.GadgetUp(GadgetOptions{}); err == nil || !strings.Contains(err.Error(), "modprobe -r g_ether") {
		t.Fatalf("%v", err)
	}
	if p.UDCFunction("fc000000.usb") != "g_ether" {
		t.Fatal("function not read")
	}
}
