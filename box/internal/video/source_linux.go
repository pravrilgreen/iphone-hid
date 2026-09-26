package video

import (
	"context"
	"fmt"
	"sync"
	"time"
)

// V4L2Source captures from an HDMI receiver's V4L2 node.
type V4L2Source struct {
	Path     string
	EDID     []byte // written once before the first capture (nil: leave the receiver's EDID)
	edidOnce sync.Once
	edidErr  error
}

// Describe names the source.
func (s *V4L2Source) Describe() string { return s.Path }

// Run streams frames until ctx ends or the stream fails.
func (s *V4L2Source) Run(ctx context.Context, emit func(*Raw)) error {
	if s.EDID != nil {
		s.edidOnce.Do(func() {
			s.edidErr = SetEDID(s.Path, s.EDID)
			if s.edidErr == nil {
				time.Sleep(time.Second) // the phone sees a display plugged in again and re-negotiates
			}
		})
	}
	d, err := OpenDevice(s.Path, 4)
	if err != nil {
		return err
	}
	defer d.Close()
	for ctx.Err() == nil {
		if err := d.Read(2*time.Second, emit); err != nil {
			if _, qerr := QuerySignal(s.Path); qerr != nil {
				return fmt.Errorf("%s: %w", s.Path, qerr)
			}
			return err
		}
	}
	return nil
}
