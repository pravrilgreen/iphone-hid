// Package doctor examines a box and fixes what it can: the kernel's USB gadget support, the USB
// device port, the gadget and its nodes, the link to the iPhone, the HDMI input and its signal, the
// services, the API and its token. Each check says what it found and what to do about it.
//
// Fixes come in two kinds. Run-time fixes (load a module, set the gadget up, start a service) are
// applied with Options.Fix. Some of them are disruptive: they may cut off something else that uses
// the USB port (unbind another gadget, switch the port's USB role); Options.SafeOnly leaves those
// out. A boot configuration change (the overlay that turns the HDMI input on) is applied only with
// Options.Boot, keeps a backup of each file it replaces, and needs a reboot.
package doctor

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
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
	What       string `json:"what"`
	Boot       bool   `json:"boot"`          // edits the boot configuration: only with Options.Boot, takes effect at the next boot
	Disruptive bool   `json:"disruptive"`    // may cut off something else: not with Options.SafeOnly
	Why        string `json:"why,omitempty"` // why it is disruptive
	do         func() (string, error)
}

// Options choose what Run changes.
type Options struct {
	Fix      bool // apply the run-time fixes
	SafeOnly bool // with Fix: only the fixes that are not disruptive
	Boot     bool // also apply boot configuration changes
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

	restarted map[string]bool // units a fix restarted in this round
}

