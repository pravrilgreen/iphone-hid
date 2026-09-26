//go:build !linux

package main

import (
	"errors"
	"log"

	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// The box's USB gadget and HDMI input are Linux interfaces; elsewhere ihcd runs the simulated phone.
var errNeedsLinux = errors.New("the USB gadget and the HDMI input need the board's Linux: use ihcd serve --sim here")

func hardware(string, bool, *log.Logger) (hid.Sink, video.Source, error) {
	return nil, nil, errNeedsLinux
}

func openGadget() (hid.Sink, error) { return nil, errNeedsLinux }
