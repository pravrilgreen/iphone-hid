// Package doctor examines a box and fixes what it can: the kernel's USB gadget support, the USB
// device port, the gadget and its nodes, the link to the iPhone, the HDMI input and its signal, the
// services, the API and its token. Each check says what it found and what to do about it.
//
// Fixes come in two kinds. Run-time fixes (load a module, set the gadget up, start a service) are
// applied with Options.Fix. A boot configuration change (the overlay that turns the HDMI input on)
// is applied only with Options.Boot, keeps a backup of the file it edits, and needs a reboot.
package doctor

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/board"
	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// Status of a check.
type Status string

// The statuses, from good to bad.
const (
	OK   Status = "ok"
	Info Status = "info"
	Warn Status = "warn"
	Fail Status = "fail"
)

// Result is one check.
type Result struct {
	ID     string   `json:"id"`
	Title  string   `json:"title"`
	Status Status   `json:"status"`
	Found  string   `json:"found"`
	Notes  []string `json:"notes,omitempty"`  // details behind Found
	Advice string   `json:"advice,omitempty"` // what a person does about it
	Fix    *Fix     `json:"fix,omitempty"`    // what doctor can do about it
	Done   string   `json:"done,omitempty"`   // after a fix: what doctor did
	Error  string   `json:"fix_error,omitempty"`
}

// Fix is a change doctor can make.
type Fix struct {
	What string `json:"what"`
	Boot bool   `json:"boot"` // edits the boot configuration: only with Options.Boot, takes effect at the next boot
	do   func() (string, error)
}

// Options choose what Run changes.
type Options struct {
	Fix  bool // apply the run-time fixes
	Boot bool // also apply boot configuration changes
}

// System is what the checks look at and act on. Tests replace every field.
type System struct {
	Root    string // "/" on the board
	Release string // the kernel release
	Service string // the service's user and group ("ihc")
	Port    int    // the API's port when /etc/default/ihc does not set one

	Command func(name string, args ...string) (string, error)
	Health  func(url string) error
	Signal  func(node string) (string, error)
	Owner   func(name string) (uid, gid int, err error) // a user's ids
	Addrs   func() []string
	Now     func() time.Time
}

// Local is the board doctor runs on.
func Local() System {
	var u syscall.Utsname
	_ = syscall.Uname(&u)
	var rel []byte
	for _, c := range u.Release {
		if c == 0 {
			break
		}
		rel = append(rel, byte(c))
	}
	return System{
		Root: "/", Release: string(rel), Service: "ihc", Port: 8000,
		Command: func(name string, args ...string) (string, error) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			out, err := exec.CommandContext(ctx, name, args...).CombinedOutput()
			return strings.TrimSpace(string(out)), err
		},
		Health: func(url string) error {
			c := http.Client{Timeout: 3 * time.Second}
			r, err := c.Get(url)
			if err != nil {
				return err
			}
			_, _ = io.Copy(io.Discard, r.Body)
			r.Body.Close()
			if r.StatusCode != http.StatusOK {
				return fmt.Errorf("status %d", r.StatusCode)
			}
			return nil
		},
		Signal: func(node string) (string, error) {
			t, err := video.QuerySignal(node)
			if err != nil {
				return "", err
			}
			return t.String(), nil
		},
		Owner: func(name string) (int, int, error) {
			u, err := user.Lookup(name)
			if err != nil {
				return 0, 0, err
			}
			uid, _ := strconv.Atoi(u.Uid)
			gid, _ := strconv.Atoi(u.Gid)
			return uid, gid, nil
		},
		Addrs: func() []string {
			var out []string
			as, _ := net.InterfaceAddrs()
			for _, a := range as {
				if n, ok := a.(*net.IPNet); ok && n.IP.To4() != nil && !n.IP.IsLoopback() && !n.IP.IsLinkLocalUnicast() {
					out = append(out, n.IP.String())
				}
			}
			return out
		},
		Now: time.Now,
	}
}

func (s System) path(p string) string { return filepath.Join(s.Root, p) }

func (s System) hid() hid.Paths {
	return hid.Paths{ConfigFS: s.path("sys/kernel/config/usb_gadget"), SysFS: s.path("sys"), Dev: s.path("dev")}
}

func (s System) exists(p string) bool { _, err := os.Stat(s.path(p)); return err == nil }

