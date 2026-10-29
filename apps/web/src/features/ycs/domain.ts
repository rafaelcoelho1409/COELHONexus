/** YouTube Content Search — pure functions: URL/ID parsers, formatting, the Search-request reader and the
 * selection/routing rules of the Source stage. No React, no fetch. */

import type {
  LibraryItem,
  PipelineWipe,
  SearchRequestPayload,
  TaskMeta,
  TaskResult,
  VideoSnippet,
} from "./entities";
import { searchRequestSchema } from "./entities";
import type { SourceMode } from "./params";

// ============================================================
// URL / ID parsers — precedence playlist > channel > video
// (mirrors yt-dlp's own disambiguation).
// ============================================================

export type ParsedSource = { kind: "playlist" | "channel" | "video"; identity: string; display: string };

export function parsePlaylist(text: string): ParsedSource | null {
  const t = (text ?? "").trim();
  if (!t) return null;
  let m = t.match(/[?&]list=([A-Za-z0-9_-]+)/);
  if (m?.[1]) return { kind: "playlist", identity: m[1], display: m[1] };
  m = t.match(/^((?:PL|UU|LL|RD|OL)[A-Za-z0-9_-]{10,})$/);
  if (m?.[1]) return { kind: "playlist", identity: m[1], display: m[1] };
  return null;
}

export function parseChannel(text: string): ParsedSource | null {
  const t = (text ?? "").trim();
  if (!t) return null;
  let m = t.match(/youtube\.com\/(@[\w.-]+)/);
  if (m?.[1]) return { kind: "channel", identity: m[1], display: m[1] };
  m = t.match(/youtube\.com\/channel\/(UC[\w-]{22})/);
  if (m?.[1]) return { kind: "channel", identity: m[1], display: m[1] };
  m = t.match(/^(@[\w.-]+)$/);
  if (m?.[1]) return { kind: "channel", identity: m[1], display: m[1] };
  m = t.match(/^(UC[A-Za-z0-9_-]{22})$/);
  if (m?.[1]) return { kind: "channel", identity: m[1], display: m[1] };
  return null;
}

function parseVideo(text: string): ParsedSource | null {
  const t = (text ?? "").trim();
  if (!t) return null;
  let m = t.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([A-Za-z0-9_-]{11})/);
  if (m?.[1]) return { kind: "video", identity: m[1], display: m[1] };
  m = t.match(/^([A-Za-z0-9_-]{11})$/);
  if (m?.[1]) return { kind: "video", identity: m[1], display: m[1] };
  return null;
}

/** Paste → claim card. `parsed` replaces the input (the resolved state feels committed), `invalid` keeps
 * the input and disables the submit, `hidden` means nothing was typed. */
export type UnfurlState =
  | { state: "hidden" }
  | { state: "parsed"; parsed: ParsedSource }
  | { state: "invalid"; raw: string };

export function unfurlState(raw: string, parser: (text: string) => ParsedSource | null): UnfurlState {
  if (!raw.trim()) return { state: "hidden" };
  const parsed = parser(raw);
  if (parsed) return { state: "parsed", parsed };
  return { state: "invalid", raw };
}

/** The pasted Videos textarea → deduped video ids. Accepts bare 11-char ids, watch URLs, youtu.be URLs,
 * mixed CSV + newline; unparsable tokens are skipped. */
export function parseTextareaIds(text: string): string[] {
  if (!text) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[\n,]+/)) {
    const parsed = parseVideo(raw);
    if (parsed && !seen.has(parsed.identity)) {
      seen.add(parsed.identity);
      out.push(parsed.identity);
    }
  }
  return out;
}

// ============================================================
// Formatting
// ============================================================

