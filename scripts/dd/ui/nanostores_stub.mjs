export function atom(v) { let val = v; const subs = new Set(); return { get: () => val, set: (n) => { val = n; subs.forEach(f => f(n)); }, subscribe: (f) => { subs.add(f); f(val); return () => subs.delete(f); }, listen: (f) => { subs.add(f); return () => subs.delete(f); } }; }
export function map(v) { return atom(v || {}); }