// Local is the board doctor runs on.
func Local() System {
	return System{
		Root: "/", Release: kernelRelease(), Service: "ihc", Port: 8000,
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
		Signal: querySignal,
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
	s.restarted = map[string]bool{}
	results := s.checks()
	if !opts.Fix {
		return results
	}
	done := map[string]Result{} // check ID -> the result whose fix was applied
	for round := 0; round < 3; round++ {
		clear(s.restarted) // a unit restarts once per round, however many checks ask for it
		applied := false
		for _, r := range results {
			if r.Fix == nil || (r.Fix.Boot && !opts.Boot) || (r.Fix.Disruptive && opts.SafeOnly) {
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
	installed := s.exists("etc/systemd/system/ihcd.service")
	hdmi, node := s.hdmiInput()
	add(hdmi)
	add(s.hdmiSignal(node, installed))
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

// What a person does about the USB link, by what the Type-C port shows.
const (
	adviceNoPartner = "nothing is plugged into the board's Type-C port: connect the hub's USB-A port to the Type-C port " +
		"next to the USB 3 ports with a USB-A to USB-C data cable; if it is plugged in, try another cable or port"
	adviceHostRole = "the board took the host role: use a USB-A to USB-C data cable from the hub's USB-A port, not C-to-C"
	advicePower    = "the hub needs its charger (in its USB-C PD input) for the port to get power"
	advicePhone    = "unlock the iPhone, check Settings > Privacy & Security > Wired Accessories"
)

// typec is the Type-C port of the USB device port, when the board shows exactly one port that can
// be either a host or a device (or exactly one port at all).
func (s System) typec() (board.TypeCPort, bool) {
	all := board.TypeCPorts(s.Root)
	var dual []board.TypeCPort
	for _, p := range all {
		if p.DualData {
			dual = append(dual, p)
		}
	}
	if len(dual) == 0 {
		dual = all
	}
	if len(dual) != 1 {
		return board.TypeCPort{}, false
	}
	return dual[0], true
}

func typecNote(p board.TypeCPort) string {
	plugged := "nothing plugged in"
	if p.Partner {
		plugged = "something plugged in"
	}
	return fmt.Sprintf("Type-C %s: data role %s, power role %s, %s", p.Name, orUnknown(p.DataRole), orUnknown(p.PowerRole), plugged)
}

func (s System) usbPort() *Result {
	r := &Result{ID: "usb-port", Title: "USB device port"}
	udcs := s.hid().ListUDCs()
	switches := board.RoleSwitches(s.Root)
	ports := board.TypeCPorts(s.Root)
	for _, c := range board.USBControllers(s.Root) {
		r.Notes = append(r.Notes, fmt.Sprintf("device tree %s: dr_mode %s", c.Node, c.DRMode))
	}
	for _, sw := range switches {
		r.Notes = append(r.Notes, fmt.Sprintf("role switch %s: %s", sw.Name, orNone(sw.Role)))
	}
	partner := false
	for _, p := range ports {
		r.Notes = append(r.Notes, typecNote(p))
		partner = partner || p.Partner
	}
	if len(udcs) > 0 {
		r.Status, r.Found = OK, "device controller "+strings.Join(udcs, ", ")
		r.Notes = nil
		return r
	}
	r.Status, r.Found = Fail, "no USB device controller: the board's USB-C port works as a host only"
	tc, one := s.typec()
	if one && tc.Partner && tc.DataRole == "host" {
		r.Found = "no USB device controller: the board's USB-C port is a USB host now"
		r.Advice = adviceHostRole
		return r
	}
	var hostSwitches []string
	for _, sw := range switches {
		if sw.Role == "host" {
			hostSwitches = append(hostSwitches, sw.Name)
		}
		// with something plugged in, the port's role follows it: only a port with no role and
		// nothing plugged in is switched by hand
		if sw.Role != "none" || partner {
			continue
		}
		sw := sw
		file := filepath.Join("/sys/class/usb_role", sw.Name, "role")
		r.Fix = &Fix{What: "switch " + sw.Name + " to device mode (echo device > " + file + ")", Disruptive: true,
			Why: "the port stops acting as a USB host: a USB device plugged into it (a disk, a keyboard) stops working",
			do: func() (string, error) {
				return "", os.WriteFile(s.path(file), []byte("device"), 0o644)
			}}
		r.Advice = "if the port needs this at every boot, the image's Type-C settings keep it in host mode: " +
			"the Orange Pi image allows device mode on the Type-C port next to the USB 3 ports"
		if len(ports) > 0 {
			r.Advice = "nothing is plugged into the Type-C port yet: connected to the hub's USB-A port with a USB-A to USB-C " +
				"data cable, it takes the device role by itself. " + r.Advice
		}
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
	case len(hostSwitches) > 0:
		r.Advice = "the role switch " + strings.Join(hostSwitches, ", ") + " keeps the port in host mode: connect the hub's " +
			"USB-A port to it with a USB-A to USB-C data cable, so the board takes the device role; if it stays a host, the " +
			"image's Type-C settings keep it there (the Orange Pi image allows device mode on the Type-C port next to the USB 3 ports)"
		if one && !tc.Partner {
			r.Advice = adviceNoPartner + ". " + r.Advice
		}
	default:
		r.Advice = "no USB controller of this kernel acts as a device: use the Orange Pi image, or Armbian with the vendor kernel"
	}
	return r
}

// disruptGadget says why unbinding another gadget is disruptive.
const disruptGadget = "whatever uses that gadget now (ADB, a network or serial link over USB) loses its connection"

func (s System) otherGadget() *Result {
	p := s.hid()
	r := &Result{ID: "other-gadget", Title: "Other gadgets"}
	users := p.UDCUsers()
	for udc, g := range users {
		if g == hid.GadgetName {
			continue
		}
		r.Status, r.Found = Warn, fmt.Sprintf("the device controller %s is used by the gadget %q (often ADB)", udc, g)
		file := filepath.Join(p.ConfigFS, g, "UDC")
		r.Fix = &Fix{What: fmt.Sprintf("unbind %q from %s until the next boot", g, udc), Disruptive: true, Why: disruptGadget,
			do: func() (string, error) {
				return "", os.WriteFile(file, []byte("\n"), 0o644)
			}}
		if units := s.gadgetUnits(); len(units) > 0 {
			r.Advice = "it is set up at boot by " + strings.Join(units, ", ") + ": sudo systemctl disable " + strings.Join(units, " ")
		} else {
			r.Advice = "find what sets it up at boot (systemctl list-units | grep -i -e adb -e gadget) and disable it"
		}
		return r
	}
	mods := s.legacyModules()
	for _, udc := range p.ListUDCs() {
		fn := p.UDCFunction(udc)
		if fn == "" || fn == hid.GadgetName || users[udc] != "" {
			continue
		}
		// a legacy gadget module: its driver is named after it
		r.Status, r.Found = Warn, fmt.Sprintf("the device controller %s is used by the gadget driver %q, a legacy gadget module", udc, fn)
		mod := fn
		if !contains(mods, mod) && len(mods) == 1 {
			mod = mods[0]
		}
		if !contains(mods, mod) {
			r.Advice = "find its module (lsmod | grep ^g_), then " + s.moduleAdvice([]string{"<module>"})
			return r
		}
		r.Advice = s.moduleAdvice([]string{mod})
		r.Fix = s.unloadFix([]string{mod}, true)
		return r
	}
	if len(mods) > 0 {
		r.Status, r.Found = Warn, "legacy gadget module loaded: "+strings.Join(mods, ", ")+": it takes the device controller whenever it is free"
		r.Advice = s.moduleAdvice(mods)
		r.Fix = s.unloadFix(mods, false)
		return r
	}
	if len(p.ListUDCs()) == 0 {
		return nil
	}
	r.Status, r.Found = OK, "none"
	return r
}

// legacyModules lists the loaded legacy gadget modules (g_ether, g_serial, g_multi, g_mass_storage,
// g_hid, ...): each sets up a gadget of its own on the device controller.
func (s System) legacyModules() []string {
	entries, _ := os.ReadDir(s.path("sys/module"))
	var out []string
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), "g_") {
			out = append(out, e.Name())
		}
	}
	return out
}

// moduleAdvice says how to remove legacy gadget modules for good: unload them, and take them out of
// the files that load them at boot.
func (s System) moduleAdvice(mods []string) string {
	files := []string{"etc/modules"}
	entries, _ := os.ReadDir(s.path("etc/modules-load.d"))
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".conf") {
			files = append(files, "etc/modules-load.d/"+e.Name())
		}
	}
	var found []string
	for _, f := range files {
		for _, l := range strings.Split(readFile(s.path(f)), "\n") {
			if w := strings.Fields(l); len(w) > 0 && contains(mods, strings.ReplaceAll(w[0], "-", "_")) {
				found = append(found, "/"+f)
				break
			}
		}
	}
	where := "/etc/modules and /etc/modules-load.d"
	if len(found) > 0 {
		where = strings.Join(found, ", ")
	}
	return fmt.Sprintf("sudo modprobe -r %s, and remove it from %s so it is not loaded at boot", strings.Join(mods, " "), where)
}