export function fmtCount(n: number | null | undefined): string {
  if (n == null) return "";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Full integer with thousands separators — token counters. */
export function fmtInt(n: number | null | undefined): string {
  if (n == null) return "";
  return new Intl.NumberFormat("en-US").format(Math.round(n));
}

export function fmtDate(yyyymmdd: string | null | undefined): string {
  if (yyyymmdd?.length !== 8) return "";
  return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

/** "en, pt, es" (or whitespace-separated) → list; null when empty so the body omits the field. */
export function parseLangs(raw: string | null | undefined): Array<string> | null {
  const v = (raw ?? "").trim();
  if (!v) return null;
  return v
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Best-effort duration: yt-dlp's `duration_string` is missing for live/premiere entries. */
export function durationLabel(v: VideoSnippet): string | null {
  if (v.duration_string) return v.duration_string;
  const s = v.duration;
  if (!s || s <= 0) return null;
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function ago(diffMs: number): string | null {
  const s = Math.max(0, Math.round(diffMs / 1000));
  if (s < 60) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return d === 1 ? "1 day ago" : `${d} days ago`;
  return null; // ≥30 days → absolute
}

function absDate(y: number, mo: number, da: number): string | null {
  if (!y) return null;
  if (!mo || mo < 1) return String(y);
  const monthLabel = MONTHS[mo - 1] ?? "";
  if (!da || da < 1) return `${monthLabel} ${y}`;
  return `${monthLabel} ${da}, ${y}`;
}

/** Best-effort release date: partial YYYYMMDD ("20240900" → September 2024) then unix epoch seconds.
 * Relative inside the last 30 days, absolute otherwise — YouTube's own convention. */
function dateLabel(v: VideoSnippet): string | null {
  if (v.upload_date && /^\d{8}$/.test(v.upload_date)) {
    const y = Number.parseInt(v.upload_date.slice(0, 4), 10);
    const mo = Number.parseInt(v.upload_date.slice(4, 6), 10);
    const da = Number.parseInt(v.upload_date.slice(6, 8), 10);
    if (mo && da) {
      const rel = ago(Date.now() - Date.UTC(y, mo - 1, da));
      if (rel) return rel;
    }
    return absDate(y, mo, da);
  }
  const ts = v.release_timestamp ?? v.timestamp;
  if (typeof ts === "number" && ts > 0) {
    const dt = new Date(ts * 1000);
    const rel = ago(Date.now() - dt.getTime());
    if (rel) return rel;
    return absDate(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());
  }
  return null;
}

/** Per-kind meta line of a search result row: videos show views · duration · channel · date; channels and
 * playlists show their label and the probed video count (absent when the backend's probe failed). */
export function resultMeta(v: VideoSnippet): string {
  const kind = v.kind || "video";
  if (kind === "video") {
    return [
      v.view_count != null ? `${fmtCount(v.view_count)} views` : null,
      durationLabel(v),
      v.channel,
      dateLabel(v),
    ]
      .filter(Boolean)
      .join(" · ");
  }
  const countPart =
    v.video_count != null ? `${fmtCount(v.video_count)} video${v.video_count === 1 ? "" : "s"}` : null;
  return [kind === "channel" ? "Channel" : "Playlist", countPart].filter(Boolean).join(" · ");
}

// ============================================================
// Search request — FormData → SearchRequest
// ============================================================

const NUMERIC_FIELDS = new Set([
  "duration_min",
  "duration_max",
  "min_views",
  "max_views",
  "min_likes",
  "age_limit",
]);
const DATE_FIELDS = new Set(["date_after", "date_before"]);
const TOGGLE_FIELDS = new Set(["sort_by_date", "exclude_shorts"]);
/** Whitelist of `SearchRequest` fields; anything else on the form is dropped (the backend runs
 * `extra = "forbid"` and 422s unknown fields). */
const KNOWN_FIELDS = new Set([
  "query",
  "duration",
  "kind_filter",
  ...NUMERIC_FIELDS,
  ...DATE_FIELDS,
  ...TOGGLE_FIELDS,
  "is_live",
  "live_status",
  "availability",
  "title_contains",
  "description_contains",
  "channel_name",
]);

/** `<input type="date">` emits YYYY-MM-DD; the backend wants YYYYMMDD. Partial dates are skipped entirely. */
function isoToYyyymmdd(v: string): string | null {
  const ymd = v.replaceAll("-", "");
  return /^\d{8}$/.test(ymd) ? ymd : null;
}

export function readSearchRequest(form: FormData, pageSize: number): SearchRequestPayload {
  const record: Record<string, unknown> = {};
  for (const [key, value] of form.entries()) {
    if (typeof value !== "string" || !value || !KNOWN_FIELDS.has(key)) continue;
    if (TOGGLE_FIELDS.has(key)) {
      record[key] = true;
      continue;
    }
    if (NUMERIC_FIELDS.has(key)) {
      const n = Number.parseInt(value, 10);
      if (Number.isFinite(n)) record[key] = n;
      continue;
    }
    if (DATE_FIELDS.has(key)) {
      const ymd = isoToYyyymmdd(value);
      if (ymd) record[key] = ymd;
      continue;
    }
    record[key] = value;
  }
  record.max_results = pageSize;
  return searchRequestSchema.parse(record);
}

export type FilterChip = { key: string; label: string };

const KIND_FILTER_LABEL = { video: "Videos", channel: "Channels", playlist: "Playlists" } as const;

/** The filters a submitted request carries, as removable chips in the order the filter panel lists them.
 * `key` is the form field's name, so removing a chip clears that field. */
export function filterChips(req: SearchRequestPayload): FilterChip[] {
  const chips: FilterChip[] = [];
  const add = (key: string, label: string) => chips.push({ key, label });
  if (req.kind_filter) add("kind_filter", `Kind: ${KIND_FILTER_LABEL[req.kind_filter]}`);
  if (req.duration) add("duration", `Duration: ${req.duration}`);
  if (req.date_after) add("date_after", `Uploaded after ${fmtDate(req.date_after)}`);
  if (req.date_before) add("date_before", `Uploaded before ${fmtDate(req.date_before)}`);
  if (req.min_views) add("min_views", `Min views: ${fmtInt(req.min_views)}`);
  if (req.max_views) add("max_views", `Max views: ${fmtInt(req.max_views)}`);
  if (req.min_likes) add("min_likes", `Min likes: ${fmtInt(req.min_likes)}`);
  if (req.title_contains) add("title_contains", `Title: ${req.title_contains}`);
  if (req.channel_name) add("channel_name", `Channel: ${req.channel_name}`);
  if (req.sort_by_date) add("sort_by_date", "Newest first");
  if (req.exclude_shorts) add("exclude_shorts", "No shorts");
  return chips;
}

/** The filters that sit in the "More filters" panel; the others (kind, duration, newest first) are always in view. */
const PANEL_FILTER_KEYS: ReadonlySet<string> = new Set([
  "date_after",
  "date_before",
  "min_views",
  "max_views",
  "min_likes",
  "title_contains",
  "channel_name",
  "exclude_shorts",
]);

/** How many of the active filters are set in the panel — the number on its button. */
export function panelFilterCount(chips: ReadonlyArray<FilterChip>): number {
  return chips.filter((chip) => PANEL_FILTER_KEYS.has(chip.key)).length;
}

/** "1–25 of 25+": where the page sits in what has been fetched; the "+" says the source may hold more. */
export function rangeLabel(page: number, size: number, total: number, exhausted: boolean): string {
  if (total === 0) return "No results";
  const from = (page - 1) * size + 1;
  const to = Math.min(page * size, total);
  return `${from}–${to} of ${total}${exhausted ? "" : "+"}`;
}

// ============================================================
// Selection + bulk routing (Search results list)
// ============================================================

/** Channel and playlist rows are mutually exclusive within their kind — the Channel and Playlist tabs each
 * take one source at a time. Videos are unaffected. */
const SINGLETON_KINDS = new Set(["channel", "playlist"]);

export function selectSnippet(
  selected: ReadonlyMap<string, VideoSnippet>,
  snippet: VideoSnippet,
  on: boolean,
): Map<string, VideoSnippet> {
  const next = new Map(selected);
  const kind = snippet.kind || "video";
  if (on) {
    if (SINGLETON_KINDS.has(kind)) {
      for (const [otherId, other] of Array.from(next.entries())) {
        if (otherId === snippet.id || (other.kind || "video") !== kind) continue;
        next.delete(otherId);
      }
    }
    next.set(snippet.id, snippet);
  } else {
    next.delete(snippet.id);
  }
  return next;
}

/** Select (or deselect) a run of rows — the Shift-click range, applying the same singleton rule. */
export function selectRange(
  selected: ReadonlyMap<string, VideoSnippet>,
  snippets: ReadonlyArray<VideoSnippet>,
  on: boolean,
): Map<string, VideoSnippet> {
  let next = new Map(selected);
  for (const snippet of snippets) next = selectSnippet(next, snippet, on);
  return next;
}

/** Per-kind routing rule: videos go to the Videos tab, channels to Channel, playlists to Playlist. Mixed
 * selections are partitioned — only the matching subset is sent. */
export function routeItems(
  mode: Exclude<SourceMode, "search">,
  selected: ReadonlyMap<string, VideoSnippet>,
): string[] {
  const ids: string[] = [];
  for (const snippet of selected.values()) {
    const kind = snippet.kind || "video";
    if (mode === "videos" && kind === "video") ids.push(snippet.id);
    if (mode === "channel" && kind === "channel") {
      const channelId = snippet.id || snippet.channel_id || "";
      if (channelId) ids.push(channelId);
    }
    if (mode === "playlist" && kind === "playlist" && snippet.id) ids.push(snippet.id);
  }
  return mode === "videos" ? ids : Array.from(new Set(ids));
}

/** Remove the routed items from the selection; the rest stay selected so they can be routed elsewhere. */
export function dropRouted(
  selected: ReadonlyMap<string, VideoSnippet>,
  mode: Exclude<SourceMode, "search">,
  items: ReadonlyArray<string>,
): Map<string, VideoSnippet> {
  const routed = new Set(items);
  const next = new Map<string, VideoSnippet>();
  for (const [id, snippet] of selected.entries()) {
    const kind = snippet.kind || "video";
    const matches =
      (mode === "videos" && kind === "video" && routed.has(id)) ||
      (mode === "channel" && kind === "channel" && routed.has(id)) ||
      (mode === "playlist" && kind === "playlist" && routed.has(id));
    if (!matches) next.set(id, snippet);
  }
  return next;
}

/** Master-checkbox state over the visible rows: all checked, some (indeterminate) or none. */
export function masterState(
  visible: ReadonlyArray<VideoSnippet>,
  selected: ReadonlyMap<string, VideoSnippet>,
): "all" | "some" | "none" {
  if (visible.length === 0) return "none";
  const count = visible.reduce((n, s) => n + (selected.has(s.id) ? 1 : 0), 0);
  if (count === 0) return "none";
  return count === visible.length ? "all" : "some";
}

/** The Library's master-checkbox state over the selected video ids. */
export function libraryMasterState(
  rows: ReadonlyArray<{ video_id: string }>,
  selected: ReadonlySet<string>,
): "all" | "some" | "none" {
  if (rows.length === 0) return "none";
  const count = rows.reduce((n, row) => n + (selected.has(row.video_id) ? 1 : 0), 0);
  if (count === 0) return "none";
  return count === rows.length ? "all" : "some";
}

// ============================================================
// Pipeline panel — bar math, per-store cell statuses, hints
// (port of static/js/ycs/pipeline_panel.js, all pure)
// ============================================================

export type PhaseSnapshot = {
  state: string;
  meta?: TaskMeta | null;
  result?: TaskResult | null;
  error?: string | null;
};

export type PipelinePrefix = "playwright" | "elasticsearch" | "qdrant" | "neo4j";

const TERMINAL_STATES: ReadonlySet<string> = new Set(["SUCCESS", "FAILURE", "REVOKED", "ERROR"]);

export function isTerminal(state: string): boolean {
  return TERMINAL_STATES.has(state);
}

const SPLIT_PHASE_ORDER = ["metadata", "metadata_done", "transcription", "es_indexing"] as const;

function pctFromCounters(meta: TaskMeta | null | undefined): number | null {
  if (meta?.total && meta.current != null) {
    return Math.max(2, Math.min(100, (meta.current / meta.total) * 100));
  }
  return null;
}

/** Percentage of a task-polled bar (the extract task drives both split bars through `subPhasePct`). */
export function phasePct(snapshot: PhaseSnapshot): number {
  if (isTerminal(snapshot.state)) return 100;
  const pct = pctFromCounters(snapshot.meta);
  if (pct != null) return pct;
  if (snapshot.meta?.phase && snapshot.state === "PROGRESS") return 8;
  return 0;
}

/** Percentage label for a task-polled bar. */
export function phaseLabel(snapshot: PhaseSnapshot): string {
  if (snapshot.state === "SUCCESS") return "Done";
  if (snapshot.state === "FAILURE" || snapshot.state === "ERROR") {
    return `Failed: ${snapshot.error?.slice(0, 60) ?? "see logs"}`;
  }
  if (snapshot.state === "PENDING") return "Queued";
  const meta = snapshot.meta;
  if (meta?.current != null && meta.total)
    return `${meta.phase ?? "running"} · ${meta.current}/${meta.total}`;
  if (meta?.phase) return meta.phase;
  return snapshot.state || "Running";
}

/** The split bars read the shared extract snapshot relative to their own target sub-phase. */
function subPhaseIndex(prefix: "playwright" | "elasticsearch", meta: TaskMeta | null | undefined): number {
  const targetIdx = SPLIT_PHASE_ORDER.indexOf(prefix === "playwright" ? "transcription" : "es_indexing");
  const currentIdx = meta?.phase
    ? SPLIT_PHASE_ORDER.indexOf(meta.phase as (typeof SPLIT_PHASE_ORDER)[number])
    : -1;
  if (targetIdx < 0 || currentIdx < 0) return -1;
  return currentIdx;
}

export function subPhasePct(snapshot: PhaseSnapshot, prefix: "playwright" | "elasticsearch"): number {
  if (isTerminal(snapshot.state)) return 100;
  const meta = snapshot.meta;
  const idx = subPhaseIndex(prefix, meta);
  if (idx < 0) return 0;
  const targetIdx = SPLIT_PHASE_ORDER.indexOf(prefix === "playwright" ? "transcription" : "es_indexing");
  if (idx > targetIdx) return 100;
  if (idx < targetIdx) return 0;
  const pct = pctFromCounters(meta);
  if (pct != null) return pct;
  return 8;
}

export function subPhaseLabel(snapshot: PhaseSnapshot, prefix: "playwright" | "elasticsearch"): string {
  if (snapshot.state === "SUCCESS") return "Done";
  if (snapshot.state === "FAILURE" || snapshot.state === "ERROR") {
    return `Failed: ${snapshot.error?.slice(0, 60) ?? "see logs"}`;
  }
  if (snapshot.state === "PENDING") return "Queued";
  const idx = subPhaseIndex(prefix, snapshot.meta);
  if (idx < 0) return "Queued";
  const targetIdx = SPLIT_PHASE_ORDER.indexOf(prefix === "playwright" ? "transcription" : "es_indexing");
  if (idx > targetIdx) return "Done";
  if (idx < targetIdx) return "Queued";
  const meta = snapshot.meta;
  if (meta?.current != null && meta.total) return `${meta.current}/${meta.total}`;
  return "Running";
}

type Outcome = "success" | "partial" | "failure";

/** Honest outcome for the split bars on SUCCESS: the task reports Celery SUCCESS even when yt-dlp
 * fetched nothing, and "no captions" is an expected outcome — the ES denominator only counts videos
 * that were eligible to be indexed. */
export function extractOutcome(snapshot: PhaseSnapshot, prefix: "playwright" | "elasticsearch"): Outcome {
  const result = snapshot.result;
  const total = result?.total_videos ?? 0;
  const t = result?.transcriptions ?? null;
  if (prefix === "playwright") {
    const fetched = (t?.indexed ?? 0) + (t?.cached ?? 0);
    if (!total) return "success"; // nothing attempted — not a failure
    if ((t?.fetch_failed ?? 0) > 0) return "partial";
    if (fetched <= 0) return "failure";
    return fetched < total ? "partial" : "success";
  }
  const eligibleTotal = total - (t?.no_transcript ?? 0);
  if (eligibleTotal <= 0) return "success"; // nothing was ever eligible
  const indexed = (t?.indexed ?? 0) + (t?.cached ?? 0);
  if (indexed <= 0) return "failure";
  if ((t?.failed ?? 0) > 0 || indexed < eligibleTotal) return "partial";
  return "success";
}

export function extractBarState(snapshot: PhaseSnapshot, prefix: "playwright" | "elasticsearch"): string {
  if (snapshot.state !== "SUCCESS") return snapshot.state.toLowerCase();
  return extractOutcome(snapshot, prefix);
}

export function extractSuccessLabel(snapshot: PhaseSnapshot, prefix: "playwright" | "elasticsearch"): string {
  const outcome = extractOutcome(snapshot, prefix);
  if (outcome === "success") return "Done";
  const result = snapshot.result;
  const t = result?.transcriptions ?? null;
  const fetched = (t?.indexed ?? 0) + (t?.cached ?? 0);
  if (prefix === "playwright") {
    if (outcome === "failure") return "Failed: 0 fetched";
    return `${fetched}/${result?.total_videos ?? "?"} fetched`;
  }
  const eligibleTotal = (result?.total_videos ?? 0) - (t?.no_transcript ?? 0);
  if (outcome === "failure") return "Failed: 0 indexed";
  return `${fetched}/${eligibleTotal} indexed`;
}

/** Stream-aggregator percentage: Neo4j prefers piece counts (partitions counted individually) over
 * video counts, falling back to videos at run start before the totals are corrected. */
export function streamPct(snapshot: PhaseSnapshot, prefix: "qdrant" | "neo4j"): number {
  if (isTerminal(snapshot.state)) return 100;
  const meta = snapshot.meta;
  if (prefix === "neo4j" && meta?.piece_total && meta.piece_current != null) {
    return Math.max(2, Math.min(100, (meta.piece_current / meta.piece_total) * 100));
  }
  const pct = pctFromCounters(meta);
  if (pct != null) return pct;
  if (meta?.phase && snapshot.state === "PROGRESS") return 8;
  return 0;
}

function streamCounts(meta: TaskMeta | null | undefined): { done: number; failed: number } {
  return {
    failed: meta?.failed_ids?.length ?? 0,
    done: meta?.completed_ids?.length ?? 0,
  };
}

/** Stream-aggregator label. A SUCCESS with losses reads "Partial" with an explicit split — a green
 * "Done" on a half-failed run hid real data loss. */
export function streamLabel(snapshot: PhaseSnapshot, prefix: "qdrant" | "neo4j"): string {
  if (snapshot.state === "SUCCESS") {
    const { done, failed } = streamCounts(snapshot.meta);
    if (failed > 0) return `${done}/${done + failed} done · ${failed} failed`;
    return "Done";
  }
  if (snapshot.state === "FAILURE" || snapshot.state === "ERROR") {
    return `Failed: ${snapshot.error?.slice(0, 60) ?? "see logs"}`;
  }
  if (snapshot.state === "PENDING") return "Queued";
  const meta = snapshot.meta;
  if (prefix === "neo4j" && meta?.piece_total && meta.piece_current != null) {
    return `running · ${meta.piece_current}/${meta.piece_total} pieces`;
  }
  if (meta?.current != null && meta.total) return `running · ${meta.current}/${meta.total}`;
  if (meta?.phase) return meta.phase;
  return snapshot.state || "Running";
}

/** The stream bar's `data-state`: partial when finished but lossy. */
export function streamBarState(snapshot: PhaseSnapshot): string {
  if (snapshot.state === "SUCCESS") return streamCounts(snapshot.meta).failed > 0 ? "partial" : "success";
  return snapshot.state.toLowerCase();
}

/** One-line outcome summary shown under a SUCCESS bar. */
export function successHint(snapshot: PhaseSnapshot, prefix: PipelinePrefix): string | null {
  if (snapshot.state !== "SUCCESS" || !snapshot.result) return null;
  const result = snapshot.result;
  if (prefix === "playwright") {
    const t = result.transcriptions ?? null;
    const parts = [
      `${result.metadata?.indexed ?? 0} metadata`,
      `${(t?.indexed ?? 0) + (t?.cached ?? 0)} transcripts fetched`,
    ];
    const noTranscript = t?.no_transcript ?? 0;
    const fetchFailed = t?.fetch_failed ?? 0;
    if (noTranscript) parts.push(`${noTranscript} no transcript`);
    if (fetchFailed) parts.push(`${fetchFailed} fetch failed`);
    return parts.join(" · ");
  }
  if (prefix === "elasticsearch") {
    const t = result.transcriptions ?? null;
    const newIndexed = t?.indexed ?? 0;
    const cached = t?.cached ?? 0;
    const parts = [`${newIndexed + cached} transcripts in ES (${newIndexed} new · ${cached} cached)`];
    const indexFailed = t?.failed ?? 0;
    if (indexFailed) parts.push(`${indexFailed} index failed`);
    return parts.join(" · ");
  }
  if (prefix === "qdrant") {
    const parts = [
      `${result.total_transcripts} transcripts`,
      `${result.total_chunks} chunks`,
      `${result.points_upserted} points`,
    ];
    const unchanged = result.videos_unchanged;
    if (unchanged) parts.push(`${unchanged} unchanged`);
    if (result.failed_ids.length) parts.push(`${result.failed_ids.length} failed`);
    return parts.join(" · ");
  }
  const base = `${result.nodes_created} nodes · ${result.relationships_created} rels · ${result.entities_merged} merged`;
  const failed = result.failed_ids.length;
  if (failed) return `${result.completed_ids.length} videos ok · ${failed} failed · ${base}`;
  return base;
}

export type StoreStatus = "unavailable" | "failed" | "running" | "done" | "queued";

export const STORE_STATUS_LABELS: Record<StoreStatus, string> = {
  queued: "Queued",
  running: "Running",
  done: "Done",
  failed: "Failed",
  unavailable: "N/A",
};

/** Per-(video, store) status for the per-video table. `noTranscriptIds` marks videos Playwright found
 * have no captions — never eligible for the later stores, so those cells read "N/A" instead of a
 * misleading "Failed". The Playwright column passes undefined (it made the call). */
export function storeStatus(
  videoId: string,
  prefix: PipelinePrefix,
  snapshot: PhaseSnapshot,
  noTranscriptIds: ReadonlySet<string> | null,
): StoreStatus {
  if (noTranscriptIds?.has(videoId)) return "unavailable";
  const meta = snapshot.meta;
  if (meta?.failed_ids?.includes(videoId)) return "failed";
  if (prefix === "elasticsearch") {
    const fetched = meta?.completed_ids?.includes(videoId) ?? false;
    if (!fetched) {
      if (meta?.current_item?.id === videoId) return "running";
      if (snapshot.state === "SUCCESS") return "done";
      if (snapshot.state === "FAILURE" || snapshot.state === "REVOKED" || snapshot.state === "ERROR")
        return "failed";
      return "queued";
    }
    // Fetched — but committed to ES only once its chunk's bulk write lands.
    if (meta?.phase === "es_indexing" || snapshot.state === "SUCCESS") return "done";
    return "running";
  }
  if (meta?.current_item?.id === videoId) return "running";
  if (meta?.completed_ids?.includes(videoId)) return "done";
  // SUCCESS without completed_ids: everything dispatched must have been processed.
  if (snapshot.state === "SUCCESS") return "done";
  if (snapshot.state === "FAILURE" || snapshot.state === "REVOKED" || snapshot.state === "ERROR")
    return "failed";
  return "queued";
}

/** m:ss elapsed clock, driven by the persisted start time so reloads keep counting from the dispatch. */
export function fmtElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

/** Per-video table cell duration. */
export function cellDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return "—";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${r}`;
}

/** One-line wipe summary for the panel head. */
export function wipeSummaryLine(wipe: PipelineWipe): string {
  const s = wipe.summary;
  if (!s) return wipe.status ? `Wiped (${wipe.status}).` : "Wiped.";
  const es = s.es ?? { metadata_deleted: 0, transcripts_deleted: 0 };
  const qd = s.qdrant ?? { qdrant_deleted: 0 };
  const nj = s.neo4j ?? { documents_deleted: 0, videos_deleted: 0, entities_swept: 0 };
  return (
    `Wiped: ES ${es.metadata_deleted}m + ${es.transcripts_deleted}t · ` +
    `Qdrant ${qd.qdrant_deleted} · Neo4j ${nj.documents_deleted}d + ${nj.videos_deleted}v + ${nj.entities_swept}e`
  );
}

// ============================================================
// Library rows
// ============================================================

/** Meta line of a library row: channel · views · duration · likes · date. */
export function libraryMetaLine(v: LibraryItem): string {
  return [
    v.channel,
    v.view_count != null ? `${fmtCount(v.view_count)} views` : null,
    v.duration_string || durationNumberLabel(v.duration),
    v.like_count != null ? `${fmtCount(v.like_count)} likes` : null,
    fmtDate(v.upload_date),
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Stats line of a library row: transcript chars · languages · entities. */
export function libraryStatsLine(v: LibraryItem): string | null {
  return (
    [
      v.transcript_length ? `${fmtCount(v.transcript_length)} chars` : null,
      v.transcript_langs?.length ? v.transcript_langs.join(", ") : null,
      v.entity_count ? `${fmtCount(v.entity_count)} entities` : null,
    ]
      .filter(Boolean)
      .join(" · ") || null
  );
}

function durationNumberLabel(s: number | null | undefined): string | null {
  if (!s || s <= 0) return null;
  const total = Math.round(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

/** Row status → the token colour class carrying it (always paired with the status text itself). */
export function libraryStatusTone(status: string): string {
  if (status === "done") return "text-success-text";
  if (status === "partial") return "text-warning-text";
  if (status === "error") return "text-destructive-text";
  return "text-muted-foreground";
}

/** Watch-page URL for a library row, falling back to the canonical watch link. */
export function libraryUrl(v: LibraryItem): string {
  return v.webpage_url || `https://www.youtube.com/watch?v=${v.video_id}`;
}

/** Per-video client-side timing for the pipeline's duration column. Copy-on-write maps, so a tick
 * that stamps nothing returns the same object. */
export type Timing = { started: Map<string, number>; finished: Map<string, number> };

export type TimingRow = {
  videoId: string;
  anyRunning: boolean;
  allDone: boolean;
  anyFailed: boolean;
};

/** Stamp each row's start when it is first seen running and its finish when every cell is done or
 * one has failed. Runs inside the one-second ticker, never during render. */
export function nextTiming(prev: Timing, rows: ReadonlyArray<TimingRow>, stamp: number): Timing {
  let started = prev.started;
  let finished = prev.finished;
  for (const row of rows) {
    if (row.anyRunning && !started.has(row.videoId)) {
      started = new Map(started);
      started.set(row.videoId, stamp);
    }
    if ((row.allDone || row.anyFailed) && !finished.has(row.videoId)) {
      finished = new Map(finished);
      finished.set(row.videoId, stamp);
    }
  }
  return started === prev.started && finished === prev.finished ? prev : { started, finished };
}
