// ihcd is the box software: it makes the board the iPhone's USB touch pointer and keyboard, reads
// the iPhone's screen from the HDMI input, and serves the API and the web console.
//
//	ihcd serve                 run the box (the systemd service)
//	ihcd serve --sim           run with a simulated iPhone, no hardware needed
//	ihcd gadget up|down|status|wake
//	ihcd doctor [--fix]        examine the board and the box, fix what it can, say how to fix the rest
//	                           (--fix-safe: only the fixes that cut nothing else off)
//	ihcd hid corners|tap|key|type|button   drive the phone directly, without the service
//	ihcd version
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/pravrilgreen/iphone-hid/box/internal/board"
	"github.com/pravrilgreen/iphone-hid/box/internal/doctor"
	"github.com/pravrilgreen/iphone-hid/box/internal/hid"
	"github.com/pravrilgreen/iphone-hid/box/internal/input"
	"github.com/pravrilgreen/iphone-hid/box/internal/server"
	"github.com/pravrilgreen/iphone-hid/box/internal/sim"
	"github.com/pravrilgreen/iphone-hid/box/internal/video"
	"github.com/pravrilgreen/iphone-hid/box/web"
)

// version is set at build time (-ldflags "-X main.version=...").
var version = "dev"

func main() {
	log.SetFlags(0)
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "serve":
		err = serve(args)
	case "gadget":
		err = gadget(args)
	case "doctor", "check":
		err = doctorCmd(args)
	case "hid":
		err = hidCmd(args)
	case "version", "--version", "-v":
		if len(args) > 0 && isHelp(args[0]) {
			fmt.Fprintln(os.Stderr, "usage: ihcd version\n\nPrints the version of ihcd and its JPEG encoder.")
			return
		}
		fmt.Println("ihcd", version, "("+video.EncoderName+")")
	case "help", "--help", "-h":
		usage()
	default:
		usage()
		os.Exit(2)
	}
	if errors.Is(err, errUsage) {
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "ihcd:", err)
		os.Exit(1)
	}
}

// errUsage: the command's usage was printed for a wrong call.
var errUsage = errors.New("usage")

func isHelp(a string) bool { return a == "-h" || a == "-help" || a == "--help" || a == "help" }

// needsRoot turns a permission error into advice.
func needsRoot(err error) error {
	if errors.Is(err, fs.ErrPermission) {
		return fmt.Errorf("%w: run with sudo", err)
	}
	return err
}

func usage() {
	fmt.Fprint(os.Stderr, `usage: ihcd <command> [flags]

  serve     run the box: API and web console (--sim: a simulated iPhone)
  gadget    up | down | status | wake: the USB touch pointer and keyboard gadget
  doctor    examine the board and the box: USB device port, gadget, iPhone, HDMI input, service;
            --fix fixes what it can, --fix-safe only what cuts nothing else off, --fix-boot also turns
            the HDMI input on in the boot configuration
  hid       drive the phone directly: corners | tap X Y | key COMBO | type TEXT | button NAME |
            click secondary|middle
  version   print the version

Run "ihcd <command> -h" for the flags of a command.
`)
}

// -- serve -------------------------------------------------------------------------------------------------

