package hid

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"time"
)

// Paths of the kernel interfaces. Tests point them at a directory tree of their own.
type Paths struct {
	ConfigFS string // /sys/kernel/config/usb_gadget
	SysFS    string // /sys
	Dev      string // /dev
}

// SystemPaths are the real kernel interfaces.
var SystemPaths = Paths{ConfigFS: "/sys/kernel/config/usb_gadget", SysFS: "/sys", Dev: "/dev"}

// GadgetName is the configfs name of the box's gadget.
const GadgetName = "ihc"

// GadgetOptions describe the gadget to set up.
type GadgetOptions struct {
	Name         string // default GadgetName
	Profile      string // default DefaultProfile
	UDC          string // default: the board's only USB device controller
	Vendor       uint16 // default 0x1d6b (Linux Foundation)
	Product      uint16 // default 0x0104 (Multifunction Composite Gadget)
	Serial       string // default "ihc-<profile>"
	RemoteWakeup bool   // the gadget may wake a host that suspended the bus (a sleeping iPhone)
}

// GadgetStatus is what configfs and sysfs say about the gadget.
type GadgetStatus struct {
	Name      string            `json:"name"`
	Exists    bool              `json:"exists"`
	Profile   string            `json:"profile,omitempty"`
	UDC       string            `json:"udc,omitempty"`
	State     string            `json:"state,omitempty"` // "configured" once the phone enumerated it
	Functions []string          `json:"functions"`
	Nodes     map[string]string `json:"nodes"`
	UDCs      []string          `json:"udcs"`
	UDCUsers  map[string]string `json:"udc_users"` // UDC -> gadget bound to it
}

// ListUDCs returns the board's USB device controllers (none: no port can act as a USB device).
func (p Paths) ListUDCs() []string {
	entries, err := os.ReadDir(filepath.Join(p.SysFS, "class", "udc"))
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		out = append(out, e.Name())
	}
	sort.Strings(out)
	return out
}

// UDCState reads /sys/class/udc/<udc>/state: "configured", "not attached", "suspended", ...
func (p Paths) UDCState(udc string) string {
	if udc == "" {
		return ""
	}
	return readTrim(filepath.Join(p.SysFS, "class", "udc", udc, "state"))
}

// BoundUDC returns the UDC a gadget is bound to ("" if none).
func (p Paths) BoundUDC(name string) string {
	return readTrim(filepath.Join(p.ConfigFS, name, "UDC"))
}

// UDCUsers maps each bound UDC to the configfs gadget using it (another one may be the board's ADB).
func (p Paths) UDCUsers() map[string]string {
	out := map[string]string{}
	entries, _ := os.ReadDir(p.ConfigFS)
	for _, e := range entries {
		if udc := p.BoundUDC(e.Name()); udc != "" {
			out[udc] = e.Name()
		}
	}
	return out
}

// GadgetProfile is the profile the gadget was set up with, from its serial number ihc-<profile>.
func (p Paths) GadgetProfile(name string) string {
	serial := readTrim(filepath.Join(p.ConfigFS, name, "strings", "0x409", "serialnumber"))
	prof := strings.TrimPrefix(serial, "ihc-")
	if _, ok := Profiles[prof]; ok && prof != serial {
		return prof
	}
	return ""
}

// GadgetFunctions lists the HID functions linked into the gadget's configuration, in interface order.
func (p Paths) GadgetFunctions(name string) []string {
	entries, err := os.ReadDir(filepath.Join(p.ConfigFS, name, "configs", "c.1"))
	if err != nil {
		return nil
	}
	present := map[string]bool{}
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "hid.") {
			present[strings.TrimPrefix(e.Name(), "hid.")] = true
		}
	}
	var out []string
	for _, fn := range Profiles[p.GadgetProfile(name)] {
		if present[fn] {
			out = append(out, fn)
			delete(present, fn)
		}
	}
	for _, fn := range []string{"keyboard", "consumer", "mouse", "absolute"} {
		if present[fn] {
			out = append(out, fn)
		}
	}
	return out
}

// Nodes maps each function of the gadget to its /dev/hidgN node.
func (p Paths) Nodes(name string) map[string]string {
	out := map[string]string{}
	for _, fn := range p.GadgetFunctions(name) {
		majmin := readTrim(filepath.Join(p.ConfigFS, name, "functions", "hid."+fn, "dev"))
		if majmin == "" {
			continue
		}
		uevent, err := os.ReadFile(filepath.Join(p.SysFS, "dev", "char", majmin, "uevent"))
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(uevent), "\n") {
			if v, ok := strings.CutPrefix(line, "DEVNAME="); ok {
				out[fn] = filepath.Join(p.Dev, strings.TrimSpace(v))
			}
		}
	}
	return out
}

