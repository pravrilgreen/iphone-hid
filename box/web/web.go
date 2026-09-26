// Package web holds the console's static files, built into the binary.
package web

import "embed"

// Files are the console: index.html and what it loads.
//
//go:embed index.html app.js style.css fonts/*.woff2 fonts/OFL.txt
var Files embed.FS
