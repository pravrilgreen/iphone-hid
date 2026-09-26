package video

import (
	"testing"
	"unsafe"
)

func TestIoctlNumbersMatchTheKernel(t *testing.T) {
	want := map[string][2]uintptr{
		"QUERYCAP":         {vidiocQuerycap, 0x80685600},
		"G_FMT":            {vidiocGFmt, 0xC0D05604},
		"S_FMT":            {vidiocSFmt, 0xC0D05605},
		"REQBUFS":          {vidiocReqbufs, 0xC0145608},
		"QUERYBUF":         {vidiocQuerybuf, 0xC0585609},
		"QBUF":             {vidiocQbuf, 0xC058560F},
		"DQBUF":            {vidiocDqbuf, 0xC0585611},
		"STREAMON":         {vidiocStreamon, 0x40045612},
		"STREAMOFF":        {vidiocStreamoff, 0x40045613},
		"S_EDID":           {vidiocSEDID, 0xC0285629},
		"S_DV_TIMINGS":     {vidiocSDVTimings, 0xC0845657},
		"QUERY_DV_TIMINGS": {vidiocQueryDVTimings, 0x80845663},
	}
	for name, v := range want {
		if v[0] != v[1] {
			t.Errorf("VIDIOC_%s = %#x, the kernel's is %#x", name, v[0], v[1])
		}
	}
	if unsafe.Sizeof(v4l2Plane{}) != 64 {
		t.Errorf("v4l2_plane is %d bytes, not 64", unsafe.Sizeof(v4l2Plane{}))
	}
}
