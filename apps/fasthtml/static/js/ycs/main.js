/* YCS — main ES-module entry. Reads `data-ycs-stage` on the page root
 * and dynamic-imports the per-stage module. Lets a single <script>
 * tag drive every wizard step.
 */
const root = document.querySelector(".ycs-page");
const stage = root?.dataset?.ycsStage || "source";

switch (stage) {
    case "source":
        await import("@ycs/source.js");
        break;
    case "ingest":
        await import("@ycs/ingest.js");
        break;
    case "ask":
        await import("@ycs/ask.js");
        break;
}
