package config

import (
	"sync"
)

// LLMSettings holds the persisted LLM configuration
type LLMSettings struct {
	Provider    string  `json:"provider"`
	APIKey      string  `json:"api_key"`
	Model       string  `json:"model"`
	Temperature float64 `json:"temperature"`
}

// SettingsStore provides thread-safe access to settings
type SettingsStore struct {
	mu       sync.RWMutex
	llm      LLMSettings
	models   []Option // Cached models from last fetch
}

var store = &SettingsStore{
	llm: LLMSettings{
		Provider:    DefaultProvider,
		Model:       DefaultModel,
		Temperature: 0.0,
	},
	models: FallbackModels,
}

// GetLLMSettings returns the current LLM settings
func GetLLMSettings() LLMSettings {
	store.mu.RLock()
	defer store.mu.RUnlock()
	return store.llm
}

// SetLLMSettings updates the LLM settings
func SetLLMSettings(s LLMSettings) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.llm = s
}

// UpdateLLMField updates a single field
func UpdateLLMField(field string, value interface{}) {
	store.mu.Lock()
	defer store.mu.Unlock()
	switch field {
	case "provider":
		store.llm.Provider = value.(string)
	case "api_key":
		store.llm.APIKey = value.(string)
	case "model":
		store.llm.Model = value.(string)
	case "temperature":
		store.llm.Temperature = value.(float64)
	}
}

// GetCachedModels returns the cached models list
func GetCachedModels() []Option {
	store.mu.RLock()
	defer store.mu.RUnlock()
	if len(store.models) == 0 {
		return FallbackModels
	}
	return store.models
}

// SetCachedModels updates the cached models list
func SetCachedModels(models []Option) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.models = models
}
