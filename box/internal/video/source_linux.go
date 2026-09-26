package video

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// V4L2Source captures from an HDMI receiver's V4L2 node.
type V4L2Source struct {
	Path string
	EDID []byte        // written before capturing, until it succeeds once (nil: leave the receiver's EDID)
	Find func() string // if set, finds the node again before each start (the receiver may come up late)

	mu      sync.Mutex
	edidOK  bool
	edidErr error
}

// Describe names the source, and says so when its EDID could not be written.
func (s *V4L2Source) Describe() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.edidErr != nil {
		return fmt.Sprintf("%s (EDID not written: %v)", s.Path, s.edidErr)
	}
	return s.Path
}

// Run streams frames until ctx ends or the stream fails.
func (s *V4L2Source) Run(ctx context.Context, emit func(*Raw)) error {
	s.mu.Lock()
	if s.Find != nil {
		if p := s.Find(); p != "" {
			s.Path = p
		}
	}
	path, writeEDID := s.Path, s.EDID != nil && !s.edidOK
	s.mu.Unlock()
	if writeEDID {
		err := SetEDID(path, s.EDID)
		s.mu.Lock()
		s.edidOK, s.edidErr = err == nil, err
		s.mu.Unlock()
		if err == nil {
			time.Sleep(time.Second) // the phone sees a display plugged in again and re-negotiates
		}
	}
	d, err := OpenDevice(path, 4)
	if err != nil {
		return err
	}
	defer d.Close()
	for ctx.Err() == nil {
		if err := d.Read(2*time.Second, emit); err != nil {
			if _, qerr := QuerySignal(path); qerr != nil {
				return fmt.Errorf("%s: %w", path, qerr)
			}
			return err
		}
	}
	return nil
}