// Status reports the gadget and the board's USB device controllers.
func (p Paths) Status(name string) GadgetStatus {
	udc := p.BoundUDC(name)
	st := GadgetStatus{
		Name:      name,
		Profile:   p.GadgetProfile(name),
		UDC:       udc,
		State:     p.UDCState(udc),
		Functions: p.GadgetFunctions(name),
		Nodes:     p.Nodes(name),
		UDCs:      p.ListUDCs(),
		UDCUsers:  p.UDCUsers(),
	}
	_, err := os.Stat(filepath.Join(p.ConfigFS, name))
	st.Exists = err == nil
	if st.Functions == nil {
		st.Functions = []string{}
	}
	return st
}

// GadgetUp creates the HID gadget and binds it to a USB device controller (needs root).
// It returns the controller's name.
func (p Paths) GadgetUp(o GadgetOptions) (string, error) {
	if o.Name == "" {
		o.Name = GadgetName
	}
	if o.Profile == "" {
		o.Profile = DefaultProfile
	}
	fns, ok := Profiles[o.Profile]
	if !ok {
		return "", fmt.Errorf("unknown profile %q (one of %s)", o.Profile, strings.Join(profileNames(), ", "))
	}
	if o.Vendor == 0 {
		o.Vendor = 0x1d6b
	}
	if o.Product == 0 {
		o.Product = 0x0104
	}
	if o.Serial == "" {
		o.Serial = "ihc-" + o.Profile
	}
	if _, err := os.Stat(p.ConfigFS); err != nil {
		return "", fmt.Errorf("%s not found: load the gadget framework first (modprobe libcomposite)", p.ConfigFS)
	}
	g := filepath.Join(p.ConfigFS, o.Name)
	if _, err := os.Stat(g); err == nil {
		return "", fmt.Errorf("a gadget named %q already exists: remove it first (ihcd gadget down)", o.Name)
	}
	udcs := p.ListUDCs()
	switch {
	case o.UDC == "" && len(udcs) == 0:
		return "", errors.New("no USB device controller on this board: the USB-C port is in host mode " +
			"(its device-tree node must allow peripheral or OTG mode)")
	case o.UDC == "" && len(udcs) > 1:
		return "", fmt.Errorf("several USB device controllers (%s): choose one with --udc", strings.Join(udcs, ", "))
	case o.UDC == "":
		o.UDC = udcs[0]
	case len(udcs) > 0 && !contains(udcs, o.UDC):
		return "", fmt.Errorf("no USB device controller %q; this board has: %s", o.UDC, strings.Join(udcs, ", "))
	}
	if user := p.UDCUsers()[o.UDC]; user != "" {
		return "", fmt.Errorf("%s is used by the gadget %q (often ADB): unbind it first (echo '' > %s)",
			o.UDC, user, filepath.Join(p.ConfigFS, user, "UDC"))
	}
	attrs := byte(0x80) // bit 7 is always set
	if o.RemoteWakeup {
		attrs |= 0x20
	}
	err := func() error {
		if err := os.Mkdir(g, 0o755); err != nil {
			return err
		}
		strs := filepath.Join(g, "strings", "0x409")
		conf := filepath.Join(g, "configs", "c.1")
		steps := []struct {
			path  string
			value string
		}{
			{filepath.Join(g, "idVendor"), fmt.Sprintf("0x%04x", o.Vendor)},
			{filepath.Join(g, "idProduct"), fmt.Sprintf("0x%04x", o.Product)},
			{filepath.Join(g, "bcdDevice"), "0x0100"},
			{filepath.Join(g, "bcdUSB"), "0x0200"},
		}
		for _, s := range steps {
			if err := writeAttr(s.path, s.value); err != nil {
				return err
			}
		}
		if _, err := os.Stat(filepath.Join(g, "max_speed")); err == nil {
			if err := writeAttr(filepath.Join(g, "max_speed"), "high-speed"); err != nil {
				return err
			}
		}
		if err := os.MkdirAll(strs, 0o755); err != nil {
			return err
		}
		for _, s := range [][2]string{{"manufacturer", "iphone-hid"}, {"product", "iphone-hid touch + keyboard"},
			{"serialnumber", o.Serial}} {
			if err := writeAttr(filepath.Join(strs, s[0]), s[1]); err != nil {
				return err
			}
		}
		if err := os.MkdirAll(filepath.Join(conf, "strings", "0x409"), 0o755); err != nil {
			return err
		}
		if err := writeAttr(filepath.Join(conf, "strings", "0x409", "configuration"), "touch + keyboard"); err != nil {
			return err
		}
		if err := writeAttr(filepath.Join(conf, "bmAttributes"), fmt.Sprintf("0x%02x", attrs)); err != nil {
			return err
		}
		if err := writeAttr(filepath.Join(conf, "MaxPower"), "100"); err != nil {
			return err
		}
		for _, name := range fns {
			spec := Functions[name]
			f := filepath.Join(g, "functions", "hid."+name)
			if err := os.MkdirAll(f, 0o755); err != nil {
				if errors.Is(err, syscall.ENODEV) {
					return fmt.Errorf("the kernel allows %d HID gadget functions in all and none is left for %q: "+
						"another gadget may hold some (ihcd gadget status)", MaxFunctions, name)
				}
				return err
			}
			for _, s := range [][2]string{{"protocol", fmt.Sprint(spec.Protocol)}, {"subclass", fmt.Sprint(spec.Subclass)},
				{"report_length", fmt.Sprint(spec.ReportLength)}} {
				if err := writeAttr(filepath.Join(f, s[0]), s[1]); err != nil {
					return err
				}
			}
			if err := writeAttrBytes(filepath.Join(f, "report_desc"), spec.Descriptor); err != nil {
				return err
			}
			if !spec.OutReports {
				if _, err := os.Stat(filepath.Join(f, "no_out_endpoint")); err == nil {
					if err := writeAttr(filepath.Join(f, "no_out_endpoint"), "1"); err != nil {
						return err
					}
				}
			}
			if err := os.Symlink(f, filepath.Join(conf, "hid."+name)); err != nil {
				return err
			}
		}
		return writeAttr(filepath.Join(g, "UDC"), o.UDC)
	}()
	if err != nil {
		_, _ = p.GadgetDown(o.Name)
		return "", fmt.Errorf("cannot set up the gadget: %w", err)
	}
	return o.UDC, nil
}