func serve(args []string) error {
	fl := flag.NewFlagSet("serve", flag.ExitOnError)
	addr := fl.String("addr", ":8000", "address to listen on")
	simulate := fl.Bool("sim", false, "a simulated iPhone instead of the hardware")
	videoDev := fl.String("video", "auto", "HDMI input capture node (auto: the board's HDMI receiver)")
	noEDID := fl.Bool("no-edid", false, "leave the HDMI input's EDID as it is (default: 1080p60)")
	landscape := fl.Bool("landscape", false, "the phone mirrors in landscape")
	tokenFile := fl.String("token-file", "", "file holding the API token (created with a random token if missing)")
	token := fl.String("token", os.Getenv("IHC_TOKEN"), "API token (default $IHC_TOKEN)")
	noToken := fl.Bool("no-token", false, "no API token: anyone on the network can drive the phone")
	id := fl.String("id", "", "the phone's name on the network (default iphone-<board serial>)")
	settle := fl.Duration("settle", input.DefaultConfig.Settle, "wait after a pointer jump before pressing")
	home := fl.String("home", "keys", "how Home is pressed: keys (Cmd+H) or button (secondary pointer button mapped to Home)")
	noMDNS := fl.Bool("no-mdns", false, "do not announce the box on the network")
	webDir := fl.String("web", "", "serve the console from this directory (development)")
	tlsCert := fl.String("tls-cert", "", "serve HTTPS with this certificate (PEM; with --tls-key)")
	tlsKey := fl.String("tls-key", "", "the certificate's private key (PEM)")
	maxViewers := fl.Int("max-viewers", 4, "screen streams served at once")
	var allow multiFlag
	fl.Var(&allow, "allow-host", "another host name the box answers to (repeatable)")
	fl.Usage = func() {
		fmt.Fprint(fl.Output(), "usage: ihcd serve [flags]\n\nRuns the box: the API, the web console, the phone's touch and keyboard.\n"+
			"The service's flags go in IHCD_ARGS of /etc/default/ihc.\n\n")
		fl.PrintDefaults()
	}
	_ = fl.Parse(args)

	logger := log.New(os.Stderr, "", log.LstdFlags)
	if (*tlsCert == "") != (*tlsKey == "") {
		return errors.New("--tls-cert and --tls-key go together")
	}
	if *maxViewers < 1 {
		return errors.New("--max-viewers is at least 1")
	}
	useTLS := *tlsCert != ""
	if err := input.SetHomeMethod(*home); err != nil {
		return err
	}
	tok, err := loadToken(*token, *tokenFile, *noToken || (*simulate && *token == "" && *tokenFile == ""))
	if err != nil {
		return err
	}
	if tok == "" {
		logger.Print("warning: no API token, anyone who can reach this box can drive the phone")
	}
	deviceID := *id
	if deviceID == "" {
		deviceID = board.DeviceID("/")
		if *simulate {
			deviceID = "iphone-sim"
		}
	}

	var sink hid.Sink
	var src video.Source
	var simState func() any
	var simSet func(usb, video string) error
	if *simulate {
		phone := sim.New()
		sink, src = phone, phone
		simState = func() any { return phone.State() }
		simSet = phone.Set
	} else {
		if sink, src, err = hardware(*videoDev, !*noEDID, logger); err != nil {
			return err
		}
	}
	cfg := input.DefaultConfig
	cfg.Settle = *settle
	engine := input.New(sink, cfg)
	defer engine.Close()
	hub := video.NewHub(src, video.Layout{Landscape: *landscape})

	var webFS fs.FS = web.Files
	if *webDir != "" {
		webFS = os.DirFS(*webDir)
	}
	srv := server.New(server.Config{DeviceID: deviceID, Version: version, Token: tok, AllowHosts: allow,
		Web: webFS, Log: logger, SimState: simState, SimSet: simSet, MaxViewers: *maxViewers}, engine, hub)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go hub.Run(ctx)

	ln, err := net.Listen("tcp", *addr)
	if err != nil {
		return err
	}
	port := ln.Addr().(*net.TCPAddr).Port
	if !*noMDNS {
		if m, err := server.Advertise(deviceID, port, version, tok != "", useTLS); err != nil {
			logger.Printf("mDNS announcement failed: %v", err)
		} else {
			defer m.Shutdown()
		}
	}
	httpSrv := &http.Server{Handler: srv.Handler(), ReadHeaderTimeout: 10 * time.Second}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = httpSrv.Shutdown(sctx)
	}()
	what := "HDMI " + src.Describe()
	if *simulate {
		what = "a simulated iPhone"
	}
	scheme := "http"
	if useTLS {
		scheme = "https"
	}
	logger.Printf("ihcd %s: %s on %s://%s/ (%s, JPEG by %s)", version, deviceID, scheme, displayAddr(ln.Addr()), what, video.EncoderName)
	if useTLS {
		err = httpSrv.ServeTLS(ln, *tlsCert, *tlsKey)
	} else {
		err = httpSrv.Serve(ln)
	}
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func displayAddr(a net.Addr) string {
	t := a.(*net.TCPAddr)
	if t.IP.IsUnspecified() {
		return "localhost:" + strconv.Itoa(t.Port)
	}
	return t.String()
}

