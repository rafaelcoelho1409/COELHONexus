// Provider display helpers — RETIRED live map.
// The live catalog (`/api/v1/llm/rotator/models`) was a gateway-specific
// surface, removed with it. `getProviderMap()` now always resolves null;
// callers already null-check (`if (_rrProviderMap)`). `providerForModel`
// falls back to the bare model id. `displayProviderName` kept for labels.
const _TTL_MS = 60_000;
let _map = null;
let _fetchedAt = 0;
let _promise = null;

async function _fetchMap() {
  return null;
}

export function getProviderMap() {
  const stale = _map && (Date.now() - _fetchedAt) >= _TTL_MS;
  if (_map && !stale) return Promise.resolve(_map);
  if (!_promise) {
    _promise = _fetchMap().then(m => {
      _promise = null;
      if (m) { _map = m; _fetchedAt = Date.now(); }
      return _map;
    });
  }
  // Serve stale data immediately if we have it; refresh continues in the background.
  return stale ? Promise.resolve(_map) : _promise;
}

// Rotator canonical provider id → human display name (see COELHOLLMRotator
// chain/service.py's _PROVIDER_ID_CANDIDATES for the canonical id list).
const _DISPLAY_NAMES = {
  nim: 'NVIDIA NIM',
  groq: 'Groq',
  cerebras: 'Cerebras',
  openrouter: 'OpenRouter',
  mistral: 'Mistral',
  gemini: 'Gemini',
  sambanova: 'SambaNova',
  deepseek: 'DeepSeek',
};

export function displayProviderName(id) {
  const key = String(id || '').toLowerCase();
  return _DISPLAY_NAMES[key] || String(id || 'Unknown');
}

export function providerForModel(model, fallbackProvider) {
  if (!model) return fallbackProvider || 'unknown';
  const lower = String(model).toLowerCase();
  if (_map && _map.has(lower)) return _map.get(lower);
  // try bare after slash
  const idx = lower.lastIndexOf('/');
  if (idx > 0) {
    const bare = lower.slice(idx + 1);
    if (_map && _map.has(bare)) return _map.get(bare);
  }
  return fallbackProvider || 'unknown';
}
