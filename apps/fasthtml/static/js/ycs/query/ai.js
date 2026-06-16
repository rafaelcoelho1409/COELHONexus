/* AI text-to-DSL — consumes the `/api/v1/ycs/query/ai/{backend}` SSE
 * stream and writes generated tokens into the CodeMirror editor.
 *
 * Two-pass:
 *   1. While the model streams, every `data: {"event": "chunk", ...}`
 *      frame is appended to the editor (so the user watches it write
 *      in real-time, vs. an opaque spinner).
 *   2. On `event: "done"`, we REPLACE the editor body with the cleaned
 *      `final` text — same content as the stream when generation
 *      succeeded, OR the self-repaired version when the first attempt
 *      failed safety. Either way the editor lands in a runnable state.
 */
const API = "/api/v1/ycs/query/ai";


/* `fetch()` throws TypeError "Failed to fetch" on every network-level
 * failure (CORS, DNS, server down, connection refused). The raw text
 * is unhelpful — "Failed to fetch" doesn't tell the user the backend
 * isn't running. Surface a friendlier explanation while keeping the
 * original message available via `cause` for debugging. */
function humanizeError(e) {
    if (!e) return "Unknown error";
    if (e.name === "AbortError") return "Stopped";
    const msg = e.message || String(e);
    if (e instanceof TypeError && /failed to fetch|networkerror|load failed/i.test(msg)) {
        return "Cannot reach the API server — is the backend running?";
    }
    return msg;
}


/* SSE for POST bodies — the native EventSource API only supports GET,
 * so we implement the parser manually over fetch().body's
 * ReadableStream. Conservative: handles `data:` lines, ignores
 * `event:` lines (we encode all event types in the JSON), buffers
 * across chunk boundaries.
 *
 * `onFrame` is fired per decoded JSON object. `abortSignal` propagates
 * Stop to the underlying fetch. */
async function streamSSE(url, body, { abortSignal, onFrame }) {
    const resp = await fetch(url, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
        signal:  abortSignal,
    });
    if (!resp.ok || !resp.body) {
        const text = await resp.text().catch(() => "");
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
    }
    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE frames are separated by blank lines (`\n\n`). Parse all
        // complete frames in the buffer; keep any trailing partial.
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
            const raw = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const dataLines = raw
                .split("\n")
                .filter((l) => l.startsWith("data:"))
                .map((l) => l.slice(5).trim());
            if (!dataLines.length) continue;
            const payload = dataLines.join("\n");
            try {
                onFrame(JSON.parse(payload));
            } catch (_) {
                /* malformed frame — ignore */
            }
        }
    }
}


/* Run one generation. `editor` is the handle from makeEditor().
 *
 * Lifecycle the orchestrator should drive:
 *   ai.start({ backend, prompt, previous, editor, onStatus, onDone })
 *      → returns an AbortController for the Stop button.
 *
 * `onStatus(kind, text)` fires at every milestone so the orchestrator
 * can paint the AI panel's status pill + show/hide the Stop button.
 * `kind` is one of: "running" | "repair" | "ok" | "error". */
export function start({
    backend, prompt, previous, editor, onStatus, onModel, onDone,
}) {
    const ctrl = new AbortController();
    let receivedAny = false;
    // Defer the editor wipe until the FIRST chunk arrives. Earlier we
    // wiped immediately + restored on error, which flashed a blank
    // editor every time `fetch()` failed network-level (skaffold off,
    // CORS, etc.) — the user saw their WIP vanish + reappear. Now the
    // editor stays untouched on network failure; only successful
    // generation overwrites it.
    let editorWiped = false;
    const wipeEditorOnce = () => {
        if (editorWiped) return;
        editor.setText("");
        editorWiped = true;
    };

    onStatus?.("running", "Generating…");
    onModel?.("");          // clear any prior model chip

    streamSSE(`${API}/${backend}`, {
        app:      "ycs",
        prompt,
        previous: previous || "",
    }, {
        abortSignal: ctrl.signal,
        onFrame: (frame) => {
            if (frame.event === "start") return;
            if (frame.event === "model") {
                onModel?.(frame.model || "");
                return;
            }
            if (frame.event === "chunk") {
                wipeEditorOnce();
                receivedAny = true;
                editor.appendText(frame.data || "");
                return;
            }
            if (frame.event === "repair") {
                onStatus?.("repair", "Self-repairing…");
                // The repair pass re-streams; reset the editor so the
                // rejected attempt's text doesn't get concatenated.
                if (editorWiped) editor.setText("");
                return;
            }
            if (frame.event === "done") {
                // Replace the streamed buffer with the cleaned final
                // body (strips fences / prose). Only if we have a
                // non-empty final — otherwise leave the editor alone.
                if (frame.final && frame.final.trim()) {
                    editor.setText(frame.final);
                }
                if (frame.ok) {
                    onStatus?.("ok", "Generated");
                } else {
                    onStatus?.("error", `Rejected: ${frame.error || "unknown"}`);
                }
                onDone?.(frame);
                return;
            }
            if (frame.event === "error") {
                onStatus?.("error", frame.error || "AI error");
                onDone?.(frame);
                return;
            }
        },
    }).catch((e) => {
        const msg = humanizeError(e);
        onStatus?.("error", msg);
        onDone?.({ event: "error", error: msg });
    });

    return ctrl;
}
