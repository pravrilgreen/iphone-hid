package video

import "testing"

// BenchmarkEncodePhoneScreen encodes the phone screen of a 1080p frame (498 x 1080, NV12 source).
func BenchmarkEncodePhoneScreen(b *testing.B) {
	raw := nv12(1920, 1080, 120, 110, 140)
	for i := range raw.Planes[0] {
		raw.Planes[0][i] = byte(i * 7) // some detail, not a flat picture
	}
	img, err := Crop(raw, Layout{}.Rect(1920, 1080), nil)
	if err != nil {
		b.Fatal(err)
	}
	enc := NewEncoder()
	defer enc.Close()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, err := enc.Encode(img, 80); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkCropPhoneScreen copies and level-expands the phone screen out of a 1080p NV12 frame.
func BenchmarkCropPhoneScreen(b *testing.B) {
	raw := nv12(1920, 1080, 120, 110, 140)
	r := Layout{}.Rect(1920, 1080)
	for i := 0; i < b.N; i++ {
		if _, err := Crop(raw, r, nil); err != nil {
			b.Fatal(err)
		}
	}
}
