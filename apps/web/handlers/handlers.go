package handlers

import (
	"fmt"
	"net/http"

	"coelhonexus-web/config"
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

// Settings Handlers

func RefreshModelsHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	apiKey := r.FormValue("api_key")
	provider := r.FormValue("provider")

	if provider == "" {
		provider = config.DefaultProvider
	}

	if apiKey == "" {
		pages.ModelsFetchError("Enter API key first").Render(r.Context(), w)
		return
	}

	// Save API key and provider immediately
	config.UpdateLLMField("api_key", apiKey)
	config.UpdateLLMField("provider", provider)

	// Currently only NVIDIA NIM is supported
	if provider != "nvidia_nim" {
		pages.ModelsFetchError("Provider not yet supported").Render(r.Context(), w)
		return
	}

	models, err := config.FetchModelsWithKey(apiKey)
	if err != nil || len(models) == 0 {
		pages.ModelsFetchError("Failed to fetch - check API key").Render(r.Context(), w)
		return
	}

	// Cache the fetched models
	config.SetCachedModels(models)

	settings := config.GetLLMSettings()
	pages.ModelOptions(models, settings.Model).Render(r.Context(), w)
}

func SaveLLMSettingsHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()

	provider := r.FormValue("provider")
	apiKey := r.FormValue("api_key")
	model := r.FormValue("model")
	tempStr := r.FormValue("temperature")

	var temperature float64
	if tempStr != "" {
		fmt.Sscanf(tempStr, "%f", &temperature)
	}

	// Save all settings
	config.SetLLMSettings(config.LLMSettings{
		Provider:    provider,
		APIKey:      apiKey,
		Model:       model,
		Temperature: temperature,
	})

	// TODO: Forward to FastAPI /agents_config endpoint

	components.SettingsSuccess().Render(r.Context(), w)
}

func SaveNeo4jSettingsHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	// TODO: Forward to FastAPI

	components.SettingsSuccess().Render(r.Context(), w)
}

func TestNeo4jHandler(w http.ResponseWriter, r *http.Request) {
	// TODO: Forward to FastAPI /neo4j/test endpoint

	w.Write([]byte(`<div class="alert alert-success"><span>Connection successful</span></div>`))
}

func SaveSearchSettingsHandler(w http.ResponseWriter, r *http.Request) {
	r.ParseForm()
	// TODO: Forward to FastAPI

	components.SettingsSuccess().Render(r.Context(), w)
}