// loadToken returns the API token: the flag, else the file's content, else (the file missing) a new
// random token written to the file.
func loadToken(flagTok, file string, none bool) (string, error) {
	if flagTok != "" {
		return flagTok, nil
	}
	if file == "" {
		if none {
			return "", nil
		}
		return "", errors.New("no API token: give --token-file (the service uses /var/lib/ihc/token), --token, or --no-token")
	}
	b, err := os.ReadFile(file)
	if err == nil {
		if t := strings.TrimSpace(string(b)); t != "" {
			return t, nil
		}
	} else if !errors.Is(err, fs.ErrNotExist) {
		return "", err
	}
	raw := make([]byte, 18)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	t := hex.EncodeToString(raw)
	if err := os.MkdirAll(filepath.Dir(file), 0o755); err != nil {
		return "", err
	}
	if err := os.WriteFile(file, []byte(t+"\n"), 0o600); err != nil {
		return "", err
	}
	return t, nil
}

type multiFlag []string

func (m *multiFlag) String() string     { return strings.Join(*m, ",") }
func (m *multiFlag) Set(v string) error { *m = append(*m, v); return nil }

// -- gadget ------------------------------------------------------------------------------------------------

const gadgetUsage = `usage: ihcd gadget <command> [flags]

  up       set the gadget up and bind it to the USB device controller (root)
  down     unbind the gadget and remove it (root)
  status   what configfs and sysfs say about it, as JSON
  wake     wake an iPhone that suspended the USB bus (root, or the group ihc)

The service ihcd-gadget runs "ihcd gadget up --if-missing --owner ihc" with IHCD_GADGET_ARGS of
/etc/default/ihc. Flags of up:
`

func gadget(args []string) error {
	name := "up"
	if len(args) > 0 {
		name = args[0]
	}
	fl := flag.NewFlagSet("gadget "+name, flag.ExitOnError)
	profile := fl.String("profile", hid.DefaultProfile, "HID layout: RA (confirmed on iPhone 15), A (absolute pointer only), AR")
	udc := fl.String("udc", "", "USB device controller (default: the board's only one)")
	ifMissing := fl.Bool("if-missing", false, "do nothing if the gadget is up with this profile already (else set it up again)")
	replace := fl.Bool("replace", false, "remove an existing gadget first")
	noWakeup := fl.Bool("no-remote-wakeup", false, "do not declare USB remote wakeup")
	owner := fl.String("owner", "", "give the /dev/hidg nodes to this user (the service's user)")
	fl.Usage = func() {
		fmt.Fprint(fl.Output(), gadgetUsage)
		fl.PrintDefaults()
	}
	if len(args) == 0 {
		fl.Usage()
		return errUsage
	}
	if isHelp(args[0]) {
		fl.Usage()
		return nil
	}
	_ = fl.Parse(args[1:])
	p := hid.SystemPaths
	switch args[0] {
	case "up":
		st := p.Status(hid.GadgetName)
		opts := hid.GadgetOptions{Profile: *profile, UDC: *udc, RemoteWakeup: !*noWakeup}
		if *ifMissing && p.Matches(opts) {
			fmt.Printf("gadget %s is up on %s, profile %s (%s)\n", hid.GadgetName, st.UDC, st.Profile, strings.Join(st.Functions, ", "))
			return needsRoot(chownNodes(p, *owner))
		}
		if st.Exists && (*replace || *ifMissing) {
			if *ifMissing {
				fmt.Printf("gadget %s is not up as asked (profile %s on %s): setting it up again\n", hid.GadgetName,
					orNone(st.Profile), orNone(st.UDC))
			}
			if _, err := p.GadgetDown(hid.GadgetName); err != nil {
				return needsRoot(err)
			}
		}
		got, err := p.GadgetUp(opts)
		if err != nil {
			return needsRoot(err)
		}
		fmt.Printf("gadget %s is up on %s, profile %s: plug the iPhone in (USB state: %s)\n", hid.GadgetName, got,
			*profile, p.UDCState(got))
		time.Sleep(200 * time.Millisecond) // udev creates the nodes
		return needsRoot(chownNodes(p, *owner))
	case "down":
		ok, err := p.GadgetDown(hid.GadgetName)
		if err != nil {
			return needsRoot(err)
		}
		if ok {
			fmt.Println("gadget removed")
		} else {
			fmt.Println("no gadget")
		}
		return nil
	case "status":
		b, _ := json.MarshalIndent(p.Status(hid.GadgetName), "", "  ")
		fmt.Println(string(b))
		return nil
	case "wake":
		before, after, err := p.Wake(p.BoundUDC(hid.GadgetName), 3*time.Second)
		if err != nil {
			return needsRoot(err)
		}
		fmt.Printf("USB state %s -> %s\n", before, after)
		return nil
	}
	return fmt.Errorf("unknown gadget command %q (ihcd gadget -h lists them)", args[0])
}

