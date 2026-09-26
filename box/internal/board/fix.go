package board

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// BootFix is a change to the boot configuration that turns the HDMI input on: an overlay named in the
// boot environment file, and for user_overlays the overlay copied next to it first. It takes effect
// at the next boot.
type BootFix struct {
	Env     string `json:"env"`             // the boot environment file, e.g. /boot/armbianEnv.txt
	Key     string `json:"key"`             // overlays or user_overlays
	Entry   string `json:"entry"`           // the name added to that line
	Overlay string `json:"overlay"`         // the kernel's overlay file
	From    string `json:"from,omitempty"`  // user_overlays: the overlay to copy ...
	To      string `json:"to,omitempty"`    // ... to /boot/overlay-user
	Drop    string `json:"drop,omitempty"`  // an overlays= entry this boot script cannot load (moved to user_overlays)
	Stale   bool   `json:"stale,omitempty"` // the copy in /boot/overlay-user differs from the kernel's overlay
}

func (f BootFix) String() string {
	if f.Stale {
		return fmt.Sprintf("copy %s to %s again (the copy there differs from the kernel's overlay)", f.From, f.To)
	}
	s := fmt.Sprintf("add %s to the %s= line of %s", f.Entry, f.Key, f.Env)
	if f.From != "" {
		s = fmt.Sprintf("copy %s to %s, then ", f.From, f.To) + s
	}
	if f.Drop != "" {
		s += fmt.Sprintf(" and remove %s from overlays=", f.Drop)
	}
	return s
}

// HDMIBootFix returns the boot configuration change that turns on an HDMI input the device tree
// leaves off, when the image ships one overlay for it and the boot environment does not name it
// yet, or names a copy of it that differs from the kernel's (nil otherwise: nothing this can change
// safely). Paths in the result are absolute on the board.
func HDMIBootFix(root string) *BootFix {
	var off, on bool
	for _, n := range dtNodes(root) {
		if strings.HasPrefix(n.path, "reserved-memory") {
			continue
		}
		if n.status == "okay" || n.status == "ok" {
			on = true
		} else {
			off = true
		}
	}
	dtbo, _ := hdmiOverlay(root)
	envPath, env := bootEnv(root)
	if on || !off || dtbo == "" || envPath == "" {
		return nil
	}
	name := strings.TrimSuffix(filepath.Base(dtbo), ".dtbo")
	envFile := "/boot/" + filepath.Base(envPath)
	prefix := env["overlay_prefix"]
	listed, userListed := strings.Fields(env["overlays"]), strings.Fields(env["user_overlays"])
	plain := strings.Contains(readFile(filepath.Join(root, "boot", "boot.cmd")), "overlay/${overlay_file}.dtbo")
	switch {
	case prefix != "" && strings.HasPrefix(name, prefix+"-"):
		entry := strings.TrimPrefix(name, prefix+"-")
		if contains(listed, entry) {
			return nil
		}
		return &BootFix{Env: envFile, Key: "overlays", Entry: entry, Overlay: "/" + dtbo}
	case plain:
		if contains(listed, name) {
			return nil
		}
		return &BootFix{Env: envFile, Key: "overlays", Entry: name, Overlay: "/" + dtbo}
	}
	if contains(userListed, name) {
		if _, err := os.Stat(filepath.Join(root, "boot", "overlay-user", name+".dtbo")); err == nil {
			return StaleUserOverlay(root)
		}
	}
	f := &BootFix{Env: envFile, Key: "user_overlays", Entry: name, Overlay: "/" + dtbo, From: "/" + dtbo,
		To: "/boot/overlay-user/" + name + ".dtbo"}
	if contains(listed, name) {
		f.Drop = name
	}
	return f
}

// StaleUserOverlay returns the copy to make again when user_overlays names the HDMI overlay and its
// copy in /boot/overlay-user differs from the kernel's (the kernel was updated after the copy), nil
// otherwise.
func StaleUserOverlay(root string) *BootFix {
	dtbo, _ := hdmiOverlay(root)
	envPath, env := bootEnv(root)
	if dtbo == "" || envPath == "" {
		return nil
	}
	name := strings.TrimSuffix(filepath.Base(dtbo), ".dtbo")
	to := filepath.Join("boot", "overlay-user", name+".dtbo")
	if !contains(strings.Fields(env["user_overlays"]), name) {
		return nil
	}
	if _, err := os.Stat(filepath.Join(root, to)); err != nil || sameFile(filepath.Join(root, to), filepath.Join(root, dtbo)) {
		return nil
	}
	return &BootFix{Env: "/boot/" + filepath.Base(envPath), Key: "user_overlays", Entry: name, Overlay: "/" + dtbo,
		From: "/" + dtbo, To: "/" + to, Stale: true}
}

