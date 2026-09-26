package doctor

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const release = "6.1.115-vendor-rk35xx"

func init() { settle = 0 }

func write(t *testing.T, root, path, content string, mode os.FileMode) {
	t.Helper()
	p := filepath.Join(root, path)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(content), mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(p, mode); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, root, path string) string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(root, path))
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// fake is a board and a box doctor looks at, with the commands it ran.
type fake struct {
	root     string
	sys      System
	commands []string
	inactive map[string]bool
	health   error
}

func newFake(t *testing.T) *fake {
	f := &fake{root: t.TempDir(), inactive: map[string]bool{}}
	write(t, f.root, "sys/firmware/devicetree/base/model", "Xunlong Orange Pi 5 Plus\x00", 0o644)
	write(t, f.root, "proc/mounts", "configfs /sys/kernel/config configfs rw 0 0\n", 0o644)
	write(t, f.root, "lib/modules/"+release+"/kernel/drivers/usb/gadget/function/usb_f_hid.ko", "", 0o644)
	write(t, f.root, "lib/modules/"+release+"/kernel/drivers/usb/gadget/libcomposite.ko", "", 0o644)
	f.sys = System{
		Root: f.root, Release: release, Service: "ihc", Port: 8000,
		Command: func(name string, args ...string) (string, error) {
			c := strings.Join(append([]string{name}, args...), " ")
			f.commands = append(f.commands, c)
			if name == "systemctl" && len(args) == 2 {
				switch args[0] {
				case "is-enabled":
					return "enabled", nil
				case "is-active":
					if f.inactive[args[1]] {
						return "failed", errors.New("exit status 3")
					}
					return "active", nil
				}
			}
			if name == "journalctl" {
				return "ihcd: listen tcp :8000: address already in use", nil
			}
			return "", nil
		},
		Health: func(string) error { return f.health },
		Signal: func(string) (string, error) { return "1920x1080p60", nil },
		Owner:  func(string) (int, int, error) { return os.Getuid(), os.Getgid(), nil },
		Addrs:  func() []string { return []string{"192.168.1.20"} },
		Now:    func() time.Time { return time.Date(2026, 9, 26, 12, 0, 0, 0, time.UTC) },
	}
	return f
}

// ready makes the fake a working box: the USB device port, the gadget bound and taken by the
// iPhone, the HDMI input, the services.
func (f *fake) ready(t *testing.T) {
	r := f.root
	write(t, r, "sys/class/udc/fc000000.usb/state", "configured\n", 0o644)
	g := "sys/kernel/config/usb_gadget/ihc/"
	write(t, r, g+"UDC", "fc000000.usb\n", 0o644)
	write(t, r, g+"strings/0x409/serialnumber", "ihc-RA\n", 0o644)
	for i, fn := range []string{"keyboard", "consumer", "mouse", "absolute"} {
		if err := os.MkdirAll(filepath.Join(r, g+"configs/c.1/hid."+fn), 0o755); err != nil {
			t.Fatal(err)
		}
		majmin := "236:" + string(rune('0'+i))
		write(t, r, g+"functions/hid."+fn+"/dev", majmin+"\n", 0o644)
		write(t, r, "sys/dev/char/"+majmin+"/uevent", "MAJOR=236\nDEVNAME=hidg"+string(rune('0'+i))+"\n", 0o644)
		write(t, r, "dev/hidg"+string(rune('0'+i)), "", 0o660)
	}
	write(t, r, "sys/class/video4linux/video0/name", "rk_hdmirx\n", 0o644)
	write(t, r, "etc/systemd/system/ihcd.service", "", 0o644)
	write(t, r, "etc/systemd/system/ihcd-gadget.service", "", 0o644)
	write(t, r, "etc/udev/rules.d/99-ihc.rules", "", 0o644)
	write(t, r, "var/lib/ihc/token", "secret\n", 0o600)
}

func find(t *testing.T, rs []Result, id string) Result {
	t.Helper()
	for _, r := range rs {
		if r.ID == id {
			return r
		}
	}
	t.Fatalf("no check %s in %+v", id, rs)
	return Result{}
}

