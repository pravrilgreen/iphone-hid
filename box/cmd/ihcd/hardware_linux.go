package main

import (
	"log"

	"github.com/pravrilgreen/iphone-hid/box/internal/board"
	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/input"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
)

// hardware is the board's own phone: the USB gadget, opened when first needed and again after it
// was set up anew, and the HDMI input (videoDev "auto": the board's HDMI receiver).
func hardware(videoDev string, edid bool, logger *log.Logger) (hid.Sink, video.Source, error) {
	sink := hid.NewReopening(hid.SystemPaths, hid.GadgetName, input.DefaultConfig.WriteTimeout)
	dev := videoDev
	var find func() string
	if dev == "auto" {
		// the receiver may register after the service starts: look for it again on each start
		find = func() string { return board.HDMIInput("/") }
		dev = find()
		if dev == "" {
			dev = "/dev/video0"
			logger.Printf("no HDMI receiver found (ihcd doctor says why): trying %s", dev)
		}
	}
	vs := &video.V4L2Source{Path: dev, Find: find}
	if edid {
		vs.EDID = video.EDID1080p60("iphone-hid")
	}
	return sink, vs, nil
}

// openGadget opens the gadget's nodes as they are now.
func openGadget() (hid.Sink, error) {
	return hid.OpenGadget(hid.SystemPaths, hid.GadgetName, input.DefaultConfig.WriteTimeout)
}
