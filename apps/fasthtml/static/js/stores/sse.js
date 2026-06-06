// stores/sse.js — SSE connection-status atom.
//
// One per active SSE stream. The map's key is the run/thread id; the
// value is the connection phase. Consumers can `subscribe()` to know
// when to show "reconnecting" hints or to abort their UI updates.
//
// Values: Map<string, 'connecting' | 'open' | 'reconnecting' | 'closed' | 'errored'>
import { map } from 'nanostores';

export const $sseStreams = map({});
