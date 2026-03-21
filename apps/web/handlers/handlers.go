package handlers

import (
	"net/http"

	"coelhonexus-web/templates"
	"coelhonexus-web/templates/components"
	"coelhonexus-web/templates/pages"
)

// Page Handlers

func HomePage(w http.ResponseWriter, r *http.Request) {
	templates.Layout(pages.Home()).Render(r.Context(), w)
}

func SearchPage(w http.ResponseWriter, r *http.Request) {
	templates.Layout(pages.Search()).Render(r.Context(), w)
}

func SettingsPage(w http.ResponseWriter, r *http.Request) {
	templates.Layout(pages.Settings()).Render(r.Context(), w)
}

func GraphsPage(w http.ResponseWriter, r *http.Request) {
	templates.Layout(pages.Graphs()).Render(r.Context(), w)
}

// HTMX Partial Handlers

func SearchHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	searchType := r.FormValue("search_type")
	query := r.FormValue("query")

	// TODO: Connect to FastAPI backend
	// For now, return mock results
	results := []components.SearchResult{
		{
			Title:     "Sample Video Result",
			Channel:   "Sample Channel",
			Views:     "1.2M views",
			Duration:  "10:30",
			Thumbnail: "https://via.placeholder.com/320x180",
			VideoID:   "abc123",
		},
	}

	components.SearchResults(results, searchType, query).Render(r.Context(), w)
}

func SearchFormPartial(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	searchType := r.FormValue("search_type")
	components.SearchForm(searchType).Render(r.Context(), w)
}

func ChatMessagesPartial(w http.ResponseWriter, r *http.Request) {
	// TODO: Fetch from session/backend
	messages := []components.ChatMessage{
		{Role: "assistant", Content: "Welcome to YouTube Content Search. How can I help you today?"},
	}
	components.ChatMessages(messages).Render(r.Context(), w)
}

func ChatSendHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	message := r.FormValue("message")

	// TODO: Send to AI agent via FastAPI
	response := components.ChatMessage{
		Role:    "assistant",
		Content: "Processing your request: " + message,
	}

	components.SingleMessage(response).Render(r.Context(), w)
}

func SettingsSaveHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	// TODO: Save settings to session/backend

	w.Header().Set("HX-Trigger", "settings-saved")
	components.SettingsSuccess().Render(r.Context(), w)
}

func ClearMemoryHandler(w http.ResponseWriter, r *http.Request) {
	// TODO: Clear session memory

	w.Header().Set("HX-Trigger", "memory-cleared")
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("Memory cleared"))
}
