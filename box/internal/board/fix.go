package board

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// BootFix is a change to the boot configuration that turns the HDMI input on: an overlay named in the
// boot environment file, and for user_overlays the overlay copied next to it first. It takes effect
// at the next boot.
type BootFix struct {
	Env   string `json:"env"`            // the boot environment file, e.g. /boot/armbianEnv.txt
	Key   string `json:"key"`            // overlays or user_overlays
	Entry string `json:"entry"`          // the name added to that line
	From  string `json:"from,omitempty"` // user_overlays: the overlay to copy ...
	To    string `json:"to,omitempty"`   // ... to /boot/overlay-user
	Drop  string `json:"drop,omitempty"` // an overlays= entry this boot script cannot load (moved to user_overlays)
}

func (f BootFix) String() string {
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
// leaves off, when the image ships the overlay for it and the boot environment does not name it
// yet (nil otherwise: nothing this can change safely). Paths in the result are absolute on the board.
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
	shipped := shippedOverlays(root)
	envPath, env := bootEnv(root)
	if on || !off || len(shipped) == 0 || envPath == "" {
		return nil
	}
	dtbo := shipped[0]
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
		return &BootFix{Env: envFile, Key: "overlays", Entry: entry}
	case plain:
		if contains(listed, name) {
			return nil
		}
		return &BootFix{Env: envFile, Key: "overlays", Entry: name}
	}
	if contains(userListed, name) {
		if _, err := os.Stat(filepath.Join(root, "boot", "overlay-user", name+".dtbo")); err == nil {
			return nil
		}
	}
	f := &BootFix{Env: envFile, Key: "user_overlays", Entry: name, From: "/" + dtbo, To: "/boot/overlay-user/" + name + ".dtbo"}
	if contains(listed, name) {
		f.Drop = name
	}
	return f
}

// Apply makes the change under root, keeping the environment file as it was in <file>.ihc-<time>.
// It returns the backup's path.
func (f BootFix) Apply(root string, now time.Time) (string, error) {
	env := filepath.Join(root, f.Env)
	old, err := os.ReadFile(env)
	if err != nil {
		return "", err
	}
	st, err := os.Stat(env)
	if err != nil {
		return "", err
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
		if err := os.WriteFile(to, b, 0o644); err != nil {
			return "", err
		}
	}
	backup := env + ".ihc-" + now.Format("20060102-150405")
	if err := os.WriteFile(backup, old, st.Mode().Perm()); err != nil {
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
	tmp := env + ".ihc-new"
	if err := os.WriteFile(tmp, []byte(strings.Join(lines, "\n")+"\n"), st.Mode().Perm()); err != nil {
		return "", err
	}
	return backup, os.Rename(tmp, env)
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
