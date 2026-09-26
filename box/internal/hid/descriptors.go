// Package hid turns the box into the iPhone's USB keyboard and absolute pointer: the report
// descriptors, the Linux USB gadget set-up (configfs) and the writers for the /dev/hidgN nodes.
//
// Each report type is its own USB interface, without report IDs, in the order keyboard, consumer
// control, relative mouse, absolute pointer. That is the layout an iPhone 15 follows (its pointer
// lands where the absolute report puts it); iOS brings its soft keyboard back reliably only when a
// pointer interface does not directly follow the keyboard, so consumer control sits in between.
// The relative mouse interface is declared but never used: the confirmed layout has it.
package hid

// Report descriptors (HID 1.11 and the HID Usage Tables).

// DescKeyboard is a boot keyboard: 8 modifier bits, a reserved byte, 6 key usages, 5 LED bits out.
var DescKeyboard = []byte{
	0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, // Generic Desktop / Keyboard / Collection (Application)
	0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, // Keyboard page, Left Control .. Right GUI
	0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02, // 8 modifier bits
	0x95, 0x01, 0x75, 0x08, 0x81, 0x01, // reserved byte
	0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x95, 0x05, 0x75, 0x01, 0x91, 0x02, // 5 LED bits (output)
	0x95, 0x01, 0x75, 0x03, 0x91, 0x01, // LED padding
	0x05, 0x07, 0x19, 0x00, 0x2A, 0xFF, 0x00, 0x15, 0x00, 0x26, 0xFF, 0x00,
	0x95, 0x06, 0x75, 0x08, 0x81, 0x00, // 6 key usages (array)
	0xC0,
}

// ConsumerUsages are the 24 one-bit consumer controls of the consumer report, in bit order.
var ConsumerUsages = [24]uint16{
	0x0E9, 0x0EA, 0x0E2, 0x0CD, 0x0B5, 0x0B6, 0x0B7, 0x0B8, 0x18A, 0x221, 0x22A, 0x223,
	0x224, 0x225, 0x226, 0x227, 0x183, 0x196, 0x192, 0x1B1, 0x194, 0x206, 0x0B2, 0x0B4,
}

// DescConsumer is a 24-bit bitmap of the ConsumerUsages.
var DescConsumer = func() []byte {
	d := []byte{
		0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01, // Consumer / Consumer Control / Collection (Application)
		0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x18,
	}
	for _, u := range ConsumerUsages {
		d = append(d, 0x0A, byte(u), byte(u>>8))
	}
	return append(d, 0x81, 0x02, 0xC0)
}()

// DescMouse is a relative mouse: 3 buttons, X, Y and wheel in -127..127.
var DescMouse = []byte{
	0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, // Generic Desktop / Mouse
	0x09, 0x01, 0xA1, 0x00, // Pointer / Collection (Physical)
	0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02, // 3 buttons
	0x95, 0x01, 0x75, 0x05, 0x81, 0x03, // padding
	0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x09, 0x38, // X, Y, wheel
	0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x03, 0x81, 0x06, // relative, -127..127
	0xC0, 0xC0,
}

// DescAbsolute is the absolute pointer: 3 buttons, X and Y in 0..32767 over the whole screen, and
// a relative wheel.
var DescAbsolute = []byte{
	0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, // Generic Desktop / Mouse
	0x09, 0x01, 0xA1, 0x00, // Pointer / Collection (Physical)
	0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01, 0x95, 0x03, 0x75, 0x01, 0x81, 0x02, // 3 buttons
	0x95, 0x01, 0x75, 0x05, 0x81, 0x03, // padding
	0x05, 0x01, 0x09, 0x30, 0x09, 0x31, // X, Y
	0x16, 0x00, 0x00, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x02, // absolute 0..32767
	0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06, // relative wheel
	0xC0, 0xC0,
}

// AbsMax is the largest absolute coordinate: the right or bottom edge of the screen.
const AbsMax = 32767

// Function is one HID gadget function (one USB interface, one /dev/hidgN node).
type Function struct {
	Name         string
	Protocol     int // interface protocol: 1 keyboard, 2 mouse, 0 none
	Subclass     int // 1 = boot interface
	ReportLength int
	Descriptor   []byte
	OutReports   bool // the host sends reports back (keyboard LEDs)
}

// Functions by name.
var Functions = map[string]Function{
	"keyboard": {"keyboard", 1, 1, 8, DescKeyboard, true},
	"consumer": {"consumer", 0, 0, 3, DescConsumer, false},
	"mouse":    {"mouse", 2, 1, 4, DescMouse, false},
	"absolute": {"absolute", 0, 0, 6, DescAbsolute, false},
}

// Profiles list the functions of each gadget layout, in interface order. iOS may keep the
// descriptor it saw for a known device, so each profile has its own serial number.
var Profiles = map[string][]string{
	"RA": {"keyboard", "consumer", "mouse", "absolute"}, // the layout confirmed on an iPhone 15
	"A":  {"keyboard", "consumer", "absolute"},          // absolute pointer only
	"AR": {"keyboard", "consumer", "absolute", "mouse"}, // absolute pointer before the relative mouse
}

// DefaultProfile is the layout the box sets up unless told otherwise.
const DefaultProfile = "RA"

// MaxFunctions is how many HID gadget functions the kernel allows in all (HIDG_MINORS in f_hid).
const MaxFunctions = 4