func orNone(s string) string {
	if s == "" {
		return "none"
	}
	return s
}

func chownNodes(p hid.Paths, owner string) error {
	if owner == "" {
		return nil
	}
	uid, gid, err := lookupUser(owner)
	if err != nil {
		return err
	}
	for _, n := range p.Nodes(hid.GadgetName) {
		if err := os.Chown(n, uid, gid); err != nil {
			return err
		}
	}
	return nil
}

// -- doctor -------------------------------------------------------------------------------------------------

func doctorCmd(args []string) error {
	fl := flag.NewFlagSet("doctor", flag.ExitOnError)
	fix := fl.Bool("fix", false, "fix what can be fixed at run time (modules, gadget, permissions, services), "+
		"including the disruptive fixes: unbind another gadget, switch the port's USB role")
	safe := fl.Bool("fix-safe", false, "fix only what cuts nothing else off: --fix without the disruptive fixes")
	boot := fl.Bool("fix-boot", false, "also change the boot configuration to turn the HDMI input on (keeps a backup; reboot after)")
	asJSON := fl.Bool("json", false, "print the results as JSON")
	fl.Usage = func() {
		fmt.Fprint(fl.Output(), "usage: sudo ihcd doctor [flags]\n\nExamines the board and the box, fixes what it is asked to, "+
			"says how to fix the rest.\nWithout root it cannot read everything: run it with sudo.\n\n")
		fl.PrintDefaults()
	}
	_ = fl.Parse(args)
	opts := doctor.Options{Fix: *fix || *safe || *boot, SafeOnly: *safe && !*fix, Boot: *boot}
	if opts.Fix && os.Geteuid() != 0 {
		return doctor.ErrNotRoot
	}
	sys := doctor.Local()
	if !*asJSON {
		fmt.Printf("ihcd doctor %s\n\n", version)
	}
	rs := doctor.Run(sys, opts)
	if *asJSON {
		b, _ := json.MarshalIndent(rs, "", "  ")
		fmt.Println(string(b))
	} else {
		doctor.Report(os.Stdout, rs, opts)
	}
	if doctor.Worst(rs) == doctor.Fail {
		os.Exit(1)
	}
	return nil
}

// -- hid ------------------------------------------------------------------------------------------------------

func hidUsage() {
	var names []string
	for _, b := range input.Buttons {
		names = append(names, b.Name)
	}
	fmt.Fprintf(os.Stderr, `usage: sudo ihcd hid <command>

Drives the phone directly, without the service: stop it first (sudo systemctl stop ihcd) and
start it after (sudo systemctl start ihcd).

  corners                  the pointer to each corner and the centre
  tap X Y                  tap at X, Y: fractions of the screen, 0 to 1
  key COMBO                press a key combination, e.g. cmd+space
  type TEXT                type the text
  button NAME              press a button: %s
  click secondary|middle   the AssistiveTouch button actions
`, strings.Join(names, ", "))
}

