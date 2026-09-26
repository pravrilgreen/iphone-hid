package board

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const release = "6.1.115-vendor-rk35xx"

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func fakeBoard(t *testing.T) string {
	root := t.TempDir()
	for _, d := range []string{"sys/firmware/devicetree/base/reserved-memory", "sys/bus/platform/drivers",
		"lib/modules/" + release, "boot", "proc"} {
		if err := os.MkdirAll(filepath.Join(root, d), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return root
}

func makeNode(t *testing.T, root, name, status string) {
	n := filepath.Join(root, "sys/firmware/devicetree/base", name)
	write(t, filepath.Join(n, "compatible"), "rockchip,rk3588-hdmirx-ctrler\x00")
	if status != "" {
		write(t, filepath.Join(n, "status"), status+"\x00")
	}
}

const oldScript = "for overlay_file in ${overlays}; do\n" +
	"\tif load ${devtype} ${devnum}:${distro_bootpart} ${load_addr} ${prefix}dtb/rockchip/overlay/${overlay_prefix}-${overlay_file}.dtbo; then\n" +
	"\t\tfdt apply ${load_addr} || setenv overlay_error \"true\"\n\tfi\ndone\n"

const newScript = oldScript + "\telif load ${devtype} ${devnum}:${distro_bootpart} ${load_addr} ${prefix}dtb/rockchip/overlay/${overlay_file}.dtbo; then\n"

// armbian fakes the Orange Pi 5 Plus on Armbian's vendor kernel, as seen on a real board.
func armbian(t *testing.T, script, overlays, userOverlays string, uptime, editedAgo time.Duration) string {
	root := fakeBoard(t)
	makeNode(t, root, "hdmirx-controller@fdee0000", "disabled")
	for _, n := range []string{"rk3588-hdmirx", "rk3588-can0-m0", "rockchip-rk3588-panthor-gpu"} {
		write(t, filepath.Join(root, "boot/dtb/rockchip/overlay", n+".dtbo"), "")
	}
	if script != "" {
		write(t, filepath.Join(root, "boot/boot.cmd"), script)
	}
	env := "overlay_prefix=rockchip-rk3588\noverlays=" + overlays + "\n"
	if userOverlays != "" {
		env += "user_overlays=" + userOverlays + "\n"
	}
	envPath := filepath.Join(root, "boot/armbianEnv.txt")
	write(t, envPath, env)
	edited := time.Now().Add(-editedAgo)
	if err := os.Chtimes(envPath, edited, edited); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, "proc/uptime"), fmt.Sprintf("%.0f 0\n", uptime.Seconds()))
	return root
}

func verdict(t *testing.T, root string) string {
	lines := DiagnoseHDMI(root, release)
	v := lines[len(lines)-1]
	if !strings.HasPrefix(v, "=> ") {
		t.Fatalf("no verdict: %v", lines)
	}
	return v
}

func TestOldBootScriptNeedsUserOverlays(t *testing.T) {
	// before Armbian 24.11, overlays= reaches only <overlay_prefix>-<entry>.dtbo: rk3588-hdmirx is skipped
	root := armbian(t, oldScript, "panthor-gpu rk3588-hdmirx", "", 100*time.Second, time.Hour)
	v := verdict(t, root)
	if !strings.Contains(v, "user_overlays=rk3588-hdmirx") || !strings.Contains(v, "remove rk3588-hdmirx from overlays=") {
		t.Fatal(v)
	}
	lines := DiagnoseHDMI(root, release)
	if !strings.Contains(strings.Join(lines, "\n"), "skips these overlays= entries, no such file for it: rk3588-hdmirx") {
		t.Fatal("the skipped entry is not named")
	}
}

func TestNewBootScriptTakesThePlainName(t *testing.T) {
	v := verdict(t, armbian(t, newScript, "panthor-gpu", "", 100*time.Second, time.Hour))
	if !strings.Contains(v, "add rk3588-hdmirx to the overlays= line") {
		t.Fatal(v)
	}
}

func TestListedButNotRebooted(t *testing.T) {
	v := verdict(t, armbian(t, newScript, "rk3588-hdmirx", "", 2*time.Hour, time.Minute))
	if !strings.Contains(v, "changed after this boot: reboot") {
		t.Fatal(v)
	}
}

func TestUserOverlayListedButNotApplied(t *testing.T) {
	root := armbian(t, oldScript, "panthor-gpu", "rk3588-hdmirx", 100*time.Second, time.Hour)
	write(t, filepath.Join(root, "boot/overlay-user/rk3588-hdmirx.dtbo"), "")
	if v := verdict(t, root); !strings.Contains(v, "did not apply it") {
		t.Fatal(v)
	}
}

func TestNoDeviceTree(t *testing.T) {
	if v := verdict(t, t.TempDir()); !strings.Contains(v, "no device tree") {
		t.Fatal(v)
	}
}

func TestHDMIInputAndDeviceID(t *testing.T) {
	root := t.TempDir()
	write(t, filepath.Join(root, "sys/class/video4linux/video0/name"), "rkisp_mainpath\n")
	write(t, filepath.Join(root, "sys/class/video4linux/video11/name"), "stream_hdmirx\n")
	write(t, filepath.Join(root, "sys/class/video4linux/video2/name"), "other\n")
	if got := HDMIInput(root); got != "/dev/video11" {
		t.Fatalf("HDMI input %q", got)
	}
	if nodes := VideoNodes(root); nodes[1].Path != "/dev/video2" || nodes[2].Path != "/dev/video11" {
		t.Fatalf("not in natural order: %+v", nodes)
	}
	write(t, filepath.Join(root, "proc/device-tree/serial-number"), "a1b2c3d4e5f6\x00")
	id := DeviceID(root)
	if !strings.HasPrefix(id, "iphone-") || len(id) != 13 || id != DeviceID(root) {
		t.Fatalf("device id %q", id)
	}
}
