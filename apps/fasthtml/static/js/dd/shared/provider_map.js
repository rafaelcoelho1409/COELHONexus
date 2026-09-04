// Provider map from COELHO LLM Rotator — single source of truth for provider display.
// Fetches /api/v1/llm/rotator/models (proxied to coelho-llm-rotator) and builds bare model → provider.
// TTL'd: provider health (cooldowns, quota exhaustion, transient discovery
// errors) is volatile, so a one-shot fetch can permanently miss a provider
// that wasn't alive yet at page load. Re-fetch periodically instead of
// caching forever; serve the stale map immediately while refreshing.
const _TTL_MS = 60_000;
let _map = null;
let _fetchedAt = 0;
let _promise = null;

async function _fetchMap() {
  try {
    const r = await fetch('/api/v1/llm/rotator/models');
    if (!r.ok) return null;
    const data = await r.json();
    const m = new Map();
    // Model-first catalog: each entry aggregates one canonical model across
    // possibly several providers, so `owned_by` is always the literal string
    // "rotator" — the real per-provider mapping lives in `providers`, an
    // array of "provider/bare_id" labels (see fastapi/api/v1/llm/openai/router.py).
    for (const item of data.data || []) {
      const labels = Array.isArray(item.providers) ? item.providers : [];
      for (const label of labels) {
        const s = String(label || '');
        const idx = s.indexOf('/');
        if (idx <= 0) continue;
        const provider = s.slice(0, idx);
        const bare = s.slice(idx + 1).toLowerCase();
        if (bare && provider && !m.has(bare)) m.set(bare, provider);
      }
    }
    return m;
  } catch {
    return null;
  }
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
