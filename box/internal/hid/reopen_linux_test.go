package hid

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// A gadget set up again makes new nodes: the sink must leave the old ones and open the new.
func TestReopeningFollowsAGadgetSetUpAgain(t *testing.T) {
	root := t.TempDir()
	p := Paths{ConfigFS: filepath.Join(root, "cfg"), SysFS: filepath.Join(root, "sys"), Dev: "/dev"}
	g := filepath.Join(p.ConfigFS, GadgetName)
	for dir, files := range map[string]map[string]string{
		filepath.Join(g, "configs", "c.1", "hid.absolute"): nil,
		filepath.Join(g, "strings", "0x409"):               {"serialnumber": "ihc-RA"},
		filepath.Join(g, "functions", "hid.absolute"):      {"dev": "9:9\n"},
		filepath.Join(p.SysFS, "dev", "char", "9:9"):       {"uevent": "DEVNAME=full\n"}, // every write fails
	} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		for name, content := range files {
			if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	r := NewReopening(p, GadgetName, 50*time.Millisecond)
	if err := r.Pointer(Pointer{}); err == nil {
		t.Fatal("a write to /dev/full succeeded")
	}
	// set up again: the function's node is now /dev/null, which takes every write
	if err := os.WriteFile(filepath.Join(p.SysFS, "dev", "char", "9:9", "uevent"), []byte("DEVNAME=null\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	var err error
	for i := 0; i < 3; i++ {
		if err = r.Pointer(Pointer{}); err == nil {
			return
		}
		time.Sleep(600 * time.Millisecond)
	}
	t.Fatalf("still writing to the old node: %v", err)
}