func report(rs []Result, opts Options) string {
	var b bytes.Buffer
	Report(&b, rs, opts)
	return b.String()
}

func TestAReadyBoxIsAllGood(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	rs := Run(f.sys, Options{})
	for _, r := range rs {
		if r.Status != OK {
			t.Errorf("%s: %s %s (%s)", r.ID, r.Status, r.Found, r.Advice)
		}
	}
	out := report(rs, Options{})
	for _, want := range []string{"Orange Pi 5 Plus", "profile RA on fc000000.usb", "/dev/video0", "receiving 1920x1080p60",
		"http://192.168.1.20:8000", "All good"} {
		if !strings.Contains(out, want) {
			t.Errorf("report lacks %q:\n%s", want, out)
		}
	}
}

func TestPortInHostModeIsSwitchedToDevice(t *testing.T) {
	f := newFake(t)
	write(t, f.root, "sys/firmware/devicetree/base/usb@fc000000/dr_mode", "otg\x00", 0o644)
	write(t, f.root, "sys/class/usb_role/fc000000.usb-role-switch/role", "host\n", 0o644)
	if err := os.MkdirAll(filepath.Join(f.root, "sys/kernel/config/usb_gadget"), 0o755); err != nil {
		t.Fatal(err)
	}
	rs := Run(f.sys, Options{})
	port := find(t, rs, "usb-port")
	if port.Status != Fail || port.Fix == nil || !strings.Contains(strings.Join(port.Notes, "\n"), "dr_mode otg") {
		t.Fatalf("usb-port %+v", port)
	}
	if g := find(t, rs, "gadget"); g.Fix != nil || !strings.Contains(g.Advice, "USB device port first") {
		t.Fatalf("the gadget cannot be fixed before the port: %+v", g)
	}
	if out := report(rs, Options{}); !strings.Contains(out, "sudo ihcd doctor --fix fixes 1 of them") {
		t.Fatalf("report:\n%s", out)
	}
	Run(f.sys, Options{Fix: true})
	if got := read(t, f.root, "sys/class/usb_role/fc000000.usb-role-switch/role"); got != "device" {
		t.Fatalf("role %q", got)
	}
}

func TestHostOnlyDeviceTreeGetsAdvice(t *testing.T) {
	f := newFake(t)
	write(t, f.root, "sys/firmware/devicetree/base/usb@fc000000/dr_mode", "host\x00", 0o644)
	write(t, f.root, "boot/dtb/rockchip/overlay/rk3588-dwc3-peripheral.dtbo", "", 0o644)
	port := find(t, Run(f.sys, Options{}), "usb-port")
	if port.Fix != nil || !strings.Contains(port.Advice, "dr_mode") ||
		!strings.Contains(strings.Join(port.Notes, "\n"), "rk3588-dwc3-peripheral.dtbo") {
		t.Fatalf("usb-port %+v", port)
	}
}

func TestGadgetFrameworkIsLoaded(t *testing.T) {
	f := newFake(t)
	write(t, f.root, "proc/mounts", "sysfs /sys sysfs rw 0 0\n", 0o644)
	r := find(t, Run(f.sys, Options{}), "gadget-support")
	if r.Status != Fail || r.Fix == nil {
		t.Fatalf("%+v", r)
	}
	Run(f.sys, Options{Fix: true})
	got := strings.Join(f.commands, "\n")
	if !strings.Contains(got, "mount -t configfs none /sys/kernel/config") || !strings.Contains(got, "modprobe libcomposite") {
		t.Fatalf("commands:\n%s", got)
	}
}

func TestKernelWithoutGadgetSupport(t *testing.T) {
	f := newFake(t)
	if err := os.RemoveAll(filepath.Join(f.root, "lib/modules")); err != nil {
		t.Fatal(err)
	}
	r := find(t, Run(f.sys, Options{}), "gadget-support")
	if r.Status != Fail || r.Fix != nil || !strings.Contains(r.Advice, "vendor image") {
		t.Fatalf("%+v", r)
	}
}