// Run checks the box; with opts.Fix it applies the fixes and checks again. The results are in the
// order of the checks, with what each fix did.
func Run(s System, opts Options) []Result {
	results := s.checks()
	if !opts.Fix {
		return results
	}
	done := map[string]Result{} // check ID -> the result whose fix was applied
	for round := 0; round < 3; round++ {
		applied := false
		for _, r := range results {
			if r.Fix == nil || (r.Fix.Boot && !opts.Boot) {
				continue
			}
			if _, ok := done[r.ID]; ok {
				continue
			}
			msg, err := r.Fix.do()
			if msg == "" {
				msg = r.Fix.What
			}
			r.Done = msg
			if err != nil {
				r.Error = err.Error()
			}
			done[r.ID] = r
			applied = true
		}
		if !applied {
			break
		}
		time.Sleep(settle)
		results = s.checks()
	}
	for i, r := range results {
		if d, ok := done[r.ID]; ok {
			results[i].Done, results[i].Error = d.Done, d.Error
			if d.Fix.Boot && r.Status != OK {
				results[i].Fix = nil // applied; the next boot tells
			}
		}
	}
	return results
}

// settle lets udev and the kernel catch up after a fix (tests set it to 0).
var settle = time.Second

func (s System) checks() []Result {
	var out []Result
	add := func(r *Result) {
		if r != nil {
			out = append(out, *r)
		}
	}
	add(s.board())
	add(s.gadgetSupport())
	add(s.usbPort())
	add(s.otherGadget())
	add(s.gadget())
	add(s.nodes())
	add(s.link())
	hdmi, node := s.hdmiInput()
	add(hdmi)
	add(s.hdmiSignal(node))
	installed := s.exists("etc/systemd/system/ihcd.service")
	add(s.services(installed))
	add(s.api(installed))
	add(s.token(installed))
	return out
}

// -- checks --------------------------------------------------------------------------------------------------

func (s System) board() *Result {
	r := &Result{ID: "board", Title: "Board"}
	model := board.Model(s.Root)
	desc := fmt.Sprintf("%s, kernel %s", runtime.GOARCH, s.Release)
	switch {
	case model == "":
		r.Status, r.Found = Info, "no device tree ("+desc+")"
		r.Advice = "the box needs a single-board computer with a USB device port and an HDMI input, such as the Orange Pi 5 Plus"
	case strings.Contains(model, "Orange Pi 5 Plus"):
		r.Status, r.Found = OK, model+" ("+desc+")"
	default:
		r.Status, r.Found = Info, model+" ("+desc+")"
		r.Notes = []string{"the box is tested on the Orange Pi 5 Plus; the checks below still apply"}
	}
	return r
}

// module says whether a kernel module is loaded, and whether this kernel has it at all.
func (s System) module(name string) (loaded, available bool) {
	if s.exists(filepath.Join("sys/module", name)) {
		return true, true
	}
	mods := s.path(filepath.Join("lib/modules", s.Release))
	for _, l := range strings.Split(readFile(filepath.Join(mods, "modules.builtin")), "\n") {
		if strings.HasSuffix(strings.TrimSpace(l), "/"+name+".ko") {
			return true, true
		}
	}
	_ = filepath.WalkDir(mods, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() && strings.HasPrefix(d.Name(), name+".ko") {
			available = true
			return filepath.SkipAll
		}
		return nil
	})
	return false, available
}

func (s System) configfsMounted() bool {
	for _, l := range strings.Split(readFile(s.path("proc/mounts")), "\n") {
		if f := strings.Fields(l); len(f) >= 3 && f[2] == "configfs" {
			return true
		}
	}
	return false
}

