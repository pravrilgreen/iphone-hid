package hid

import (
	"fmt"
	"sync"
	"time"
)

// Reopening is a Sink over the box's gadget that opens its nodes when first needed and again after
// the gadget was set up anew (the service starts before the gadget exists, or it was replaced).
type Reopening struct {
	paths   Paths
	name    string
	timeout time.Duration

	mu      sync.Mutex
	g       *Gadget
	tried   time.Time
	openErr error
}

// NewReopening returns a Sink for the named gadget.
func NewReopening(p Paths, name string, timeout time.Duration) *Reopening {
	if name == "" {
		name = GadgetName
	}
	return &Reopening{paths: p, name: name, timeout: timeout}
}

func (r *Reopening) get() (*Gadget, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.g != nil {
		return r.g, nil
	}
	if time.Since(r.tried) < time.Second && r.openErr != nil {
		return nil, r.openErr
	}
	r.tried = time.Now()
	g, err := OpenGadget(r.paths, r.name, r.timeout)
	if err != nil {
		r.openErr = fmt.Errorf("%w: %v", ErrNotConnected, err)
		return nil, r.openErr
	}
	r.g, r.openErr = g, nil
	return g, nil
}

// drop closes the nodes after an error that means they are gone.
func (r *Reopening) check(g *Gadget, err error) error {
	if err != nil && NodeError(err) {
		r.mu.Lock()
		if r.g == g {
			_ = g.Close()
			r.g = nil
		}
		r.mu.Unlock()
	}
	return err
}

// Pointer sends the absolute pointer state.
func (r *Reopening) Pointer(p Pointer) error {
	g, err := r.get()
	if err != nil {
		return err
	}
	return r.check(g, g.Pointer(p))
}

// Keyboard sends the keyboard state.
func (r *Reopening) Keyboard(ks KeyState) error {
	g, err := r.get()
	if err != nil {
		return err
	}
	return r.check(g, g.Keyboard(ks))
}

// Consumer sends the consumer controls held.
func (r *Reopening) Consumer(bits uint32) error {
	g, err := r.get()
	if err != nil {
		return err
	}
	return r.check(g, g.Consumer(bits))
}

// Sync waits until the phone has taken every report.
func (r *Reopening) Sync(timeout time.Duration) error {
	g, err := r.get()
	if err != nil {
		return err
	}
	return r.check(g, g.Sync(timeout))
}

// Link reports the USB state, with or without the nodes open.
func (r *Reopening) Link() Link {
	udc := r.paths.BoundUDC(r.name)
	st := r.paths.UDCState(udc)
	if udc == "" {
		st = "no gadget"
	}
	return Link{UDC: udc, State: st, Connected: st == "configured", Profile: r.paths.GadgetProfile(r.name)}
}

// Wake signals USB remote wakeup.
func (r *Reopening) Wake(timeout time.Duration) (string, string, error) {
	return r.paths.Wake(r.paths.BoundUDC(r.name), timeout)
}

// Close closes the nodes.
func (r *Reopening) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.g != nil {
		err := r.g.Close()
		r.g = nil
		return err
	}
	return nil
}

var _ Sink = (*Reopening)(nil)
