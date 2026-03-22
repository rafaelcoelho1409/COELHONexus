package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"
)

// FastAPI client
var fastAPIURL = getEnv("FASTAPI_URL", "http://coelhonexus-fastapi:8000")
var httpClient = &http.Client{Timeout: 10 * time.Second}

func getEnv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func main() {
	mux := http.NewServeMux()

	// Static files
	mux.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.Dir("static"))))

	// Health check
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	// Home page - calls FastAPI /health
	mux.HandleFunc("/", homeHandler)

	// Test FastAPI connection
	mux.HandleFunc("/api/test", testFastAPIHandler)

	port := getEnv("PORT", "3000")
	log.Printf("Web server starting on :%s", port)
	log.Printf("FastAPI URL: %s", fastAPIURL)

	if err := http.ListenAndServe(":"+port, mux); err != nil {
		log.Fatal(err)
	}
}

func homeHandler(w http.ResponseWriter, r *http.Request) {
	html := `<!DOCTYPE html>
<html>
<head>
    <title>COELHO Nexus</title>
    <link rel="stylesheet" href="/static/css/main.css">
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
<body>
    <div class="app-container">
        <aside class="sidebar">
            <div class="sidebar-header">
                <div class="logo">
                    <span class="logo-text">COELHO Nexus</span>
                </div>
                <p class="subtitle">YouTube Content Search</p>
            </div>
        </aside>
        <main class="main-content">
            <div class="page">
                <header class="page-header">
                    <h1>Welcome to COELHO Nexus</h1>
                    <p class="page-subtitle">Building from scratch - Go + HTMX + FastAPI</p>
                </header>
                <div class="card">
                    <h2 class="card-title">FastAPI Connection Test</h2>
                    <button
                        class="btn btn-primary"
                        hx-get="/api/test"
                        hx-target="#result"
                        hx-swap="innerHTML"
                    >
                        Test FastAPI /health
                    </button>
                    <div id="result" style="margin-top: 16px;"></div>
                </div>
            </div>
        </main>
    </div>
</body>
</html>`
	w.Header().Set("Content-Type", "text/html")
	w.Write([]byte(html))
}

func testFastAPIHandler(w http.ResponseWriter, r *http.Request) {
	// Call FastAPI /health endpoint
	resp, err := httpClient.Get(fastAPIURL + "/health")
	if err != nil {
		w.WriteHeader(http.StatusServiceUnavailable)
		fmt.Fprintf(w, `<div class="alert" style="background:#fed7d7;color:#c53030;padding:12px;border-radius:4px;">
			Error connecting to FastAPI: %s
		</div>`, err.Error())
		return
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)

	var result map[string]interface{}
	json.Unmarshal(body, &result)

	w.WriteHeader(http.StatusOK)
	fmt.Fprintf(w, `<div class="alert" style="background:#c6f6d5;color:#276749;padding:12px;border-radius:4px;">
		<strong>FastAPI is healthy!</strong><br>
		Response: %s
	</div>`, string(body))
}