// unloadFix unloads legacy gadget modules; unloading one that holds the device controller is
// disruptive.
func (s System) unloadFix(mods []string, bound bool) *Fix {
	f := &Fix{What: "unload " + strings.Join(mods, ", ") + " until the next boot (modprobe -r)", do: func() (string, error) {
		if out, err := s.Command("modprobe", append([]string{"-r"}, mods...)...); err != nil {
			return "", fmt.Errorf("%v %s", err, out)
		}
		return "", nil
	}}
	if bound {
		f.Disruptive, f.Why = true, disruptGadget
	}
	return f
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
	for _, udc := range p.ListUDCs() {
		if fn := p.UDCFunction(udc); fn != "" && fn != hid.GadgetName {
			r.Advice = fmt.Sprintf("the gadget driver %q holds %s: see Other gadgets", fn, udc)
			return r
		}
	}
	if s.exists("etc/systemd/system/ihcd-gadget.service") {
		r.Fix = &Fix{What: "set it up: systemctl restart ihcd-gadget", do: func() (string, error) {
			return "", s.restart("ihcd-gadget")
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
	udc := p.BoundUDC(hid.GadgetName)
	if udc == "" {
		return nil
	}
	r := &Result{ID: "nodes", Title: "HID nodes"}
	nodes := p.Nodes(hid.GadgetName)
	if len(nodes) == 0 {
		r.Status, r.Found = Warn, "the gadget is up but /dev has no hidg nodes for it"
		r.Advice = "udev has not made them yet: sudo udevadm trigger --action=add --subsystem-match=hidg, then check again"
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
	// the service signals remote wakeup to a sleeping iPhone through the controller's srp
	srp := filepath.Join(p.SysFS, "class", "udc", udc, "srp")
	srpWrong := false
	if st, err := os.Stat(srp); err == nil {
		sys, ok := st.Sys().(*syscall.Stat_t)
		srpWrong = !ok || int(sys.Gid) != gid || st.Mode().Perm()&0o020 == 0
	}
	if len(wrong) == 0 && !srpWrong {
		r.Status, r.Found = OK, fmt.Sprintf("%d nodes, readable and writable by the service", len(nodes))
	} else {
		var found, what []string
		if len(wrong) > 0 {
			found = append(found, "the service cannot write "+strings.Join(wrong, ", "))
			what = append(what, "give the nodes to the group "+s.Service+" (chgrp, chmod 660)")
		}
		if srpWrong {
			found = append(found, "the service cannot write /sys/class/udc/"+udc+"/srp, so it cannot wake a sleeping iPhone")
			what = append(what, "chgrp "+s.Service+" and chmod g+w /sys/class/udc/"+udc+"/srp")
		}
		r.Status, r.Found = Warn, strings.Join(found, "; ")
		r.Fix = &Fix{What: strings.Join(what, "; "), do: func() (string, error) {
			for _, n := range wrong {
				if err := os.Chown(n, -1, gid); err != nil {
					return "", err
				}
				if err := os.Chmod(n, 0o660); err != nil {
					return "", err
				}
			}
			if srpWrong {
				st, err := os.Stat(srp)
				if err != nil {
					return "", err
				}
				if err := os.Chown(srp, -1, gid); err != nil {
					return "", err
				}
				return "", os.Chmod(srp, st.Mode().Perm()|0o020)
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
		tc, one := s.typec()
		switch {
		case one && !tc.Partner:
			r.Advice = adviceNoPartner
		case one && tc.DataRole == "host":
			r.Advice = adviceHostRole
		default:
			r.Advice = "connect the hub's USB-A port to the board's Type-C port next to the USB 3 ports (USB-A to USB-C data " +
				"cable), and the iPhone to the hub; " + advicePower + "; " + advicePhone
		}
	default:
		r.Status, r.Found = Warn, "the iPhone sees the box but has not accepted it ("+state+")"
		r.Advice = advicePhone + ", and tap Allow if it asks about the accessory"
	}
	return r
}

func (s System) hdmiInput() (*Result, string) {
	r := &Result{ID: "hdmi-input", Title: "HDMI input"}
	if node := board.HDMIInput(s.Root); node != "" {
		r.Status, r.Found = OK, node
		if f := board.StaleUserOverlay(s.Root); f != nil {
			r.Status = Warn
			r.Notes = []string{f.To + " differs from the kernel's " + f.From + ": copied before a kernel update, " +
				"it may stop applying at a later boot"}
			r.Fix = s.bootFix(*f)
		}
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
		r.Fix = s.bootFix(*f)
	}
	return r, ""
}

// bootFix makes a boot configuration change a fix. It test-applies the overlays first when the board
// has fdtoverlay, and says how to undo it.
func (s System) bootFix(f board.BootFix) *Fix {
	return &Fix{What: f.String() + " (a backup of each file it replaces is kept); then reboot", Boot: true, do: func() (string, error) {
		note, err := s.testOverlays(f)
		if err != nil {
			return "", err
		}
		undo, err := f.Apply(s.Root, s.Now())
		if err != nil {
			return "", err
		}
		msg := f.String() + "; " + note
		if undo != "" {
			msg += ". To undo: " + undo
		}
		return msg + ". Reboot to turn the HDMI input on", nil
	}}
}

// testOverlays applies the overlays the boot script will load, the new one with them, to the base
// device tree, when the board has fdtoverlay. The boot script drops every overlay when one fails to
// apply, so a failure here refuses the change. It returns what it tested, or why it could not.
func (s System) testOverlays(f board.BootFix) (string, error) {
	tool := s.which("fdtoverlay")
	if tool == "" {
		return "not test-applied: no fdtoverlay on this board (sudo apt install device-tree-compiler)", nil
	}
	base, overlays, why := f.TestSet(s.Root)
	if why == "" && len(overlays) == 0 {
		why = "no overlay file to test"
	}
	if why != "" {
		return "not test-applied: " + why, nil
	}
	out, err := s.Command(tool, append([]string{"-i", base, "-o", os.DevNull}, overlays...)...)
	if err != nil {
		return "", fmt.Errorf("the overlays do not apply to %s, so the boot script would drop them all: nothing changed (%v %s)",
			s.onBoard(base), err, out)
	}
	return fmt.Sprintf("test-applied with %d overlay(s) to %s", len(overlays), s.onBoard(base)), nil
}

func (s System) hdmiSignal(node string, installed bool) *Result {
	if node == "" {
		return nil
	}
	r := &Result{ID: "hdmi-signal", Title: "Picture"}
	t, err := s.Signal(node)
	if err == nil {
		r.Status, r.Found = OK, "receiving "+t
		return r
	}
	if errors.Is(err, fs.ErrPermission) {
		r.Status, r.Found = Info, "cannot open "+node+" without root"
		r.Advice = "run with sudo: sudo ihcd doctor"
		return r
	}
	r.Status, r.Found = Warn, "no picture: "+err.Error()
	r.Advice = "unlock the iPhone and check that the hub's HDMI cable goes into the board's HDMI IN port; " +
		"replug the hub. iPhone 16e and 17e have no video output"
	edid := "the service writes the HDMI input's EDID (1080p60) when it starts, and the phone waits for it before it sends a picture"
	switch {
	case !installed:
		r.Advice = "the box software is not running: " + edid + ": install it. If there is still no picture: " + r.Advice
	case !s.active("ihcd"):
		r.Advice = "ihcd is not running: " + edid + ": sudo systemctl start ihcd. If there is still no picture: " + r.Advice
	case contains(s.serviceArgs(), "--no-edid") || contains(s.serviceArgs(), "-no-edid"):
		r.Advice = "ihcd runs with --no-edid: " + edid + ", and only without that flag: remove it from IHCD_ARGS in " +
			"/etc/default/ihc, then sudo systemctl restart ihcd. If there is still no picture: " + r.Advice
	}
	return r
}

// active says whether a systemd unit is running.
func (s System) active(unit string) bool {
	out, _ := s.Command("systemctl", "is-active", unit)
	return strings.TrimSpace(out) == "active"
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
		return "", s.restart(bad...)
	}}
	return r
}

// restart restarts the units no fix restarted yet in this round.
func (s System) restart(units ...string) error {
	var todo []string
	for _, u := range units {
		if !s.restarted[u] {
			todo = append(todo, u)
		}
		if s.restarted != nil {
			s.restarted[u] = true
		}
	}
	if len(todo) == 0 {
		return nil
	}
	if out, err := s.Command("systemctl", append([]string{"restart"}, todo...)...); err != nil {
		return fmt.Errorf("%v %s", err, out)
	}
	return nil
}

// serviceArgs are the service's extra flags: IHCD_ARGS of /etc/default/ihc.
func (s System) serviceArgs() []string {
	var f []string
	for _, l := range strings.Split(readFile(s.path("etc/default/ihc")), "\n") {
		l = strings.TrimSpace(l)
		if strings.HasPrefix(l, "IHCD_ARGS=") {
			f = strings.Fields(strings.Trim(strings.TrimPrefix(l, "IHCD_ARGS="), `"'`))
		}
	}
	return f
}

// port is the API's port: --addr in IHCD_ARGS of /etc/default/ihc, else the default.
func (s System) port() int {
	f := s.serviceArgs()
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
			return "", s.restart("ihcd")
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
	if errors.Is(err, fs.ErrPermission) {
		r.Status, r.Found = Info, "cannot read /var/lib/ihc/token without root"
		r.Advice = "run with sudo: sudo ihcd doctor"
		return r
	}
	if err != nil || st.Size() == 0 {
		r.Status, r.Found = Warn, "no token in /var/lib/ihc/token"
		r.Fix = &Fix{What: "let the service make one: systemctl restart ihcd", do: func() (string, error) {
			return "", s.restart("ihcd")
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
	var fails, warns, fixable, disruptive, boot int
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
		if r.Status == OK {
			continue
		}
		problem := r.Status == Fail || r.Status == Warn
		switch r.Status {
		case Fail:
			fails++
		case Warn:
			warns++
		}
		if r.Fix != nil && r.Done == "" {
			flag := "--fix"
			switch {
			case !problem:
			case r.Fix.Boot:
				boot++
			case r.Fix.Disruptive:
				fixable++
				disruptive++
			default:
				fixable++
			}
			if r.Fix.Boot {
				flag = "--fix-boot"
			}
			fmt.Fprintf(w, "%sfix (sudo ihcd doctor %s): %s\n", pad, flag, r.Fix.What)
			if r.Fix.Disruptive {
				fmt.Fprintf(w, "%sdisruptive: %s (--fix-safe leaves it out)\n", pad, r.Fix.Why)
			}
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
		fmt.Fprintf(w, "sudo ihcd doctor --fix fixes %d of them", fixable)
		switch {
		case disruptive == fixable:
			fmt.Fprint(w, " (disruptive: see above)")
		case disruptive > 0:
			fmt.Fprintf(w, "; %d of those fixes %s disruptive (see above), sudo ihcd doctor --fix-safe leaves them out",
				disruptive, map[bool]string{true: "is", false: "are"}[disruptive == 1])
		}
		fmt.Fprintln(w, ".")
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

func orUnknown(s string) string {
	if s == "" {
		return "unknown"
	}
	return s
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// which finds a program on the board ("" if it has none).
func (s System) which(name string) string {
	for _, d := range []string{"usr/bin", "bin", "usr/sbin", "sbin", "usr/local/bin"} {
		if st, err := os.Stat(s.path(filepath.Join(d, name))); err == nil && !st.IsDir() {
			return "/" + d + "/" + name
		}
	}
	return ""
}

// onBoard is a path under Root as the board names it.
func (s System) onBoard(p string) string {
	rel, err := filepath.Rel(s.Root, p)
	if err != nil || strings.HasPrefix(rel, "..") {
		return p
	}
	return "/" + rel
}

func readFile(p string) string {
	b, _ := os.ReadFile(p)
	return string(b)
}

// ErrNotRoot is returned when a fix is asked for without root.
var ErrNotRoot = errors.New("fixing needs root: sudo ihcd doctor --fix")
