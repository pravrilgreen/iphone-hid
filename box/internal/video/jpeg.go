package video

// Encoder turns images into JPEG. An Encoder is not safe for concurrent use: give each goroutine
// its own.
type Encoder interface {
	Encode(img *Image, quality int) ([]byte, error)
	Close()
}

// EncoderName says which JPEG encoder this build uses.
var EncoderName = encoderName

// NewEncoder returns this build's JPEG encoder (libjpeg-turbo when built with the turbojpeg tag).
func NewEncoder() Encoder { return newEncoder() }
