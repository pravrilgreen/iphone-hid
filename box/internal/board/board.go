// Package board reads what the board offers: its serial number, its video capture nodes, and why an
// HDMI input may be missing (device tree, driver, boot overlays).
package board

import (
	"compress/gzip"
	"crypto/sha1"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// DeviceID names the phone of this box on the network: iphone-<6 hex digits of the board's serial>.
func DeviceID(root string) string {
	for _, p := range []string{"proc/device-tree/serial-number", "sys/firmware/devicetree/base/serial-number", "etc/machine-id"} {
		b, err := os.ReadFile(filepath.Join(root, p))
		s := strings.Trim(strings.TrimSpace(string(b)), "\x00")
		if err == nil && s != "" {
			return fmt.Sprintf("iphone-%x", sha1.Sum([]byte(s)))[:13]
		}
	}
	host, _ := os.Hostname()
	return fmt.Sprintf("iphone-%x", sha1.Sum([]byte(host)))[:13]
}

// VideoNode is a V4L2 device.
type VideoNode struct {
	Path   string `json:"path"`
	Name   string `json:"name"`
	Driver string `json:"driver"`
}

// VideoNodes lists the board's V4L2 devices.
func VideoNodes(root string) []VideoNode {
	dir := filepath.Join(root, "sys", "class", "video4linux")
	entries, _ := os.ReadDir(dir)
	var out []VideoNode
	for _, e := range entries {
		name := strings.TrimSpace(readFile(filepath.Join(dir, e.Name(), "name")))
		drv := ""
		if l, err := os.Readlink(filepath.Join(dir, e.Name(), "device", "driver")); err == nil {
			drv = filepath.Base(l)
		}
		out = append(out, VideoNode{Path: "/dev/" + e.Name(), Name: name, Driver: drv})
	}
	sort.Slice(out, func(i, j int) bool { return natLess(out[i].Path, out[j].Path) })
	return out
}

// HDMIInput finds the HDMI receiver's capture node ("" if the board shows none).
func HDMIInput(root string) string {
	for _, n := range VideoNodes(root) {
		if matches(n.Name, words) || matches(n.Driver, words) {
			return n.Path
		}
	}
	return ""
}

func natLess(a, b string) bool {
	na, ea := trailingNumber(a)
	nb, eb := trailingNumber(b)
	if ea == nil && eb == nil && strings.TrimRight(a, "0123456789") == strings.TrimRight(b, "0123456789") {
		return na < nb
	}
	return a < b
}

func trailingNumber(s string) (int, error) {
	i := len(s)
	for i > 0 && s[i-1] >= '0' && s[i-1] <= '9' {
		i--
	}
	return strconv.Atoi(s[i:])
}

// -- why no HDMI input ---------------------------------------------------------------------------------------

var words = []string{"hdmirx", "hdmi_receiver", "hdmi-receiver", "hdmi-rx", "hdmi_rx", "hdmiin", "hdmi-in"}

// nodeWords match the receiver's own device tree nodes (not an always-on hdmiin-sound card).
var nodeWords = []string{"hdmirx", "hdmi_receiver", "hdmi-receiver"}

func matches(name string, ws []string) bool {
	name = strings.ToLower(name)
	if strings.Contains(name, "sound") {
		return false
	}
	for _, w := range ws {
		if strings.Contains(name, w) {
			return true
		}
	}
	return false
}

func readFile(p string) string {
	b, _ := os.ReadFile(p)
	return string(b)
}

func props(node, prop string) []string {
	b, err := os.ReadFile(filepath.Join(node, prop))
	if err != nil {
		return nil
	}
	var out []string
	for _, s := range strings.Split(string(b), "\x00") {
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

type dtNode struct{ path, status, compatible string }

func dtNodes(root string) []dtNode {
	base := filepath.Join(root, "sys", "firmware", "devicetree", "base")
	var out []dtNode
	for _, parent := range []string{base, filepath.Join(base, "reserved-memory")} {
		entries, _ := os.ReadDir(parent)
		for _, e := range entries {
			if !e.IsDir() || !matches(e.Name(), nodeWords) {
				continue
			}
			n := filepath.Join(parent, e.Name())
			status := "okay" // no status property = enabled
			if s := props(n, "status"); len(s) > 0 {
				status = s[0]
			}
			rel, _ := filepath.Rel(base, n)
			out = append(out, dtNode{rel, status, strings.Join(props(n, "compatible"), ", ")})
		}
	}
	return out
}

func boundDrivers(root string) map[string][]string {
	dir := filepath.Join(root, "sys", "bus", "platform", "drivers")
	entries, _ := os.ReadDir(dir)
	out := map[string][]string{}
	for _, d := range entries {
		if !matches(d.Name(), words) {
			continue
		}
		devs := []string{}
		sub, _ := os.ReadDir(filepath.Join(dir, d.Name()))
		for _, e := range sub {
			if e.Type()&os.ModeSymlink != 0 && e.Name() != "module" {
				devs = append(devs, e.Name())
			}
		}
		sort.Strings(devs)
		out[d.Name()] = devs
	}
	return out
}

func kernelConfig(root, release string) []string {
	var text string
	if f, err := os.Open(filepath.Join(root, "proc", "config.gz")); err == nil {
		if z, err := gzip.NewReader(f); err == nil {
			b, _ := io.ReadAll(z)
			text = string(b)
		}
		f.Close()
	}
	if text == "" {
		text = readFile(filepath.Join(root, "boot", "config-"+release))
	}
	var out []string
	for _, l := range strings.Split(text, "\n") {
		if strings.Contains(strings.ToUpper(l), "HDMIRX") {
			out = append(out, l)
		}
	}
	return out
}

// driverConfig says whether a kernel config line builds the HDMI receiver's driver itself
// (CONFIG_VIDEO_ROCKCHIP_HDMIRX=y or =m), not one of its options (..._HDMIRX_LOAD_DEFAULT_EDID).
func driverConfig(l string) bool {
	k, v, ok := strings.Cut(strings.TrimSpace(l), "=")
	return ok && strings.HasPrefix(k, "CONFIG_") && strings.HasSuffix(k, "HDMIRX") && (v == "y" || v == "m")
}

func modules(root, release string) (files, builtin []string) {
	mods := filepath.Join(root, "lib", "modules", release)
	_ = filepath.Walk(mods, func(p string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.Contains(info.Name(), ".ko") && matches(info.Name(), words) {
			rel, _ := filepath.Rel(mods, p)
			files = append(files, rel)
		}
		return nil
	})
	for _, l := range strings.Split(readFile(filepath.Join(mods, "modules.builtin")), "\n") {
		if l = strings.TrimSpace(l); l != "" && matches(l, words) {
			builtin = append(builtin, l)
		}
	}
	return files, builtin
}

func bootEnv(root string) (string, map[string]string) {
	for _, name := range []string{"armbianEnv.txt", "orangepiEnv.txt"} {
		text, err := os.ReadFile(filepath.Join(root, "boot", name))
		if err != nil {
			continue
		}
		env := map[string]string{}
		for _, l := range strings.Split(string(text), "\n") {
			if k, v, ok := strings.Cut(l, "="); ok {
				env[strings.TrimSpace(k)] = strings.TrimSpace(v)
			}
		}
		return filepath.Join(root, "boot", name), env
	}
	return "", map[string]string{}
}

func shippedOverlays(root string) []string {
	var out []string
	for _, d := range []string{"boot/dtb/rockchip/overlay", "boot/dtb/overlay"} {
		entries, _ := os.ReadDir(filepath.Join(root, d))
		for _, e := range entries {
			if strings.HasSuffix(e.Name(), ".dtbo") && matches(e.Name(), words) {
				out = append(out, filepath.Join(d, e.Name()))
			}
		}
	}
	return out
}

// hdmiOverlay picks the overlay that turns the HDMI input on (relative to root): the one named
// exactly rk3588-hdmirx, else the only candidate. "" when there are several to choose from.
func hdmiOverlay(root string) (string, []string) {
	shipped := shippedOverlays(root)
	var exact []string
	for _, s := range shipped {
		if filepath.Base(s) == "rk3588-hdmirx.dtbo" {
			exact = append(exact, s)
		}
	}
	switch {
	case len(exact) == 1:
		return exact[0], shipped
	case len(exact) == 0 && len(shipped) == 1:
		return shipped[0], shipped
	}
	return "", shipped
}

// sameFile says whether two files have the same content.
func sameFile(a, b string) bool {
	x, err1 := os.ReadFile(a)
	y, err2 := os.ReadFile(b)
	return err1 == nil && err2 == nil && string(x) == string(y)
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// overlayAdvice says how to get the boot script to apply the overlay dtbo (relative to root).
//
// Armbian's boot script loads each overlays= entry as overlay/<overlay_prefix>-<entry>.dtbo; since
// Armbian 24.11 it also tries overlay/<entry>.dtbo. On an older script a kernel overlay whose name
// does not start with the prefix (rk3588-hdmirx.dtbo with overlay_prefix=rockchip-rk3588) cannot be
// named in overlays= at all: it is skipped without a word. user_overlays= (files in
// /boot/overlay-user) works with every version.
func overlayAdvice(root, dtbo string) ([]string, string) {
	name := strings.TrimSuffix(filepath.Base(dtbo), ".dtbo")
	overlayDir := filepath.Join(root, filepath.Dir(dtbo))
	envPath, env := bootEnv(root)
	envFile := "/boot/armbianEnv.txt"
	if envPath != "" {
		envFile = "/boot/" + filepath.Base(envPath)
	}
	prefix := env["overlay_prefix"]
	listed, userListed := strings.Fields(env["overlays"]), strings.Fields(env["user_overlays"])
	script := readFile(filepath.Join(root, "boot", "boot.cmd"))
	plain := strings.Contains(script, "overlay/${overlay_file}.dtbo") // the Armbian 24.11+ fallback
	exists := func(p string) bool { st, err := os.Stat(p); return err == nil && !st.IsDir() }
	found := func(entry string) bool {
		return exists(filepath.Join(overlayDir, prefix+"-"+entry+".dtbo")) || (plain && exists(filepath.Join(overlayDir, entry+".dtbo")))
	}
	var findings []string
	if script != "" && len(listed) > 0 {
		var skipped []string
		for _, e := range listed {
			if !found(e) {
				skipped = append(skipped, e)
			}
		}
		if skipped != nil {
			findings = append(findings, "boot script: skips these overlays= entries, no such file for it: "+strings.Join(skipped, " "))
		}
	}
	entry, hasEntry := "", false
	if prefix != "" && strings.HasPrefix(name, prefix+"-") {
		entry, hasEntry = strings.TrimPrefix(name, prefix+"-"), true
	} else if plain {
		entry, hasEntry = name, true
	}
	userFile := filepath.Join(root, "boot", "overlay-user", name+".dtbo")
	userSteps := fmt.Sprintf("use user_overlays, which every boot script version loads: "+
		"`sudo mkdir -p /boot/overlay-user && sudo cp /%s /boot/overlay-user/`, add the line user_overlays=%s to %s "+
		"(or add %s to that line if there is one)", dtbo, name, envFile, name)
	if contains(listed, name) {
		userSteps += ", remove " + name + " from overlays="
	}
	userSteps += ", then reboot"
	if contains(userListed, name) && exists(userFile) && !sameFile(userFile, filepath.Join(root, dtbo)) {
		return findings, fmt.Sprintf("/boot/overlay-user/%s.dtbo differs from the kernel's /%s (copied before a kernel "+
			"update?): copy it again (sudo cp /%s /boot/overlay-user/), then reboot", name, dtbo, dtbo)
	}
	if (hasEntry && contains(listed, entry)) || (contains(userListed, name) && exists(userFile)) {
		if envPath != "" {
			if st, err := os.Stat(envPath); err == nil {
				if booted, ok := bootTime(root); ok && st.ModTime().After(booted) {
					return findings, envFile + " names the overlay but changed after this boot: reboot"
				}
			}
		}
		return findings, envFile + " names the overlay but this boot did not apply it: when one overlay fails to apply, " +
			"the boot script drops them all (its messages are on the serial console); try with this overlay alone"
	}
	if contains(listed, name) || contains(userListed, name) {
		reason := fmt.Sprintf("user_overlays names %s but /boot/overlay-user/%s.dtbo does not exist", name, name)
		if contains(listed, name) {
			reason = fmt.Sprintf("this boot script looks for /%s/%s-%s.dtbo, which does not exist, so it skips %s",
				filepath.Dir(dtbo), prefix, name, name)
		}
		return findings, reason + ": " + userSteps
	}
	if hasEntry {
		return findings, fmt.Sprintf("the device tree has the HDMI input but leaves it off; this image ships the overlay "+
			"that turns it on: add %s to the overlays= line of %s (keep what is there, separate with a space), then reboot",
			entry, envFile)
	}
	return findings, "the device tree has the HDMI input but leaves it off; this image ships the overlay: " + userSteps
}

func bootTime(root string) (time.Time, bool) {
	f := strings.Fields(readFile(filepath.Join(root, "proc", "uptime")))
	if len(f) == 0 {
		return time.Time{}, false
	}
	up, err := strconv.ParseFloat(f[0], 64)
	if err != nil {
		return time.Time{}, false
	}
	return time.Now().Add(-time.Duration(up * float64(time.Second))), true
}

// DiagnoseHDMI explains why the board shows no HDMI input: findings, then a verdict line starting
// with "=> ".
func DiagnoseHDMI(root, release string) []string {
	out := []string{"kernel " + release}
	nodes := dtNodes(root)
	for _, n := range nodes {
		line := fmt.Sprintf("device tree: %s status=%s", n.path, n.status)
		if n.compatible != "" {
			line += " (" + n.compatible + ")"
		}
		out = append(out, line)
	}
	if len(nodes) == 0 {
		out = append(out, "device tree: no HDMI receiver node")
	}
	drivers := boundDrivers(root)
	var names []string
	for n := range drivers {
		names = append(names, n)
	}
	sort.Strings(names)
	bound := false
	for _, n := range names {
		if len(drivers[n]) > 0 {
			bound = true
			out = append(out, fmt.Sprintf("driver %s: bound to %s", n, strings.Join(drivers[n], ", ")))
		} else {
			out = append(out, fmt.Sprintf("driver %s: loaded, bound to nothing", n))
		}
	}
	files, builtin := modules(root, release)
	for _, f := range files {
		out = append(out, "module file: "+f)
	}
	for _, b := range builtin {
		out = append(out, "built into the kernel: "+b)
	}
	config := kernelConfig(root, release)
	for _, l := range config {
		out = append(out, "kernel config: "+l)
	}
	shipped := shippedOverlays(root)
	for _, s := range shipped {
		out = append(out, "overlay available: "+s)
	}
	if st, err := os.Stat(filepath.Join(root, "sys", "firmware", "devicetree", "base")); err != nil || !st.IsDir() {
		return append(out, "=> this machine has no device tree, so no SoC HDMI input: use a USB capture card")
	}
	var receivers, enabled []dtNode
	for _, n := range nodes {
		if strings.HasPrefix(n.path, "reserved-memory") {
			continue
		}
		receivers = append(receivers, n)
		if n.status == "okay" || n.status == "ok" {
			enabled = append(enabled, n)
		}
	}
	hasDriver := len(drivers) > 0 || len(files) > 0 || len(builtin) > 0
	for _, l := range config {
		hasDriver = hasDriver || driverConfig(l)
	}
	var verdict string
	switch {
	case len(receivers) == 0:
		verdict = "this kernel's device tree does not describe the HDMI input: use a board image made for HDMI input"
	case len(enabled) == 0 && len(shipped) > 0:
		chosen, _ := hdmiOverlay(root)
		if chosen == "" {
			verdict = "the device tree has the HDMI input but leaves it off; this image ships several overlays that may turn " +
				"it on (" + strings.Join(shipped, ", ") + "): doctor does not choose, see the image's documentation for the one " +
				"made for this board"
			break
		}
		var findings []string
		findings, verdict = overlayAdvice(root, chosen)
		out = append(out, findings...)
	case len(enabled) == 0:
		verdict = "the device tree has the HDMI input but leaves it off (status disabled): it needs a device tree " +
			"overlay that turns it on, or a board image that enables it"
	case !hasDriver:
		verdict = "the HDMI input is on in the device tree, but this kernel has no driver for it"
	case bound:
		verdict = "the driver is bound but made no video device: its start-up failed; see `sudo dmesg | grep -i hdmirx`"
	case len(files) > 0 && len(drivers) == 0:
		mod := strings.SplitN(filepath.Base(files[0]), ".", 2)[0]
		verdict = "the driver is a module that is not loaded: try `sudo modprobe " + mod + "`, then check again"
	default:
		verdict = "the driver did not take the HDMI input: see `sudo dmesg | grep -i hdmirx`"
	}
	return append(out, "=> "+verdict)
}
