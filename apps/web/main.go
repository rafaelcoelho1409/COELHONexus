package main

import (
	"log"
	"net/http"
	"os"

	"coelhonexus-web/handlers"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func main() {
	r := chi.NewRouter()

	// Middleware
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)
	r.Use(middleware.Compress(5))

	// Static files
	fileServer := http.FileServer(http.Dir("static"))
	r.Handle("/static/*", http.StripPrefix("/static/", fileServer))

	// PWA files (must be at root)
	r.Get("/manifest.json", serveFile("static/manifest.json"))
	r.Get("/sw.js", serveFile("static/sw.js"))

	// Pages
	r.Get("/", handlers.HomePage)
	r.Get("/search", handlers.SearchPage)
	r.Get("/settings", handlers.SettingsPage)
	r.Get("/graphs", handlers.GraphsPage)

	// HTMX partials
	r.Post("/api/search", handlers.SearchHandler)
	r.Post("/api/search/form", handlers.SearchFormPartial)
	r.Get("/api/chat/messages", handlers.ChatMessagesPartial)
	r.Post("/api/chat/send", handlers.ChatSendHandler)
	r.Post("/api/settings/save", handlers.SettingsSaveHandler)
	r.Delete("/api/memory/clear", handlers.ClearMemoryHandler)

	// Health check
	r.Get("/health", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "3000"
	}

	log.Printf("Starting server on :%s", port)
	if err := http.ListenAndServe(":"+port, r); err != nil {
		log.Fatal(err)
	}
}

func serveFile(filepath string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, filepath)
	}
}