func (s System) gadgetSupport() *Result {
	r := &Result{ID: "gadget-support", Title: "Gadget support"}
	_, hidAvail := s.module("usb_f_hid")
	if s.exists("sys/kernel/config/usb_gadget") {
		if !hidAvail {
			r.Status, r.Found = Fail, "the kernel has the USB gadget framework but no HID function (usb_f_hid)"
			r.Advice = "use a kernel built with CONFIG_USB_CONFIGFS_F_HID: the board's vendor image (Orange Pi Ubuntu or Debian, or Armbian's vendor kernel)"
			return r
		}
		r.Status, r.Found = OK, "USB gadget framework loaded, HID function available"
		return r
	}
	_, compAvail := s.module("libcomposite")
	if !compAvail {
		r.Status, r.Found = Fail, "this kernel has no USB gadget framework (libcomposite)"
		r.Advice = "use a kernel built with CONFIG_USB_LIBCOMPOSITE and CONFIG_USB_CONFIGFS_F_HID: the board's vendor image (Orange Pi Ubuntu or Debian, or Armbian's vendor kernel)"
		return r
	}
	r.Status, r.Found = Fail, "the USB gadget framework is not loaded"
	mounted := s.configfsMounted()
	what := "load it: modprobe libcomposite"
	if !mounted {
		what = "mount configfs on /sys/kernel/config and load the gadget framework: modprobe libcomposite"
	}
	r.Fix = &Fix{What: what, do: func() (string, error) {
		if !mounted {
			if out, err := s.Command("mount", "-t", "configfs", "none", "/sys/kernel/config"); err != nil {
				return "", fmt.Errorf("mount configfs: %v %s", err, out)
			}
		}
		if out, err := s.Command("modprobe", "libcomposite"); err != nil {
			return "", fmt.Errorf("modprobe libcomposite: %v %s", err, out)
		}
		return "", nil
	}}
	return r
}

func (s System) usbPort() *Result {
	r := &Result{ID: "usb-port", Title: "USB device port"}
	udcs := s.hid().ListUDCs()
	switches := board.RoleSwitches(s.Root)
	for _, c := range board.USBControllers(s.Root) {
		r.Notes = append(r.Notes, fmt.Sprintf("device tree %s: dr_mode %s", c.Node, c.DRMode))
	}
	for _, sw := range switches {
		r.Notes = append(r.Notes, fmt.Sprintf("role switch %s: %s", sw.Name, orNone(sw.Role)))
	}
	if len(udcs) > 0 {
		r.Status, r.Found = OK, "device controller "+strings.Join(udcs, ", ")
		r.Notes = nil
		return r
	}
	r.Status, r.Found = Fail, "no USB device controller: the board's USB-C port works as a host only"
	for _, sw := range switches {
		if sw.Role != "host" && sw.Role != "none" {
			continue
		}
		sw := sw
		file := filepath.Join("/sys/class/usb_role", sw.Name, "role")
		r.Fix = &Fix{What: "switch " + sw.Name + " to device mode (echo device > " + file + ")", do: func() (string, error) {
			return "", os.WriteFile(s.path(file), []byte("device"), 0o644)
		}}
		r.Advice = "if the port needs this at every boot, the image's Type-C settings keep it in host mode: " +
			"the Orange Pi image allows device mode on the Type-C port next to the USB 3 ports"
		return r
	}
	host := false
	for _, c := range board.USBControllers(s.Root) {
		host = host || c.DRMode == "host"
	}
	switch {
	case host:
		r.Advice = "the device tree keeps the port in host mode (dr_mode host): it needs a device tree overlay that sets dr_mode to otg or peripheral, or the Orange Pi image, which allows device mode on the Type-C port next to the USB 3 ports"
		if ovs := board.USBOverlays(s.Root); len(ovs) > 0 {
			r.Notes = append(r.Notes, "overlays in this image that may do it: "+strings.Join(ovs, ", "))
		}
	default:
		r.Advice = "no USB controller of this kernel acts as a device: use the Orange Pi image, or Armbian with the vendor kernel"
	}
	return r
}

func (s System) otherGadget() *Result {
	p := s.hid()
	r := &Result{ID: "other-gadget", Title: "Other gadgets"}
	for udc, g := range p.UDCUsers() {
		if g == hid.GadgetName {
			continue
		}
		r.Status, r.Found = Warn, fmt.Sprintf("the device controller %s is used by the gadget %q (often ADB)", udc, g)
		file := filepath.Join(p.ConfigFS, g, "UDC")
		r.Fix = &Fix{What: fmt.Sprintf("unbind %q from %s until the next boot", g, udc), do: func() (string, error) {
			return "", os.WriteFile(file, []byte("\n"), 0o644)
		}}
		if units := s.gadgetUnits(); len(units) > 0 {
			r.Advice = "it is set up at boot by " + strings.Join(units, ", ") + ": sudo systemctl disable " + strings.Join(units, " ")
		} else {
			r.Advice = "find what sets it up at boot (systemctl list-units | grep -i -e adb -e gadget) and disable it"
		}
		return r
	}
	if len(p.ListUDCs()) == 0 {
		return nil
	}
	r.Status, r.Found = OK, "none"
	return r
}