// TestSet is what the boot script applies once the change is made: the base device tree
// (/boot/dtb/<fdtfile>) and the overlay files in the order the script loads them, as paths under
// root. Why says why it cannot tell ("" when it can).
//
// Armbian's and Orange Pi's boot scripts load the overlays= entries, then the user_overlays= entries
// from /boot/overlay-user; an entry without a file is skipped.
func (f BootFix) TestSet(root string) (base string, overlays []string, why string) {
	_, env := bootEnv(root)
	fdt := env["fdtfile"]
	if fdt == "" {
		return "", nil, "no fdtfile= in " + f.Env
	}
	base = filepath.Join(root, "boot", "dtb", fdt)
	if _, err := os.Stat(base); err != nil {
		return "", nil, "no /boot/dtb/" + fdt
	}
	dir := filepath.Join(root, filepath.Dir(f.Overlay))
	prefix := env["overlay_prefix"]
	plain := strings.Contains(readFile(filepath.Join(root, "boot", "boot.cmd")), "overlay/${overlay_file}.dtbo")
	isFile := func(p string) bool { st, err := os.Stat(p); return err == nil && !st.IsDir() }
	listed, userListed := strings.Fields(env["overlays"]), strings.Fields(env["user_overlays"])
	if f.Key == "overlays" && !contains(listed, f.Entry) {
		listed = append(listed, f.Entry)
	}
	if f.Key == "user_overlays" && !contains(userListed, f.Entry) {
		userListed = append(userListed, f.Entry)
	}
	for _, e := range listed {
		if e == f.Drop {
			continue
		}
		if p := filepath.Join(dir, prefix+"-"+e+".dtbo"); prefix != "" && isFile(p) {
			overlays = append(overlays, p)
		} else if p := filepath.Join(dir, e+".dtbo"); plain && isFile(p) {
			overlays = append(overlays, p)
		}
	}
	for _, e := range userListed {
		p := filepath.Join(root, "boot", "overlay-user", e+".dtbo")
		if f.Key == "user_overlays" && e == f.Entry && f.From != "" {
			p = filepath.Join(root, f.From) // the copy the change makes
		}
		if isFile(p) {
			overlays = append(overlays, p)
		}
	}
	return base, overlays, ""
}

// Apply makes the change under root. It keeps each file it replaces as <file>.ihc-<time> and
// returns the commands that put them back.
func (f BootFix) Apply(root string, now time.Time) (undo string, err error) {
	stamp := ".ihc-" + now.Format("20060102-150405")
	var undos []string
	board := func(p string) string { // the path on the board
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return p
		}
		return "/" + rel
	}
	if f.From != "" {
		b, err := os.ReadFile(filepath.Join(root, f.From))
		if err != nil {
			return "", err
		}
		to := filepath.Join(root, f.To)
		if err := os.MkdirAll(filepath.Dir(to), 0o755); err != nil {
			return "", err
		}
		if old, err := os.ReadFile(to); err == nil {
			if err := writeFile(to+stamp, old, 0o644); err != nil {
				return "", err
			}
			undos = append(undos, "sudo cp "+board(to+stamp)+" "+f.To)
		}
		if err := writeFile(to, b, 0o644); err != nil {
			return "", err
		}
	}
	// edit the file a symlink points to, not the link
	env, err := filepath.EvalSymlinks(filepath.Join(root, f.Env))
	if err != nil {
		return "", err
	}
	old, err := os.ReadFile(env)
	if err != nil {
		return "", err
	}
	st, err := os.Stat(env)
	if err != nil {
		return "", err
	}
	lines := strings.Split(strings.TrimRight(string(old), "\n"), "\n")
	found := false
	for i, l := range lines {
		k, v, ok := strings.Cut(l, "=")
		if !ok {
			continue
		}
		switch strings.TrimSpace(k) {
		case f.Key:
			words := strings.Fields(v)
			if !contains(words, f.Entry) {
				words = append(words, f.Entry)
			}
			lines[i], found = f.Key+"="+strings.Join(words, " "), true
		case "overlays":
			if f.Drop != "" {
				var keep []string
				for _, w := range strings.Fields(v) {
					if w != f.Drop {
						keep = append(keep, w)
					}
				}
				lines[i] = "overlays=" + strings.Join(keep, " ")
			}
		}
	}
	if !found {
		lines = append(lines, f.Key+"="+f.Entry)
	}
	text := strings.Join(lines, "\n") + "\n"
	if text != string(old) {
		if err := writeFile(env+stamp, old, st.Mode().Perm()); err != nil {
			return "", err
		}
		if err := writeFile(env, []byte(text), st.Mode().Perm()); err != nil {
			return "", err
		}
		undos = append([]string{"sudo cp " + board(env+stamp) + " " + f.Env}, undos...)
	}
	return strings.Join(undos, "; "), nil
}