func TestAnotherGadgetIsUnbound(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	if err := os.RemoveAll(filepath.Join(f.root, "sys/kernel/config/usb_gadget/ihc")); err != nil {
		t.Fatal(err)
	}
	write(t, f.root, "sys/kernel/config/usb_gadget/g1/UDC", "fc000000.usb\n", 0o644)
	write(t, f.root, "lib/systemd/system/adbd.service", "", 0o644)
	rs := Run(f.sys, Options{})
	other := find(t, rs, "other-gadget")
	if other.Status != Warn || !strings.Contains(other.Advice, "adbd.service") {
		t.Fatalf("%+v", other)
	}
	if g := find(t, rs, "gadget"); g.Fix != nil || !strings.Contains(g.Advice, "Other gadgets") {
		t.Fatalf("gadget %+v", g)
	}
	Run(f.sys, Options{Fix: true})
	if got := strings.TrimSpace(read(t, f.root, "sys/kernel/config/usb_gadget/g1/UDC")); got != "" {
		t.Fatalf("still bound to %q", got)
	}
	if !strings.Contains(strings.Join(f.commands, "\n"), "systemctl restart ihcd-gadget") {
		t.Fatalf("the gadget was not set up after: %v", f.commands)
	}
}

func TestPhoneStates(t *testing.T) {
	for state, want := range map[string]string{
		"configured": "connected", "suspended": "asleep", "not attached": "nothing is attached", "default": "Allow",
	} {
		f := newFake(t)
		f.ready(t)
		write(t, f.root, "sys/class/udc/fc000000.usb/state", state+"\n", 0o644)
		r := find(t, Run(f.sys, Options{}), "iphone")
		if !strings.Contains(r.Found+r.Advice, want) {
			t.Errorf("%s: %+v", state, r)
		}
	}
}

func TestNodesTheServiceCannotWrite(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	if err := os.Chmod(filepath.Join(f.root, "dev/hidg1"), 0o600); err != nil {
		t.Fatal(err)
	}
	r := find(t, Run(f.sys, Options{}), "nodes")
	if r.Status != Warn || !strings.Contains(r.Found, "hidg1") {
		t.Fatalf("%+v", r)
	}
	Run(f.sys, Options{Fix: true})
	if st, _ := os.Stat(filepath.Join(f.root, "dev/hidg1")); st.Mode().Perm() != 0o660 {
		t.Fatalf("mode %v", st.Mode())
	}
}

// armbian fakes Armbian's vendor kernel with the HDMI input off and the overlay shipped.
func armbian(t *testing.T, f *fake, script, overlays string) {
	write(t, f.root, "sys/firmware/devicetree/base/hdmirx-controller@fdee0000/status", "disabled\x00", 0o644)
	write(t, f.root, "sys/firmware/devicetree/base/hdmirx-controller@fdee0000/compatible", "rockchip,rk3588-hdmirx-ctrler\x00", 0o644)
	write(t, f.root, "boot/dtb/rockchip/overlay/rk3588-hdmirx.dtbo", "dtbo", 0o644)
	write(t, f.root, "boot/boot.cmd", script, 0o644)
	write(t, f.root, "boot/armbianEnv.txt", "verbosity=1\noverlay_prefix=rockchip-rk3588\noverlays="+overlays+"\n", 0o644)
}

const newScript = "load ${prefix}dtb/rockchip/overlay/${overlay_prefix}-${overlay_file}.dtbo\n" +
	"elif load ${prefix}dtb/rockchip/overlay/${overlay_file}.dtbo; then\n"