// gadgetUnits lists systemd units of the image that set up a USB gadget of their own.
func (s System) gadgetUnits() []string {
	var out []string
	seen := map[string]bool{}
	for _, d := range []string{"etc/systemd/system", "lib/systemd/system", "usr/lib/systemd/system"} {
		entries, _ := os.ReadDir(s.path(d))
		for _, e := range entries {
			n := strings.ToLower(e.Name())
			if !strings.HasSuffix(n, ".service") || strings.HasPrefix(n, "ihcd") || seen[n] {
				continue
			}
			if strings.Contains(n, "adbd") || strings.Contains(n, "usbdevice") || strings.Contains(n, "usb-gadget") ||
				strings.Contains(n, "usbgadget") {
				seen[n] = true
				out = append(out, e.Name())
			}
		}
	}
	return out
}

func (s System) gadget() *Result {
	p := s.hid()
	r := &Result{ID: "gadget", Title: "Gadget"}
	st := p.Status(hid.GadgetName)
	switch {
	case st.Exists && st.UDC != "":
		r.Status, r.Found = OK, fmt.Sprintf("profile %s on %s: %s", orNone(st.Profile), st.UDC, strings.Join(st.Functions, ", "))
		return r
	case !s.exists("sys/kernel/config/usb_gadget") || len(p.ListUDCs()) == 0:
		r.Status, r.Found = Fail, "not set up"
		r.Advice = "fix the gadget support and the USB device port first"
		return r
	case st.Exists:
		r.Status, r.Found = Fail, "set up but not bound to a device controller"
	default:
		r.Status, r.Found = Fail, "not set up"
	}
	for udc, g := range p.UDCUsers() {
		if g != hid.GadgetName {
			r.Advice = fmt.Sprintf("the gadget %q holds %s: see Other gadgets", g, udc)
			return r
		}
	}
	if s.exists("etc/systemd/system/ihcd-gadget.service") {
		r.Fix = &Fix{What: "set it up: systemctl restart ihcd-gadget", do: func() (string, error) {
			out, err := s.Command("systemctl", "restart", "ihcd-gadget")
			if err != nil {
				return "", fmt.Errorf("%v %s", err, out)
			}
			return "", nil
		}}
		return r
	}
	r.Fix = &Fix{What: "set it up: ihcd gadget up --replace", do: func() (string, error) {
		if st.Exists {
			if _, err := p.GadgetDown(hid.GadgetName); err != nil {
				return "", err
			}
		}
		udc, err := p.GadgetUp(hid.GadgetOptions{RemoteWakeup: true})
		if err != nil {
			return "", err
		}
		return "set the gadget up on " + udc, nil
	}}
	return r
}

func (s System) nodes() *Result {
	p := s.hid()
	if p.BoundUDC(hid.GadgetName) == "" {
		return nil
	}
	r := &Result{ID: "nodes", Title: "HID nodes"}
	nodes := p.Nodes(hid.GadgetName)
	if len(nodes) == 0 {
		r.Status, r.Found = Warn, "the gadget is up but /dev has no hidg nodes for it"
		r.Advice = "udev has not made them yet: sudo udevadm trigger, then check again"
		return r
	}
	_, gid, err := s.Owner(s.Service)
	if err != nil {
		r.Status, r.Found = Info, fmt.Sprintf("%d nodes; no user %q (the box software is not installed)", len(nodes), s.Service)
		return r
	}
	var wrong []string
	for _, n := range nodes {
		st, err := os.Stat(n)
		if err != nil {
			wrong = append(wrong, n)
			continue
		}
		sys, ok := st.Sys().(*syscall.Stat_t)
		if !ok || int(sys.Gid) != gid || st.Mode().Perm()&0o060 != 0o060 {
			wrong = append(wrong, n)
		}
	}
	if len(wrong) == 0 {
		r.Status, r.Found = OK, fmt.Sprintf("%d nodes, readable and writable by the service", len(nodes))
	} else {
		r.Status, r.Found = Warn, "the service cannot write "+strings.Join(wrong, ", ")
		r.Fix = &Fix{What: "give them to the group " + s.Service + " (chgrp, chmod 660)", do: func() (string, error) {
			for _, n := range wrong {
				if err := os.Chown(n, -1, gid); err != nil {
					return "", err
				}
				if err := os.Chmod(n, 0o660); err != nil {
					return "", err
				}
			}
			return "", nil
		}}
	}
	if !s.exists("etc/udev/rules.d/99-ihc.rules") {
		r.Notes = append(r.Notes, "no /etc/udev/rules.d/99-ihc.rules: reinstall the bundle so the permissions survive a reboot")
		if r.Status == OK {
			r.Status = Warn
		}
	}
	return r
}

