package doctor

import (
	"syscall"

	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// kernelRelease is the running kernel's release, as uname -r prints it.
func kernelRelease() string {
	var u syscall.Utsname
	_ = syscall.Uname(&u)
	var rel []byte
	for _, c := range u.Release {
		if c == 0 {
			break
		}
		rel = append(rel, byte(c))
	}
	return string(rel)
}

// querySignal asks the HDMI receiver what it gets.
func querySignal(node string) (string, error) {
	t, err := video.QuerySignal(node)
	if err != nil {
		return "", err
	}
	return t.String(), nil
}