func TestHDMIOverlayOnlyWithFixBoot(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	if err := os.RemoveAll(filepath.Join(f.root, "sys/class/video4linux")); err != nil {
		t.Fatal(err)
	}
	armbian(t, f, newScript, "panthor-gpu")
	rs := Run(f.sys, Options{Fix: true})
	h := find(t, rs, "hdmi-input")
	if h.Status != Fail || h.Fix == nil || !h.Fix.Boot || h.Done != "" {
		t.Fatalf("--fix must not touch the boot configuration: %+v", h)
	}
	if out := report(rs, Options{Fix: true}); !strings.Contains(out, "--fix-boot") {
		t.Fatalf("report:\n%s", out)
	}
	rs = Run(f.sys, Options{Fix: true, Boot: true})
	h = find(t, rs, "hdmi-input")
	if !strings.Contains(h.Done, "Reboot") || h.Error != "" {
		t.Fatalf("%+v", h)
	}
	env := read(t, f.root, "boot/armbianEnv.txt")
	if !strings.Contains(env, "overlays=panthor-gpu rk3588-hdmirx\n") || !strings.Contains(env, "verbosity=1\n") {
		t.Fatalf("env:\n%s", env)
	}
	if got := read(t, f.root, "boot/armbianEnv.txt.ihc-20260926-120000"); !strings.Contains(got, "overlays=panthor-gpu\n") {
		t.Fatalf("backup:\n%s", got)
	}
}

func TestHDMIOverlayOnAnOldBootScriptGoesToUserOverlays(t *testing.T) {
	f := newFake(t)
	armbian(t, f, "load ${prefix}dtb/rockchip/overlay/${overlay_prefix}-${overlay_file}.dtbo\n", "rk3588-hdmirx")
	h := find(t, Run(f.sys, Options{Fix: true, Boot: true}), "hdmi-input")
	if h.Error != "" {
		t.Fatalf("%+v", h)
	}
	env := read(t, f.root, "boot/armbianEnv.txt")
	if !strings.Contains(env, "user_overlays=rk3588-hdmirx\n") || !strings.Contains(env, "overlays=\n") {
		t.Fatalf("env:\n%s", env)
	}
	if read(t, f.root, "boot/overlay-user/rk3588-hdmirx.dtbo") != "dtbo" {
		t.Fatal("the overlay was not copied")
	}
	if Run(f.sys, Options{})[0].ID != "board" {
		t.Fatal("order")
	}
	if h := find(t, Run(f.sys, Options{}), "hdmi-input"); h.Fix != nil {
		t.Fatalf("once named, the fix is not offered again: %+v", h)
	}
}

func TestServiceDownAndAPIPort(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	f.inactive["ihcd"] = true
	f.health = errors.New("connection refused")
	write(t, f.root, "etc/default/ihc", "# settings\nIHCD_ARGS=\"--id iphone-a01 --addr :8080\"\n", 0o644)
	rs := Run(f.sys, Options{})
	s := find(t, rs, "service")
	if s.Status != Fail || !strings.Contains(s.Found, "not running: ihcd") ||
		!strings.Contains(strings.Join(s.Notes, "\n"), "address already in use") {
		t.Fatalf("%+v", s)
	}
	if a := find(t, rs, "api"); !strings.Contains(a.Found, "port 8080") {
		t.Fatalf("%+v", a)
	}
	Run(f.sys, Options{Fix: true})
	if !strings.Contains(strings.Join(f.commands, "\n"), "systemctl restart ihcd") {
		t.Fatalf("commands %v", f.commands)
	}
}

func TestTokenOthersCanRead(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	if err := os.Chmod(filepath.Join(f.root, "var/lib/ihc/token"), 0o644); err != nil {
		t.Fatal(err)
	}
	if r := find(t, Run(f.sys, Options{}), "token"); r.Status != Warn || r.Fix == nil {
		t.Fatalf("%+v", r)
	}
	Run(f.sys, Options{Fix: true})
	if st, _ := os.Stat(filepath.Join(f.root, "var/lib/ihc/token")); st.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v", st.Mode())
	}
}

func TestNotInstalled(t *testing.T) {
	f := newFake(t)
	f.ready(t)
	if err := os.RemoveAll(filepath.Join(f.root, "etc/systemd")); err != nil {
		t.Fatal(err)
	}
	rs := Run(f.sys, Options{})
	if r := find(t, rs, "service"); r.Status != Warn || !strings.Contains(r.Advice, ".run") {
		t.Fatalf("%+v", r)
	}
	for _, r := range rs {
		if r.ID == "api" || r.ID == "token" {
			t.Fatalf("%s is checked without the service", r.ID)
		}
	}
}