func (s System) link() *Result {
	p := s.hid()
	udc := p.BoundUDC(hid.GadgetName)
	if udc == "" {
		return nil
	}
	r := &Result{ID: "iphone", Title: "iPhone"}
	state := p.UDCState(udc)
	switch state {
	case "configured":
		r.Status, r.Found = OK, "connected: the iPhone has taken the touch pointer and keyboard"
	case "suspended":
		r.Status, r.Found = Warn, "asleep: the iPhone suspended the USB bus"
		r.Advice = "press Wake in the console (or sudo ihcd gadget wake) and set Auto-Lock to Never on the iPhone"
	case "not attached", "":
		r.Status, r.Found = Warn, "nothing is attached to the USB device port"
		r.Advice = "connect the hub's USB-A port to the board's Type-C port next to the USB 3 ports (USB-A to USB-C data cable), and the iPhone to the hub"
	default:
		r.Status, r.Found = Warn, "the iPhone sees the box but has not accepted it ("+state+")"
		r.Advice = "unlock the iPhone and tap Allow for the accessory"
	}
	return r
}

func (s System) hdmiInput() (*Result, string) {
	r := &Result{ID: "hdmi-input", Title: "HDMI input"}
	if node := board.HDMIInput(s.Root); node != "" {
		r.Status, r.Found = OK, node
		return r, node
	}
	lines := board.DiagnoseHDMI(s.Root, s.Release)
	r.Status, r.Found = Fail, "the board shows no HDMI input"
	for _, l := range lines {
		if v, ok := strings.CutPrefix(l, "=> "); ok {
			r.Advice = v
		} else if !strings.HasPrefix(l, "kernel ") {
			r.Notes = append(r.Notes, l)
		}
	}
	if f := board.HDMIBootFix(s.Root); f != nil {
		fix := *f
		r.Fix = &Fix{What: fix.String() + " (a backup of the file is kept); then reboot", Boot: true, do: func() (string, error) {
			backup, err := fix.Apply(s.Root, s.Now())
			if err != nil {
				return "", err
			}
			rel, _ := filepath.Rel(s.Root, backup)
			return fix.String() + "; the old file is /" + rel + ". Reboot to turn the HDMI input on", nil
		}}
	}
	return r, ""
}

func (s System) hdmiSignal(node string) *Result {
	if node == "" {
		return nil
	}
	r := &Result{ID: "hdmi-signal", Title: "Picture"}
	t, err := s.Signal(node)
	if err == nil {
		r.Status, r.Found = OK, "receiving "+t
		return r
	}
	r.Status, r.Found = Warn, "no picture: "+err.Error()
	r.Advice = "unlock the iPhone and check that the hub's HDMI cable goes into the board's HDMI IN port; " +
		"replug the hub. iPhone 16e and 17e have no video output"
	return r
}

var units = []string{"ihcd-gadget", "ihcd"}