// writeFile replaces path with data: a temporary file next to it, synced, renamed over it, then the
// directory synced, so a power cut leaves either the old file or the new one.
func writeFile(path string, data []byte, perm os.FileMode) error {
	tmp := path + ".ihc-new"
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, perm)
	if err != nil {
		return err
	}
	_, err = f.Write(data)
	if err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err == nil {
		err = os.Rename(tmp, path)
	}
	if err != nil {
		_ = os.Remove(tmp)
		return err
	}
	d, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer d.Close()
	if err := d.Sync(); err != nil && !errors.Is(err, syscall.EINVAL) {
		return err
	}
	return nil
}

// -- the USB device port ----------------------------------------------------------------------------------

// USBController is a USB controller node of the device tree with its dr_mode (host, peripheral, otg).
type USBController struct {
	Node   string `json:"node"`
	DRMode string `json:"dr_mode"`
}

// USBControllers lists the device tree's USB controllers that declare a dr_mode.
func USBControllers(root string) []USBController {
	base := filepath.Join(root, "sys", "firmware", "devicetree", "base")
	var out []USBController
	var walk func(dir string, depth int)
	walk = func(dir string, depth int) {
		entries, _ := os.ReadDir(dir)
		for _, e := range entries {
			if !e.IsDir() {
				continue
			}
			p := filepath.Join(dir, e.Name())
			if m := props(p, "dr_mode"); len(m) > 0 {
				rel, _ := filepath.Rel(base, p)
				out = append(out, USBController{Node: rel, DRMode: m[0]})
			}
			if depth < 2 {
				walk(p, depth+1)
			}
		}
	}
	walk(base, 0)
	return out
}

// RoleSwitch is a USB role switch (/sys/class/usb_role): the port acts as host or device.
type RoleSwitch struct {
	Name string `json:"name"`
	Role string `json:"role"` // host, device, none
}

// RoleSwitches lists the board's USB role switches.
func RoleSwitches(root string) []RoleSwitch {
	dir := filepath.Join(root, "sys", "class", "usb_role")
	entries, _ := os.ReadDir(dir)
	var out []RoleSwitch
	for _, e := range entries {
		out = append(out, RoleSwitch{e.Name(), strings.TrimSpace(readFile(filepath.Join(dir, e.Name(), "role")))})
	}
	return out
}

// TypeCPort is a USB Type-C port (/sys/class/typec/portN): its data and power roles now, and
// whether something is plugged into it (the kernel then shows a partner, portN-partner).
type TypeCPort struct {
	Name      string `json:"name"`
	DataRole  string `json:"data_role"`  // host or device ("" unknown)
	PowerRole string `json:"power_role"` // source or sink ("" unknown)
	DualData  bool   `json:"dual_data"`  // the port can take either data role
	Partner   bool   `json:"partner"`
}

// TypeCPorts lists the board's Type-C ports; a kernel without Type-C support shows none.
func TypeCPorts(root string) []TypeCPort {
	dir := filepath.Join(root, "sys", "class", "typec")
	entries, _ := os.ReadDir(dir)
	var out []TypeCPort
	for _, e := range entries {
		n := e.Name()
		if _, err := strconv.Atoi(strings.TrimPrefix(n, "port")); !strings.HasPrefix(n, "port") || err != nil {
			continue // port0-partner, port0-cable, ...
		}
		data := readFile(filepath.Join(dir, n, "data_role"))
		_, err := os.Stat(filepath.Join(dir, n+"-partner"))
		out = append(out, TypeCPort{Name: n, DataRole: currentRole(data),
			PowerRole: currentRole(readFile(filepath.Join(dir, n, "power_role"))),
			DualData:  strings.Contains(data, "host") && strings.Contains(data, "device"), Partner: err == nil})
	}
	return out
}

// currentRole reads a role file: "[host] device" is host now; a single word is the only role.
func currentRole(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.Index(s, "["); i >= 0 {
		if j := strings.Index(s[i:], "]"); j > 0 {
			return s[i+1 : i+j]
		}
	}
	if f := strings.Fields(s); len(f) == 1 {
		return f[0]
	}
	return ""
}

// USBOverlays lists the overlays the image ships that may put a USB port in device (OTG) mode.
func USBOverlays(root string) []string {
	var out []string
	for _, d := range []string{"boot/dtb/rockchip/overlay", "boot/dtb/overlay"} {
		entries, _ := os.ReadDir(filepath.Join(root, d))
		for _, e := range entries {
			n := strings.ToLower(e.Name())
			if strings.HasSuffix(n, ".dtbo") && (strings.Contains(n, "otg") || strings.Contains(n, "peripheral") ||
				strings.Contains(n, "gadget") || strings.Contains(n, "dwc3")) {
				out = append(out, filepath.Join(d, e.Name()))
			}
		}
	}
	sort.Strings(out)
	return out
}

// Model is the board's name from the device tree ("" without one).
func Model(root string) string {
	return strings.TrimRight(readFile(filepath.Join(root, "sys", "firmware", "devicetree", "base", "model")), "\x00\n")
}
