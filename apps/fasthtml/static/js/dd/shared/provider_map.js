// Provider map from COELHO LLM Rotator — single source of truth for provider display.
// Fetches /api/v1/llm/rotator/models (proxied to coelho-llm-rotator) and builds bare model → provider.
let _map = null;
let _promise = null;

async function _fetchMap() {
  try {
    const r = await fetch('/api/v1/llm/rotator/models');
    if (!r.ok) return null;
    const data = await r.json();
    const m = new Map();
    for (const item of data.data || []) {
      const id = String(item.id || '');
      const provider = String(item.owned_by || '');
      if (id && provider && provider !== 'rotator') m.set(id.toLowerCase(), provider);
      // also map provider/model → provider for prefixed ids
      if (id.includes('/')) {
        const bare = id.split('/').slice(1).join('/').toLowerCase();
        if (!m.has(bare)) m.set(bare, provider);
      }
    }
    // also handle openrouter bare :free without prefix via owned_by
    return m;
  } catch {
    return null;
  }
}

export async function getProviderMap() {
  if (_map) return _map;
  if (_promise) return _promise;
  _promise = _fetchMap().then(m => { _map = m; return m; });
  return _promise;
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
  return fallbackProvider || 'implicit';
}
