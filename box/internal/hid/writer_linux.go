package hid

import (
	"errors"
	"fmt"
	"os"
	"sync"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

// Gadget writes reports to the /dev/hidgN nodes of the box's gadget.
//
// f_hid keeps one report in flight per interface: write() queues it, and poll() reports the node
// writable again once the host has taken it (its IN transfer completed). So a report counts as
// delivered when the phone has actually polled it, and a phone that stops polling (asleep, locked)
// shows up as a timeout instead of a hang.
type Gadget struct {
	paths   Paths
	name    string
	udc     string
	profile string
	timeout time.Duration

	mu    sync.Mutex
	nodes map[string]*node
}

type node struct {
	path string
	fd   int
	mu   sync.Mutex
}

// OpenGadget opens the nodes of the named gadget. timeout bounds how long a report may wait for
// the phone to take the previous one.
func OpenGadget(p Paths, name string, timeout time.Duration) (*Gadget, error) {
	if name == "" {
		name = GadgetName
	}
	paths := p.Nodes(name)
	if len(paths) == 0 {
		return nil, fmt.Errorf("no USB gadget %q with HID functions: set it up with `ihcd gadget up`", name)
	}
	if _, ok := paths["absolute"]; !ok {
		return nil, fmt.Errorf("the gadget %q has no absolute pointer (profile %q): set it up again", name,
			p.GadgetProfile(name))
	}
	g := &Gadget{paths: p, name: name, udc: p.BoundUDC(name), profile: p.GadgetProfile(name), timeout: timeout,
		nodes: map[string]*node{}}
	for fn, path := range paths {
		flags := unix.O_WRONLY
		if Functions[fn].OutReports {
			flags = unix.O_RDWR
		}
		fd, err := unix.Open(path, flags|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
		if err != nil {
			g.Close()
			return nil, fmt.Errorf("cannot open %s: %w", path, err)
		}
		g.nodes[fn] = &node{path: path, fd: fd}
	}
	return g, nil
}

// Stale tells whether the open nodes are no longer the gadget's (it was set up again: new nodes, or
// the same names made anew), so writes to them can never reach the phone.
func (g *Gadget) Stale() bool {
	cur := g.paths.Nodes(g.name)
	g.mu.Lock()
	defer g.mu.Unlock()
	if len(cur) != len(g.nodes) {
		return true
	}
	for fn, n := range g.nodes {
		var open, now unix.Stat_t
		if cur[fn] != n.path || unix.Fstat(n.fd, &open) != nil || unix.Stat(n.path, &now) != nil ||
			open.Ino != now.Ino || open.Rdev != now.Rdev {
			return true
		}
	}
	return g.paths.BoundUDC(g.name) != g.udc
}

// Pointer sends the absolute pointer state.
func (g *Gadget) Pointer(p Pointer) error { return g.write("absolute", p.Report()) }

// Keyboard sends the keyboard state.
func (g *Gadget) Keyboard(ks KeyState) error {
	r, err := KeyboardReport(ks.Mods, ks.Keys)
	if err != nil {
		return err
	}
	return g.write("keyboard", r)
}

// Consumer sends the consumer controls held down.
func (g *Gadget) Consumer(bits uint32) error { return g.write("consumer", ConsumerReport(bits)) }

// Sync waits until the phone has taken every report written so far.
func (g *Gadget) Sync(timeout time.Duration) error {
	for _, n := range g.nodeList() {
		n.mu.Lock()
		ok, err := writable(n.fd, timeout)
		n.mu.Unlock()
		if err != nil {
			return err
		}
		if !ok {
			return g.notTaken()
		}
	}
	return nil
}

// Link reports the USB state.
func (g *Gadget) Link() Link {
	st := g.paths.UDCState(g.udc)
	return Link{UDC: g.udc, State: st, Connected: st == "configured", Profile: g.profile}
}

// Wake signals USB remote wakeup.
func (g *Gadget) Wake(timeout time.Duration) (string, string, error) {
	return g.paths.Wake(g.udc, timeout)
}

// Close closes the nodes.
func (g *Gadget) Close() error {
	for _, n := range g.nodeList() {
		n.mu.Lock()
		if n.fd >= 0 {
			_ = unix.Close(n.fd)
			n.fd = -1
		}
		n.mu.Unlock()
	}
	return nil
}

func (g *Gadget) nodeList() []*node {
	g.mu.Lock()
	defer g.mu.Unlock()
	out := make([]*node, 0, len(g.nodes))
	for _, n := range g.nodes {
		out = append(out, n)
	}
	return out
}

func (g *Gadget) write(fn string, report []byte) error {
	g.mu.Lock()
	n := g.nodes[fn]
	g.mu.Unlock()
	if n == nil {
		return fmt.Errorf("the gadget has no %s interface (profile %q)", fn, g.profile)
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if n.fd < 0 {
		return errors.New("the gadget is closed")
	}
	ok, err := writable(n.fd, g.timeout)
	if err != nil {
		return err
	}
	if !ok {
		return g.notTaken()
	}
	for {
		w, err := unix.Write(n.fd, report)
		switch {
		case err == unix.EINTR:
			continue
		case err == unix.EAGAIN:
			if ok, err := writable(n.fd, g.timeout); err != nil {
				return err
			} else if !ok {
				return g.notTaken()
			}
			continue
		case errors.Is(err, unix.ESHUTDOWN), errors.Is(err, unix.ENODEV), errors.Is(err, unix.EPIPE),
			errors.Is(err, unix.ECONNRESET):
			return ErrNotConnected
		case err != nil:
			return fmt.Errorf("%s: %w", n.path, err)
		case w != len(report):
			return fmt.Errorf("%s: short write (%d of %d bytes)", n.path, w, len(report))
		}
		return nil
	}
}

func (g *Gadget) notTaken() error {
	if st := g.paths.UDCState(g.udc); st != "" && st != "configured" && st != "suspended" {
		return ErrNotConnected
	}
	return ErrNotTaken
}

// writable waits until the node accepts a new report (the one in flight was taken).
func writable(fd int, timeout time.Duration) (bool, error) {
	deadline := time.Now().Add(timeout)
	for {
		left := time.Until(deadline)
		if left < 0 {
			left = 0
		}
		fds := []unix.PollFd{{Fd: int32(fd), Events: unix.POLLOUT}}
		n, err := unix.Poll(fds, int((left+time.Millisecond-1)/time.Millisecond))
		if err == unix.EINTR {
			continue
		}
		if err != nil {
			return false, err
		}
		if n > 0 {
			if fds[0].Revents&(unix.POLLERR|unix.POLLHUP|unix.POLLNVAL) != 0 {
				return false, ErrNotConnected
			}
			if fds[0].Revents&unix.POLLOUT != 0 {
				return true, nil
			}
		}
		if left == 0 {
			return false, nil
		}
	}
}

var _ Sink = (*Gadget)(nil)

// NodeError tells whether err means the node is missing (the gadget was torn down).
func NodeError(err error) bool {
	return errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ENODEV)
}
