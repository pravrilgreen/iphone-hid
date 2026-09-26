package video

// dtd1080p60 is the detailed timing of 1920x1080 at 60 Hz, 148.5 MHz (CEA-861 VIC 16), +hsync
// +vsync, 527 x 296 mm.
var dtd1080p60 = []byte{0x02, 0x3A, 0x80, 0x18, 0x71, 0x38, 0x2D, 0x40, 0x58, 0x2C, 0x45, 0x00,
	0x0F, 0x28, 0x21, 0x00, 0x00, 0x1E}

func checksum(block []byte) {
	sum := 0
	for _, b := range block[:127] {
		sum += int(b)
	}
	block[127] = byte(-sum)
}

// EDID1080p60 is a 256-byte EDID (EDID 1.3 base block and a CEA-861 extension) for an HDMI display
// whose preferred and only video mode is 1920x1080 at 60 Hz, with 2-channel LPCM audio. Written
// to the HDMI input, it makes the phone mirror at 1080p rather than 4K.
func EDID1080p60(name string) []byte {
	base := make([]byte, 128)
	copy(base[0:8], []byte{0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00})
	id := (('I' - 64) << 10) | (('H' - 64) << 5) | ('C' - 64)
	base[8], base[9] = byte(id>>8), byte(id)
	base[10], base[11] = 1, 0 // product code
	base[16], base[17] = 1, 2026-1990
	base[18], base[19] = 1, 3 // EDID 1.3
	base[20] = 0x80           // digital input
	base[21], base[22] = 53, 30
	base[23] = 120 // gamma 2.2
	base[24] = 0x0E
	copy(base[25:35], []byte{0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54}) // sRGB
	copy(base[35:38], []byte{0x20, 0x00, 0x00})                                           // 640x480 at 60 Hz
	for i := 38; i < 54; i++ {
		base[i] = 0x01
	}
	copy(base[54:72], dtd1080p60)
	copy(base[72:90], append([]byte{0, 0, 0, 0xFD, 0, 56, 75, 30, 83, 15, 0, 0x0A}, []byte("      ")...))
	label := []byte(name)
	if len(label) > 12 {
		label = label[:12]
	}
	label = append(label, 0x0A)
	for len(label) < 13 {
		label = append(label, 0x20)
	}
	copy(base[90:108], append([]byte{0, 0, 0, 0xFC, 0}, label...))
	copy(base[108:126], append([]byte{0, 0, 0, 0x10, 0}, make([]byte, 13)...))
	base[126] = 1 // one extension block
	checksum(base)

	ext := make([]byte, 128)
	blocks := []byte{0x23, 0x09, 0x04, 0x07, // audio: LPCM, 2 channels, 48 kHz, 16/20/24 bit
		0x41, 0x10, // video: VIC 16 (1080p60)
		0x65, 0x03, 0x0C, 0x00, 0x10, 0x00, // HDMI vendor block, physical address 1.0.0.0
		0xE2, 0x00, 0x4A} // video capability: RGB range selectable, IT and CE underscanned
	ext[0], ext[1] = 0x02, 0x03
	ext[2] = byte(4 + len(blocks))
	ext[3] = 0xC1
	copy(ext[4:], blocks)
	copy(ext[ext[2]:], dtd1080p60)
	checksum(ext)
	return append(base, ext...)
}
