package config

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"
)

// Option represents a select dropdown option
type Option struct {
	Value string `json:"value"`
	Label string `json:"label"`
}

// Provider represents an LLM provider configuration
type Provider struct {
	ID           string `json:"id"`
	Name         string `json:"name"`
	APIKeyHint   string `json:"api_key_hint"`
	ModelsURL    string `json:"models_url"`
}

// NVIDIAModel represents a model from NVIDIA API
type NVIDIAModel struct {
	ID string `json:"id"`
}

// NVIDIAModelsResponse represents the API response
type NVIDIAModelsResponse struct {
	Data []NVIDIAModel `json:"data"`
}

var (
	// Available providers (only NVIDIA NIM for now)
	Providers = []Provider{
		{
			ID:         "nvidia_nim",
			Name:       "NVIDIA NIM",
			APIKeyHint: "nvapi-...",
			ModelsURL:  "https://build.nvidia.com",
		},
	}

	// Default provider
	DefaultProvider = "nvidia_nim"

	// NVIDIA API endpoint
	nvidiaAPIURL = "https://integrate.api.nvidia.com/v1/models"

	// HTTP client with timeout
	httpClient = &http.Client{Timeout: 10 * time.Second}

	// Multimodal detection patterns (from agent_nvidia.py)
	multimodalPatterns = []string{
		"vision", "-vl", "_vl", "vlm", "fuyu", "paligemma",
		"llava", "pixtral", "molmo", "idefics", "blip", "nvclip",
		"florence", "multimodal", "kosmos", "neva", "/vila",
		"deplot", "maverick", "scout", "kimi-k2.5",
	}

	// Fallback models when no API key is provided
	FallbackModels = []Option{
		{Value: "meta/llama-3.3-70b-instruct", Label: "Llama 3.3 70B Instruct"},
		{Value: "meta/llama-3.1-405b-instruct", Label: "Llama 3.1 405B Instruct"},
		{Value: "meta/llama-3.1-70b-instruct", Label: "Llama 3.1 70B Instruct"},
		{Value: "meta/llama-3.1-8b-instruct", Label: "Llama 3.1 8B Instruct"},
		{Value: "mistralai/mixtral-8x22b-instruct-v0.1", Label: "Mixtral 8x22B Instruct"},
		{Value: "nvidia/nemotron-4-340b-instruct", Label: "Nemotron 4 340B Instruct"},
	}

	// Default model
	DefaultModel = "meta/llama-3.3-70b-instruct"
)

// IsMultimodal checks if a model supports vision based on its ID
func IsMultimodal(modelID string) bool {
	lower := strings.ToLower(modelID)
	for _, pattern := range multimodalPatterns {
		if strings.Contains(lower, pattern) {
			return true
		}
	}
	return false
}

// formatModelLabel creates a human-readable label from model ID
func formatModelLabel(modelID string) string {
	parts := strings.Split(modelID, "/")
	name := parts[len(parts)-1]

	name = strings.ReplaceAll(name, "-", " ")
	name = strings.ReplaceAll(name, "_", " ")

	words := strings.Fields(name)
	for i, word := range words {
		if len(word) > 0 {
			words[i] = strings.ToUpper(word[:1]) + word[1:]
		}
	}

	label := strings.Join(words, " ")

	if IsMultimodal(modelID) {
		label += " [vision]"
	}

	return label
}

// FetchModelsWithKey fetches available models from NVIDIA API using provided key
func FetchModelsWithKey(apiKey string) ([]Option, error) {
	if apiKey == "" {
		return FallbackModels, nil
	}

	req, err := http.NewRequest("GET", nvidiaAPIURL, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+apiKey)

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, nil
	}

	var apiResp NVIDIAModelsResponse
	if err := json.NewDecoder(resp.Body).Decode(&apiResp); err != nil {
		return nil, err
	}

	var models []Option
	for _, m := range apiResp.Data {
		models = append(models, Option{
			Value: m.ID,
			Label: formatModelLabel(m.ID),
		})
	}

	// Sort alphabetically by label (display name)
	sort.Slice(models, func(i, j int) bool {
		return strings.ToLower(models[i].Label) < strings.ToLower(models[j].Label)
	})

	return models, nil
}
