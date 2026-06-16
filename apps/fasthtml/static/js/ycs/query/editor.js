/* CodeMirror 6 setup + language switching for the Query workbench.
 *
 * CM6 ships as a constellation of small packages; we pull them through
 * the import map (see layout/head.py). All resolutions hit esm.sh's
 * standalone bundles so the editor is ~250 KB across all CM6 deps —
 * well below the Monaco floor (~5 MB) per the SOTA write-up.
 *
 * Language modes:
 *   · ES + Qdrant — `@codemirror/lang-json` (full Lezer JSON grammar)
 *   · Neo4j Cypher — legacy StreamLanguage from @codemirror/legacy-modes.
 *     That mode is keyword/regex-based, not a full Lezer parser — but
 *     it covers highlighting + bracket-matching and ships with zero
 *     bundling cost. Upgrading to @neo4j-cypher/lang-codemirror is a
 *     drop-in swap when we want LSP-style autocomplete on relationships.
 */
import { EditorView, keymap, lineNumbers, highlightActiveLine }
    from "@codemirror/view";
import { EditorState, Compartment } from "@codemirror/state";
import { history, defaultKeymap, historyKeymap, indentWithTab }
    from "@codemirror/commands";
import {
    bracketMatching, indentOnInput, defaultHighlightStyle, syntaxHighlighting,
    StreamLanguage,
} from "@codemirror/language";
import { json as jsonLang } from "@codemirror/lang-json";
import { cypher as cypherMode } from "@codemirror/legacy-modes/mode/cypher";


/* Per-backend default body — gives the user a runnable template the
 * second they pick a backend. Empty string would leave the editor blank
 * and the Run button would 400 with "Empty body". */
const DEFAULT_BODIES = {
    elasticsearch: JSON.stringify({
        query: {
            multi_match: {
                query: "transformer attention",
                fields: ["title^3", "description", "channel", "content"],
                type: "best_fields",
            },
        },
        size: 10,
    }, null, 2),
    qdrant: JSON.stringify({
        op: "scroll",
        limit: 10,
        with_payload: true,
    }, null, 2),
    neo4j: [
        "// Browse the YCS knowledge graph.",
        "MATCH (v:Video)",
        "RETURN v.title AS title, v.webpage_url AS url",
        "LIMIT 10",
    ].join("\n"),
};


/* CM6 language compartment — lets us swap the language mode without
 * rebuilding the whole EditorState (which would drop history). */
const langCompartment = new Compartment();


function languageFor(backend) {
    if (backend === "neo4j") return StreamLanguage.define(cypherMode);
    return jsonLang();           // ES + Qdrant both use JSON
}


/* Initialize the editor inside `mount`, return a handle the
 * orchestrator can call into. `onRun` is fired on Ctrl/Cmd+Enter so
 * the user can run the query straight from the keyboard. */
export function makeEditor(mount, { onRun }) {
    const initialBackend = "elasticsearch";

    const runKeymap = keymap.of([
        {
            key: "Mod-Enter",
            preventDefault: true,
            run: () => { onRun?.(); return true; },
        },
    ]);

    const state = EditorState.create({
        doc: DEFAULT_BODIES[initialBackend],
        extensions: [
            lineNumbers(),
            history(),
            bracketMatching(),
            indentOnInput(),
            highlightActiveLine(),
            syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
            langCompartment.of(languageFor(initialBackend)),
            keymap.of([
                ...defaultKeymap,
                ...historyKeymap,
                indentWithTab,
            ]),
            runKeymap,
            EditorView.theme({
                "&": {
                    height: "100%",
                    fontSize: "0.88rem",
                    fontFamily:
                        "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
                },
                ".cm-scroller": { overflow: "auto" },
                ".cm-content":  { padding: "10px 0" },
                ".cm-gutters":  {
                    background: "rgba(0,0,0,0.02)",
                    borderRight: "1px solid var(--border, #ddd)",
                    color: "var(--text-muted, #888)",
                },
            }, { dark: false }),
        ],
    });

    // Replace the placeholder text with the editor DOM.
    mount.innerHTML = "";
    mount.removeAttribute("data-cm-loading");
    const view = new EditorView({ state, parent: mount });

    return {
        view,
        backend: initialBackend,
        /* Pull the entire document as a single string. */
        getText() {
            return view.state.doc.toString();
        },
        /* Replace the entire document. Used for backend-switch
         * scaffolding + for streaming AI generation that wants to
         * replace whatever's there. */
        setText(text) {
            view.dispatch({
                changes: { from: 0, to: view.state.doc.length, insert: String(text ?? "") },
            });
        },
        /* Append a chunk at the end of the document. The AI SSE stream
         * uses this so each token lands without overwriting prior
         * chunks. */
        appendText(chunk) {
            const end = view.state.doc.length;
            view.dispatch({ changes: { from: end, insert: String(chunk ?? "") } });
        },
        /* Swap language mode AND replace the document with the backend's
         * scaffold. The tabs ARE the templates — clicking ES / Qdrant /
         * Neo4j is the user telling us "I want to start from this
         * backend's example", so we always reset. (Previously this only
         * fired when the editor was empty or held an untouched scaffold,
         * which felt sticky when the user wanted a clean default.) */
        setBackend(next) {
            if (next === this.backend) return;
            view.dispatch({
                effects: langCompartment.reconfigure(languageFor(next)),
            });
            this.backend = next;
            this.setText(DEFAULT_BODIES[next] ?? "");
        },
    };
}