func (s System) services(installed bool) *Result {
	r := &Result{ID: "service", Title: "Service"}
	if !installed {
		r.Status, r.Found = Warn, "the box software is not installed as a service"
		r.Advice = "install the bundle: sudo sh ihc-box-<version>-linux-arm64.run"
		return r
	}
	var bad, disabled []string
	for _, u := range units {
		if out, _ := s.Command("systemctl", "is-enabled", u); strings.TrimSpace(out) != "enabled" {
			disabled = append(disabled, u)
		}
		if out, _ := s.Command("systemctl", "is-active", u); strings.TrimSpace(out) != "active" {
			bad = append(bad, u)
			if log, _ := s.Command("journalctl", "-u", u, "-n", "3", "--no-pager", "-o", "cat"); log != "" {
				for _, l := range strings.Split(log, "\n") {
					r.Notes = append(r.Notes, u+": "+l)
				}
			}
		}
	}
	if len(bad) == 0 && len(disabled) == 0 {
		r.Status, r.Found = OK, "ihcd and ihcd-gadget enabled and running"
		return r
	}
	r.Status = Fail
	var found []string
	if len(bad) > 0 {
		found = append(found, "not running: "+strings.Join(bad, ", "))
	}
	if len(disabled) > 0 {
		found = append(found, "not started at boot: "+strings.Join(disabled, ", "))
	}
	r.Found = strings.Join(found, "; ")
	what := []string{}
	if len(disabled) > 0 {
		what = append(what, "systemctl enable "+strings.Join(disabled, " "))
	}
	if len(bad) > 0 {
		what = append(what, "systemctl restart "+strings.Join(bad, " "))
	}
	r.Fix = &Fix{What: strings.Join(what, "; "), do: func() (string, error) {
		if len(disabled) > 0 {
			if out, err := s.Command("systemctl", append([]string{"enable"}, disabled...)...); err != nil {
				return "", fmt.Errorf("%v %s", err, out)
			}
		}
		if len(bad) > 0 {
			if out, err := s.Command("systemctl", append([]string{"restart"}, bad...)...); err != nil {
				return "", fmt.Errorf("%v %s", err, out)
			}
		}
		return "", nil
	}}
	return r
}

// port is the API's port: --addr in IHCD_ARGS of /etc/default/ihc, else the default.
func (s System) port() int {
	for _, l := range strings.Split(readFile(s.path("etc/default/ihc")), "\n") {
		l = strings.TrimSpace(l)
		if strings.HasPrefix(l, "#") || !strings.HasPrefix(l, "IHCD_ARGS=") {
			continue
		}
		f := strings.Fields(strings.Trim(strings.TrimPrefix(l, "IHCD_ARGS="), `"'`))
		for i, a := range f {
			v, ok := strings.CutPrefix(a, "--addr=")
			if !ok && (a == "--addr" || a == "-addr") && i+1 < len(f) {
				v, ok = f[i+1], true
			}
			if ok {
				if _, p, err := net.SplitHostPort(v); err == nil {
					if n, err := strconv.Atoi(p); err == nil {
						return n
					}
				}
			}
		}
	}
	return s.Port
}

func (s System) api(installed bool) *Result {
	if !installed {
		return nil
	}
	port := s.port()
	r := &Result{ID: "api", Title: "Console and API"}
	var err error
	for try := 0; try < 3; try++ { // a service started a moment ago may not listen yet
		if err = s.Health(fmt.Sprintf("http://127.0.0.1:%d/api/health", port)); err == nil {
			break
		}
		time.Sleep(settle / 2)
	}
	if err != nil {
		r.Status, r.Found = Fail, fmt.Sprintf("nothing answers on port %d: %v", port, err)
		r.Fix = &Fix{What: "restart the service: systemctl restart ihcd", do: func() (string, error) {
			out, err := s.Command("systemctl", "restart", "ihcd")
			if err != nil {
				return "", fmt.Errorf("%v %s", err, out)
			}
			return "", nil
		}}
		r.Advice = "if it keeps failing: journalctl -u ihcd -n 50"
		return r
	}
	var urls []string
	for _, a := range s.Addrs() {
		urls = append(urls, fmt.Sprintf("http://%s:%d", a, port))
	}
	r.Status, r.Found = OK, fmt.Sprintf("answers on port %d", port)
	if len(urls) > 0 {
		r.Found += ": " + strings.Join(urls, ", ")
	}
	return r
}

