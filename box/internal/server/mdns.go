package server

import (
	"fmt"
	"net"
	"os"

	"github.com/hashicorp/mdns"
)

// Advertise announces the box on the local network as _ihc._tcp, so test runners find it without
// configuration. Stop the returned server to withdraw it.
func Advertise(id string, port int, version string, auth, tls bool) (*mdns.Server, error) {
	host, _ := os.Hostname()
	authTxt := "none"
	if auth {
		authTxt = "token"
	}
	var ips []net.IP
	if addrs, err := net.InterfaceAddrs(); err == nil {
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok && !n.IP.IsLoopback() && n.IP.To4() != nil {
				ips = append(ips, n.IP)
			}
		}
	}
	scheme := "http"
	if tls {
		scheme = "https"
	}
	txt := []string{"api=/api", "devices=1", "version=" + version, "auth=" + authTxt, "id=" + id, "scheme=" + scheme}
	svc, err := mdns.NewMDNSService(id, "_ihc._tcp", "", host+".", port, ips, txt)
	if err != nil {
		return nil, fmt.Errorf("mDNS: %w", err)
	}
	return mdns.NewServer(&mdns.Config{Zone: svc})
}
