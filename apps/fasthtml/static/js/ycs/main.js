/* YCS — main ES-module entry. Reads `data-ycs-stage` on the page root
 * and dynamic-imports the per-stage module. Lets a single <script>
 * tag drive every wizard step.
 *
 * `pipeline_panel.js` is loaded UNCONDITIONALLY before the stage
 * module so the 3-bar progress panel persists across Source / Ingest
 * / Ask navigation + page refreshes (state survives via the
 * `ycs:pipeline:active` localStorage entry).
 */
const root = document.querySelector(".ycs-page");
const stage = root?.dataset?.ycsStage || "source";

await import("@ycs/pipeline_panel.js");

switch (stage) {
    case "source":
        await import("@ycs/source.js");
        break;
    case "ingestion":
        await import("@ycs/ingest.js");
        await import("@ycs/ingest/library.js");
        break;
    case "ask":
        await import("@ycs/ask.js");
        break;
    case "query":
        await import("@ycs/query.js");
        break;
}
