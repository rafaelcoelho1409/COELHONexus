/** Docs Distiller — live progress lines: one stream event → the activity text shown on its node. */

import type { StageEvent } from "../entities";
import type { RunStage } from "../params";
import { stringList } from "./chapters";
import { fmtInt } from "./format";

const num = (ev: StageEvent, key: string): number => {
  const value = ev[key];
  return typeof value === "number" ? value : 0;
};

const str = (ev: StageEvent, key: string): string => {
  const value = ev[key];
  return typeof value === "string" ? value : "";
};

const failedSuffix = (count: number): string => (count ? ` · ${count} failed` : "");

/** Planner node activity text; empty for events that carry nothing worth showing. */
export function plannerLiveText(ev: StageEvent): string {
  const kind = ev.kind || "";
  switch (ev.step) {
    case "corpus_load":
      if (kind === "start") return "· reading manifest…";
      if (kind === "done")
        return `✓ ${fmtInt(num(ev, "files"))} files, ${Math.trunc(num(ev, "total_bytes") / 1024)} KB`;
      return "";
    case "off_topic":
      if (kind === "start") return `· filtering ${fmtInt(num(ev, "files"))} files…`;
      if (kind === "anchors_embedded") return "· anchors embedded (pos + neg)";
      if (kind === "llm_progress") {
        return `· judged ${fmtInt(num(ev, "judged"))} / ${fmtInt(num(ev, "total"))} (keep ${num(ev, "llm_keep")}, drop ${num(ev, "llm_drop")})`;
      }
      if (kind === "done")
        return `✓ kept ${fmtInt(num(ev, "kept"))}/${fmtInt(num(ev, "total"))} (${num(ev, "wall_ms")} ms)`;
      return "";
    case "doc_distill":
      if (kind === "start") return `· distilling ${fmtInt(num(ev, "n_files"))} docs…`;
      if (kind === "done") {
        const cost = ev.cache_hit === true ? "cache hit" : `${num(ev, "wall_ms")} ms`;
        return `✓ ${fmtInt(num(ev, "n_distilled"))} distilled${failedSuffix(num(ev, "n_failed"))} (${cost})`;
      }
      return "";
    case "chapter_propose":
      if (kind === "start") return "· loading distillates + extracting structural seeds…";
      if (kind === "sampling") return `· firing N=${num(ev, "n_samples") || 3} proposals…`;
      if (kind === "done") {
        const titles = stringList(ev.titles).slice(0, 3);
        const tail = titles.length > 0 ? `: ${titles.join(", ")}` : "";
        return `✓ ${fmtInt(num(ev, "n_proposals"))} chapters proposed${tail}`;
      }
      return "";
    case "chapter_assign":
      if (kind === "start") return `· scoring ${fmtInt(num(ev, "n_docs"))} docs…`;
      if (kind === "done")
        return `✓ ${fmtInt(num(ev, "n_assigned"))} assigned${failedSuffix(num(ev, "n_failed"))}`;
      return "";
    case "chapter_select":
      if (kind === "start") return `· greedy coverage over ${fmtInt(num(ev, "n_proposals"))} proposals…`;
      if (kind === "done") {
        return `✓ ${fmtInt(num(ev, "n_chapters"))} chapters selected · ${Math.round(num(ev, "coverage") * 100)}% coverage`;
      }
      return "";
    case "plan_write":
      if (kind === "start") return `· hashing inputs… (${str(ev, "manifest_hash").slice(0, 8)})`;
      if (kind === "loaded")
        return `· loaded ${num(ev, "n_chapters_in")} chapters · ${num(ev, "n_docs")} docs`;
      if (kind === "sanitized") {
        const dropped = num(ev, "n_dropped");
        return `· sanitized · ${num(ev, "n_chapters")} chapters${dropped ? ` · ${dropped} empty dropped` : ""}`;
      }
      if (kind === "done") return `✓ ${num(ev, "n_chapters")} chapters · ${num(ev, "n_sources")} sources`;
      return "";
    default:
      return "";
  }
}

/** One activity-log line for any event: the node's own wording, else the bare event kind. */
export function eventSummary(stage: RunStage, ev: StageEvent): string {
  const wording = stage === "planner" ? plannerLiveText(ev) : synthLiveText(ev);
  return wording || ev.kind || "event";
}

/** Synth node activity text (per-chapter substeps); empty when unknown. */
export function synthLiveText(ev: StageEvent): string {
  const kind = ev.kind || "";
  const chapter = str(ev, "chapter_title") || str(ev, "chapter_id") || "chapter";
  if (kind === "start") return `· starting ${ev.step === "synth" ? "" : `${ev.step} for `}${chapter}…`;
  if (kind === "done") return `✓ done (${num(ev, "wall_ms")} ms)`;
  if (ev.step === "digest_construct" && kind === "sources_loaded")
    return `· sources loaded (${num(ev, "n_sources")} files)`;
  if (ev.step === "sawc_write" && kind === "section_done") return `· section "${str(ev, "section")}" done`;
  if (ev.step === "render_audit_write" && kind === "artifacts_written")
    return `· artifacts written (${num(ev, "n_files")} files)`;
  return "";
}
