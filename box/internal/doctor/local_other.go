//go:build !linux

package doctor

import "errors"

// The box runs on Linux; elsewhere (a developer's machine) doctor finds no board to examine.

func kernelRelease() string { return "" }

func querySignal(string) (string, error) { return "", errors.New("HDMI capture needs Linux") }