func (s System) token(installed bool) *Result {
	if !installed {
		return nil
	}
	r := &Result{ID: "token", Title: "API token"}
	file := s.path("var/lib/ihc/token")
	st, err := os.Stat(file)
	if err != nil || st.Size() == 0 {
		r.Status, r.Found = Warn, "no token in /var/lib/ihc/token"
		r.Fix = &Fix{What: "let the service make one: systemctl restart ihcd", do: func() (string, error) {
			out, err := s.Command("systemctl", "restart", "ihcd")
			if err != nil {
				return "", fmt.Errorf("%v %s", err, out)
			}
			return "", nil
		}}
		return r
	}
	uid, gid, err := s.Owner(s.Service)
	if err != nil {
		r.Status, r.Found = Warn, fmt.Sprintf("no user %q to own the token", s.Service)
		r.Advice = "reinstall the bundle"
		return r
	}
	sys, _ := st.Sys().(*syscall.Stat_t)
	if st.Mode().Perm() != 0o600 || sys == nil || int(sys.Uid) != uid {
		r.Status, r.Found = Warn, fmt.Sprintf("/var/lib/ihc/token is %04o: others may read it, or the service cannot", st.Mode().Perm())
		r.Fix = &Fix{What: "chown " + s.Service + " and chmod 600 /var/lib/ihc/token", do: func() (string, error) {
			if err := os.Chown(file, uid, gid); err != nil {
				return "", err
			}
			return "", os.Chmod(file, 0o600)
		}}
		return r
	}
	r.Status, r.Found = OK, "readable by the service only (sudo cat /var/lib/ihc/token)"
	return r
}

// -- report ---------------------------------------------------------------------------------------------------

// Worst is the worst status of the results.
func Worst(rs []Result) Status {
	w := OK
	rank := map[Status]int{OK: 0, Info: 0, Warn: 1, Fail: 2}
	for _, r := range rs {
		if rank[r.Status] > rank[w] {
			w = r.Status
		}
	}
	return w
}

// Report writes the results for a person.
func Report(w io.Writer, rs []Result, opts Options) {
	labels := map[Status]string{OK: "ok", Info: "info", Warn: "WARN", Fail: "FAIL"}
	pad := strings.Repeat(" ", 25)
	var fails, warns, fixable, boot int
	for _, r := range rs {
		fmt.Fprintf(w, "  %-5s %-17s %s\n", labels[r.Status], r.Title, r.Found)
		for _, n := range r.Notes {
			fmt.Fprintf(w, "%s%s\n", pad, n)
		}
		if r.Done != "" {
			if r.Error != "" {
				fmt.Fprintf(w, "%stried: %s: %s\n", pad, r.Done, r.Error)
			} else if r.Status != OK {
				fmt.Fprintf(w, "%sdone: %s\n", pad, r.Done)
			} else {
				fmt.Fprintf(w, "%sfixed: %s\n", pad, r.Done)
			}
		}
		if r.Status == OK || r.Status == Info {
			continue
		}
		switch {
		case r.Status == Fail:
			fails++
		case r.Status == Warn:
			warns++
		}
		if r.Fix != nil && r.Done == "" {
			flag := "--fix"
			if r.Fix.Boot {
				flag = "--fix-boot"
				boot++
			} else {
				fixable++
			}
			fmt.Fprintf(w, "%sfix (sudo ihcd doctor %s): %s\n", pad, flag, r.Fix.What)
		}
		if r.Advice != "" {
			fmt.Fprintf(w, "%s-> %s\n", pad, r.Advice)
		}
	}
	fmt.Fprintln(w)
	switch {
	case fails == 0 && warns == 0:
		fmt.Fprintln(w, "All good: the box is ready.")
		return
	case fails == 0:
		fmt.Fprintf(w, "No problems, %s.\n", plural(warns, "warning"))
	default:
		fmt.Fprintf(w, "%s, %s.\n", plural(fails, "problem"), plural(warns, "warning"))
	}
	if fixable > 0 {
		fmt.Fprintf(w, "sudo ihcd doctor --fix fixes %d of them.\n", fixable)
	}
	if boot > 0 {
		fmt.Fprintln(w, "sudo ihcd doctor --fix-boot also changes the boot configuration for the HDMI input (it keeps a backup); reboot after it.")
	}
}

func plural(n int, word string) string {
	if n == 1 {
		return "1 " + word
	}
	return strconv.Itoa(n) + " " + word + "s"
}

func orNone(s string) string {
	if s == "" {
		return "none"
	}
	return s
}

func readFile(p string) string {
	b, _ := os.ReadFile(p)
	return string(b)
}

// ErrNotRoot is returned when a fix is asked for without root.
var ErrNotRoot = errors.New("fixing needs root: sudo ihcd doctor --fix")