// GadgetDown unbinds and removes the gadget (needs root). False if there was none.
func (p Paths) GadgetDown(name string) (bool, error) {
	if name == "" {
		name = GadgetName
	}
	g := filepath.Join(p.ConfigFS, name)
	if _, err := os.Stat(g); err != nil {
		return false, nil
	}
	if p.BoundUDC(name) != "" {
		if err := writeAttr(filepath.Join(g, "UDC"), "\n"); err != nil {
			return true, err
		}
	}
	conf := filepath.Join(g, "configs", "c.1")
	if entries, err := os.ReadDir(conf); err == nil {
		for _, e := range entries {
			if e.Type()&fs.ModeSymlink != 0 {
				_ = os.Remove(filepath.Join(conf, e.Name()))
			}
		}
		_ = os.Remove(filepath.Join(conf, "strings", "0x409"))
		_ = os.Remove(conf)
	}
	if entries, err := os.ReadDir(filepath.Join(g, "functions")); err == nil {
		for _, e := range entries {
			_ = os.Remove(filepath.Join(g, "functions", e.Name()))
		}
	}
	_ = os.Remove(filepath.Join(g, "strings", "0x409"))
	if err := os.Remove(g); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return true, err
	}
	return true, nil
}

// Wake signals USB remote wakeup to a host that suspended the bus (a sleeping iPhone), then waits
// up to timeout for the bus to be in use again. It returns the USB state before and after. The
// controller signals it only while the link is suspended; the phone's screen stays off until it
// gets input.
func (p Paths) Wake(udc string, timeout time.Duration) (before, after string, err error) {
	if udc == "" {
		return "", "", errors.New("the gadget is not bound to a USB device controller (ihcd gadget up)")
	}
	before = p.UDCState(udc)
	srp := filepath.Join(p.SysFS, "class", "udc", udc, "srp")
	if err := writeAttr(srp, "1"); err != nil {
		return before, before, fmt.Errorf("cannot signal remote wakeup through %s: %w", srp, err)
	}
	deadline := time.Now().Add(timeout)
	after = p.UDCState(udc)
	for after != "configured" && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
		after = p.UDCState(udc)
	}
	return before, after, nil
}

func profileNames() []string {
	var n []string
	for k := range Profiles {
		n = append(n, k)
	}
	sort.Strings(n)
	return n
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func readTrim(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// configfs wants each value in one write.
func writeAttr(path, value string) error { return writeAttrBytes(path, []byte(value)) }

func writeAttrBytes(path string, value []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	if _, err := f.Write(value); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}
