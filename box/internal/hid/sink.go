package hid

import (
	"errors"
	"time"
)

// Errors a Sink returns when a report cannot reach the phone.
var (
	// ErrNotConnected: the phone has not enumerated the gadget (unplugged, or "Allow accessory" pending).
	ErrNotConnected = errors.New("the iPhone is not connected (or has not allowed the accessory)")
	// ErrNotTaken: the phone stopped polling (asleep or locked, the USB bus suspended).
	ErrNotTaken = errors.New("the iPhone is not taking input (asleep or locked?)")
)

// Link is the USB link to the phone as the device controller sees it.
type Link struct {
	UDC       string `json:"udc,omitempty"`
	State     string `json:"state"` // configured, suspended, not attached, ...
	Connected bool   `json:"connected"`
	Profile   string `json:"profile,omitempty"`
}

// Sink takes reports to the phone. Each call returns once the report is queued on the USB port;
// Sync waits until the phone has taken everything queued.
type Sink interface {
	Pointer(p Pointer) error
	Keyboard(ks KeyState) error
	Consumer(bits uint32) error
	Sync(timeout time.Duration) error
	Link() Link
	Wake(timeout time.Duration) (before, after string, err error)
	Close() error
}
