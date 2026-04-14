package main

import (
	"bufio"
	"fmt"
	"html"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

type Config struct {
	ServeRoot            string
	Listen               string
	TLSCert              string
	TLSKey               string
	SanitizePaths        bool
	ShowHidden           bool
	AllowAnonymousUpload bool
}

func parseConfig(path string) (*Config, error) {
	cfg := &Config{
		ServeRoot:            "/opt/fileserver/files",
		Listen:               "0.0.0.0:8000",
		TLSCert:              "/opt/fileserver/server.crt",
		TLSKey:               "/opt/fileserver/server.key",
		SanitizePaths:        true,
		ShowHidden:           false,
		AllowAnonymousUpload: false,
	}

	f, err := os.Open(path)
	if err != nil {
		return cfg, nil // return defaults if config missing
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(strings.Trim(strings.TrimSpace(parts[1]), "\""))

		switch key {
		case "serve_root":
			cfg.ServeRoot = val
		case "listen":
			cfg.Listen = val
		case "tls_cert":
			cfg.TLSCert = val
		case "tls_key":
			cfg.TLSKey = val
		case "sanitize_paths":
			cfg.SanitizePaths = val == "true"
		case "show_hidden":
			cfg.ShowHidden = val == "true"
		case "allow_anonymous_upload":
			cfg.AllowAnonymousUpload = val == "true"
		}
	}
	return cfg, scanner.Err()
}

func main() {
	configPath := "/opt/fileserver/config.toml"
	if len(os.Args) > 1 {
		configPath = os.Args[1]
	}

	cfg, err := parseConfig(configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "config error: %v\n", err)
		os.Exit(1)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/files/", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet, http.MethodHead:
			serveFile(w, r, cfg)
		case http.MethodPut:
			if cfg.AllowAnonymousUpload {
				uploadFile(w, r, cfg)
			} else {
				http.Error(w, "Forbidden", http.StatusForbidden)
			}
		default:
			http.Error(w, "Method Not Allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/files/", http.StatusFound)
	})

	fmt.Printf("Serving %s on https://%s\n", cfg.ServeRoot, cfg.Listen)
	if err := http.ListenAndServeTLS(cfg.Listen, cfg.TLSCert, cfg.TLSKey, mux); err != nil {
		fmt.Fprintf(os.Stderr, "server error: %v\n", err)
		os.Exit(1)
	}
}

func serveFile(w http.ResponseWriter, r *http.Request, cfg *Config) {
	urlPath := strings.TrimPrefix(r.URL.Path, "/files")
	if urlPath == "" {
		urlPath = "/"
	}

	var target string
	if cfg.SanitizePaths {
		clean := filepath.Clean(urlPath)
		target = filepath.Join(cfg.ServeRoot, clean)
		// ensure we stay within serve root
		if !strings.HasPrefix(target, filepath.Clean(cfg.ServeRoot)+string(os.PathSeparator)) &&
			target != filepath.Clean(cfg.ServeRoot) {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
	} else {
		// vulnerable: no path sanitisation
		target = cfg.ServeRoot + urlPath
	}

	info, err := os.Stat(target)
	if err != nil {
		http.NotFound(w, r)
		return
	}

	if info.IsDir() {
		listDir(w, r, target, urlPath, cfg)
		return
	}

	// hidden file check for direct access
	base := filepath.Base(target)
	if !cfg.ShowHidden && strings.HasPrefix(base, ".") {
		http.NotFound(w, r)
		return
	}

	http.ServeFile(w, r, target)
}

func listDir(w http.ResponseWriter, r *http.Request, dir, urlPath string, cfg *Config) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	if !strings.HasSuffix(urlPath, "/") {
		urlPath += "/"
	}

	var sb strings.Builder
	sb.WriteString("<!DOCTYPE html><html><head><title>Index of " + html.EscapeString(urlPath) + "</title>")
	sb.WriteString("<style>body{font-family:monospace;padding:20px}a{display:block;padding:2px 0}</style></head><body>")
	sb.WriteString("<h2>Index of " + html.EscapeString(urlPath) + "</h2><hr>")

	if urlPath != "/" {
		parent := filepath.Dir(strings.TrimSuffix(urlPath, "/"))
		sb.WriteString(`<a href="/files` + parent + `">../</a>`)
	}

	for _, e := range entries {
		name := e.Name()
		if !cfg.ShowHidden && strings.HasPrefix(name, ".") {
			continue
		}
		display := name
		href := "/files" + urlPath + name
		if e.IsDir() {
			display += "/"
			href += "/"
		}
		sb.WriteString(`<a href="` + html.EscapeString(href) + `">` + html.EscapeString(display) + `</a>`)
	}

	sb.WriteString("<hr></body></html>")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	fmt.Fprint(w, sb.String())
}

func uploadFile(w http.ResponseWriter, r *http.Request, cfg *Config) {
	urlPath := strings.TrimPrefix(r.URL.Path, "/files")
	target := filepath.Join(cfg.ServeRoot, filepath.Clean(urlPath))

	// ensure within serve root
	if !strings.HasPrefix(target, filepath.Clean(cfg.ServeRoot)+string(os.PathSeparator)) {
		http.Error(w, "Forbidden", http.StatusForbidden)
		return
	}

	if err := os.MkdirAll(filepath.Dir(target), 0755); err != nil {
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	f, err := os.Create(target)
	if err != nil {
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}
	defer f.Close()

	if _, err := io.Copy(f, r.Body); err != nil {
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusCreated)
	fmt.Fprintf(w, "Uploaded: %s\n", urlPath)
}