func hidCmd(args []string) error {
	if len(args) == 0 {
		hidUsage()
		return errUsage
	}
	if isHelp(args[0]) || (len(args) > 1 && isHelp(args[1])) {
		hidUsage()
		return nil
	}
	name, act, err := hidAction(args)
	if err != nil {
		return err
	}
	sink, err := openGadget()
	if errors.Is(err, fs.ErrPermission) {
		return errors.New("permission denied: run with sudo (the nodes belong to the ihc group)")
	}
	if err != nil {
		return err
	}
	defer sink.Close()
	e := input.New(sink, input.DefaultConfig)
	defer e.Close()
	return e.Do(context.Background(), name, act)
}

// hidAction checks the arguments of ihcd hid and returns what to do.
func hidAction(args []string) (string, func(a *input.Actor) error, error) {
	num := func(s string) (float64, error) { return strconv.ParseFloat(s, 64) }
	switch args[0] {
	case "corners":
		// the pointer to each corner and the centre: if it reaches them, the phone follows the absolute pointer
		return "corners", func(a *input.Actor) error {
			for _, c := range [][2]float64{{0, 0}, {1, 0}, {1, 1}, {0, 1}, {0.5, 0.5}} {
				fmt.Printf("pointer to %.1f, %.1f\n", c[0], c[1])
				a.MoveSettled(c[0], c[1])
				a.Sleep(time.Second)
			}
			return a.Err()
		}, nil
	case "tap":
		if len(args) != 3 {
			return "", nil, errors.New("usage: ihcd hid tap X Y (fractions of the screen, 0 to 1)")
		}
		x, err1 := num(args[1])
		y, err2 := num(args[2])
		if err1 != nil || err2 != nil {
			return "", nil, errors.New("X and Y are numbers from 0 to 1")
		}
		return "tap", func(a *input.Actor) error { a.Tap(x, y, 0); return a.Err() }, nil
	case "key":
		if len(args) != 2 {
			return "", nil, errors.New("usage: ihcd hid key COMBO (e.g. cmd+space)")
		}
		return "key", func(a *input.Actor) error { a.Combo(args[1]); return a.Err() }, nil
	case "type":
		if len(args) != 2 {
			return "", nil, errors.New("usage: ihcd hid type TEXT")
		}
		return "type", func(a *input.Actor) error { a.Type(args[1]); return a.Err() }, nil
	case "click":
		b := map[string]uint8{"secondary": hid.ButtonSecondary, "middle": hid.ButtonMiddle}
		if len(args) != 2 || b[args[1]] == 0 {
			return "", nil, errors.New("usage: ihcd hid click secondary|middle (the AssistiveTouch button actions)")
		}
		return "click", func(a *input.Actor) error { a.PointerButton(b[args[1]]); return a.Err() }, nil
	case "button":
		if len(args) != 2 {
			var names []string
			for _, b := range input.Buttons {
				names = append(names, b.Name)
			}
			return "", nil, errors.New("usage: ihcd hid button NAME (" + strings.Join(names, ", ") + ")")
		}
		return "button", func(a *input.Actor) error { a.PressButton(args[1]); return a.Err() }, nil
	}
	return "", nil, fmt.Errorf("unknown hid command %q (ihcd hid -h lists them)", args[0])
}

func lookupUser(name string) (int, int, error) {
	u, err := user.Lookup(name)
	if err != nil {
		return 0, 0, err
	}
	uid, err1 := strconv.Atoi(u.Uid)
	gid, err2 := strconv.Atoi(u.Gid)
	if err1 != nil || err2 != nil {
		return 0, 0, fmt.Errorf("user %s: unexpected ids %s:%s", name, u.Uid, u.Gid)
	}
	return uid, gid, nil
}
