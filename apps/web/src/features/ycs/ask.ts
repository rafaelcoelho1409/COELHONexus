/** YouTube Content Search — Ask stage domain: the SSE event reducer, the stage-pill state machine,
 * citation tokenisation, duration/scope/usage formatting and the refresh-recovery helpers. Pure — no
 * React, no fetch; everything here is unit-testable in isolation. */

import type { AskCitation, AskEvent, AskThinkingState, HistoryRow, ThreadSummary } from "./entities";
import { ASK_STAGES, THREAD_LABEL_MAX } from "./params";

export type AskStageId = "retrieve" | "grade" | "generate" | "verify";
export type AskStageStatus = "queued" | "active" | "done";
export type AskStageView = { status: AskStageStatus; action: string };
export type AskDeepCard = { question: string; status: "queued" | "done"; answer: string };
export type AskDeep = { banner: string | null; planText: string; cards: AskDeepCard[] };
export type UsageTotal = { calls: number; tokens_in: number; tokens_out: number };

/** One conversation turn — the same shape whether it streams in live, hydrates from history or is
 * recovered mid-stream after a refresh. */
export type AskTurn = {
  question: string;
  mode: string;
  stages: Record<AskStageId, AskStageView>;
  deep: AskDeep | null;
  answer: string;
  citations: AskCitation[];
  usage: UsageTotal | null;
  durationMs: number | null;
  confidence: number | null;
  followups: string[];
  /** Postgres row id (from `_meta` or the history row) — Stop and Regenerate target it. */
  turnId: number | null;
  streaming: boolean;
  /** Restored from a refresh mid-run; keeps the Thinking accordion open and the poll loop alive. */
  recovered: boolean;
  stalled: boolean;
  error: string | null;
  createdAt: string;
  /** Whether the Thinking accordion renders at all (a bare legacy row with no state and no answer doesn't). */
  hasThinking: boolean;
};

const STAGE_INDEX: Record<AskStageId, number> = (() => {
  const map = {} as Record<AskStageId, number>;
  for (const [i, stage] of ASK_STAGES.entries()) map[stage.id as AskStageId] = i;
  return map;
})();

const QUEUED: AskStageView = { status: "queued", action: "" };
const ACTIVE: AskStageView = { status: "active", action: "" };
const DONE: AskStageView = { status: "done", action: "" };

function freshStages(): Record<AskStageId, AskStageView> {
  const stages = {} as Record<AskStageId, AskStageView>;
  for (const stage of ASK_STAGES) stages[stage.id as AskStageId] = QUEUED;
  return stages;
}

export function newLiveTurn(question: string): AskTurn {
  return {
    question,
    mode: "",
    stages: freshStages(),
    deep: null,
    answer: "",
    citations: [],
    usage: null,
    durationMs: null,
    confidence: null,
    followups: [],
    turnId: null,
    streaming: true,
    recovered: false,
    stalled: false,
    error: null,
    createdAt: "",
    hasThinking: true,
  };
}

/** `active` promotes every earlier stage to `done`; later ones stay `queued` (the legacy markStage). */
function stageMark(
  stages: Record<AskStageId, AskStageView>,
  stage: AskStageId,
  state: AskStageStatus,
): Record<AskStageId, AskStageView> {
  const idx = STAGE_INDEX[stage];
  const next = {} as Record<AskStageId, AskStageView>;
  for (const [id, view] of Object.entries(stages) as [AskStageId, AskStageView][]) {
    if (state === "active" && STAGE_INDEX[id] < idx) next[id] = DONE;
    else if (id === stage) next[id] = state === "active" ? ACTIVE : DONE;
    else next[id] = view;
  }
  return next;
}

/** Drops the stale action labels of every other stage (only the active one keeps its live verb). */
function withAction(
  stages: Record<AskStageId, AskStageView>,
  stage: AskStageId,
  action: string,
): Record<AskStageId, AskStageView> {
  const next = {} as Record<AskStageId, AskStageView>;
  for (const [id, view] of Object.entries(stages) as [AskStageId, AskStageView][]) {
    next[id] = id === stage ? { ...view, action } : { ...view, action: "" };
  }
  return next;
}

/* Keys match the exact node names emitted by the LangGraph backend. STAGE_MAP routes a node to its
 * stage pill; STAGE_ORDER (via STAGE_INDEX) advances prior pills to done when a later one activates. */
const STAGE_MAP: Partial<Record<string, AskStageId>> = {
  contextualize: "retrieve",
  classify_query: "retrieve",
  direct_answer: "generate",
  run_standard: "generate",
  plan_research: "retrieve",
  run_subagent: "retrieve",
  synthesize: "generate",
  critic: "verify",
  retrieve: "retrieve",
  grade_documents: "grade",
  generate: "generate",
  check_hallucination: "verify",
  rewrite_query: "retrieve",
  format_citations: "verify",
};

const NODE_ACTION_TEXT: Partial<Record<string, string>> = {
  contextualize: "Resolving prior context",
  classify_query: "Classifying intent",
  retrieve: "Searching transcripts",
  rewrite_query: "Refining query",
  grade_documents: "Grading documents",
  plan_research: "Planning sub-questions",
  run_subagent: "Researching sub-question",
  direct_answer: "Composing answer",
  run_standard: "Running standard pipeline",
  generate: "Writing answer",
  synthesize: "Synthesizing findings",
  check_hallucination: "Verifying grounding",
  format_citations: "Formatting citations",
  critic: "Assessing confidence",
};

function deepPlanBanner(count: number): string {
  return `Research plan — ${count} sub-question${count === 1 ? "" : "s"}`;
}

function deepConfidenceBanner(confidence: number): string {
  return `Critic confidence ${(confidence * 100).toFixed(0)}%`;
}

function deepSynthBanner(count: number): string {
  return `Synthesizing — merging ${count} sub-answer${count === 1 ? "" : "s"}…`;
}

function flipCard(cards: AskDeepCard[], question: string, answer: string): AskDeepCard[] {
  return cards.map((card) =>
    card.question === question ? { ...card, status: "done" as const, answer } : card,
  );
}

function withSubResult(turn: AskTurn, question: string, answer: string): AskTurn {
  if (!turn.deep || !question) return turn;
  if (!turn.deep.cards.some((card) => card.question === question)) return turn;
  return { ...turn, deep: { ...turn.deep, cards: flipCard(turn.deep.cards, question, answer) } };
}

/** Fold one SSE node update into the turn. Mirrors the legacy applyUpdate order: stage pills first,
 * then mode, documents, generation, confidence, citations, then the Deep events. */
export function applyAskEvent(turn: AskTurn, event: AskEvent): AskTurn {
  if (event.node === "_meta") return { ...turn, turnId: event.turn_id ?? turn.turnId };
  if (event.node === "end") {
    return {
      ...turn,
      streaming: false,
      stages: stageMark(turn.stages, "verify", "done"),
      followups: (turn.deep?.cards ?? []).map((card) => card.question),
      durationMs: event.duration_ms ?? turn.durationMs,
      usage: event.usage?.total ?? turn.usage,
    };
  }
  if (event.node === "error") {
    return { ...turn, streaming: false, error: event.error ?? "stream error" };
  }

  let next = turn;
  const mapped = STAGE_MAP[event.node];
  if (mapped) {
    next = {
      ...next,
      stages: withAction(
        stageMark(next.stages, mapped, "active"),
        mapped,
        NODE_ACTION_TEXT[event.node] ?? "",
      ),
    };
  }
  if (event.mode) next = { ...next, mode: event.mode };
  if (event.documents != null && event.document_count != null) {
    next = { ...next, stages: stageMark(next.stages, "retrieve", "done") };
  }
  if (event.generation) {
    next = { ...next, stages: stageMark(next.stages, "generate", "active"), answer: event.generation };
  }
  if (event.confidence_score != null) {
    next = {
      ...next,
      stages: stageMark(next.stages, "verify", "active"),
      confidence: event.confidence_score,
    };
    if (next.deep) {
      next = { ...next, deep: { ...next.deep, banner: deepConfidenceBanner(event.confidence_score) } };
    }
  }
  if (event.citations?.length) next = { ...next, citations: [...event.citations] };
  if (event.sub_questions?.length) {
    next = {
      ...next,
      deep: {
        banner: deepPlanBanner(event.sub_questions.length),
        planText: event.research_plan ?? "",
        cards: event.sub_questions.map((question) => ({ question, status: "queued", answer: "" })),
      },
    };
  }
  if (event.sub_results?.length) {
    for (const result of event.sub_results) {
      next = withSubResult(next, result.sub_question, result.answer);
    }
  } else if (event.latest_sub_question) {
    next = withSubResult(
      next,
      event.latest_sub_question,
      event.latest_sub_answer ?? event.latest_sub_answer_preview ?? "",
    );
  }
  if (event.node === "synthesize" && next.deep) {
    next = { ...next, deep: { ...next.deep, banner: deepSynthBanner(next.deep.cards.length) } };
  }
  return next;
}

/** Humanize a millisecond duration for the per-turn "Answered in …" badge. Buckets: <1s → "<1s",
 * <60s → "12.4s", <60m → "2m 14s", else "1h 03m". Empty on invalid input so the badge stays hidden. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return "<1s";
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  if (m < 60) return `${m}m ${String(rs).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  const rm = m - h * 60;
  return `${h}h ${String(rm).padStart(2, "0")}m`;
}

export function formatTurnDuration(ms: number | null | undefined): string {
  const text = formatDuration(ms);
  return text ? `in ${text}` : "";
}

export function fmtTokens(n: number | null | undefined): string {
  return Number.isFinite(Number(n)) ? Number(n ?? 0).toLocaleString("en-US") : "0";
}

/** Per-turn usage chip: "1,234 in · 5,678 out", or "" when zero calls (the chip renders disabled then). */
export function usageLabel(usage: UsageTotal | null): string {
  if (!usage || !usage.calls) return "";
  return `${fmtTokens(usage.tokens_in)} in · ${fmtTokens(usage.tokens_out)} out`;
}

/** "2h ago" relative timestamp from an ISO string; falls back to the raw string when parsing fails. */
export function relTime(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toISOString().slice(0, 10);
}

/** Friendly labels for the retriever arms; falls back to the raw value for any new arm. */
const ARM_LABEL: Record<string, string> = {
  qdrant_hybrid: "vector",
  neo4j_graph: "graph",
  elasticsearch: "full-text",
};

export function armLabel(source: string | null | undefined): string {
  if (!source) return "";
  return ARM_LABEL[source] ?? source;
}

/** `[Video: title]` → `⟦CITE-N⟧`, title-aware with a substring fallback because LLMs sometimes
 * paraphrase. The Unicode brackets have no markdown semantics, so they pass a markdown renderer
 * verbatim — unlike the old `__CITE_N__` placeholder, which CommonMark rewrote into bold. */
export function citeTokens(text: string, citations: readonly AskCitation[]): string {
  if (!citations.length) return text;
  const index = new Map<string, number>();
  citations.forEach((c, i) => {
    const title = c.title.trim().toLowerCase();
    if (title) index.set(title, i + 1);
  });
  return text.replace(/\[Video:\s*([^\]]+?)\]/gi, (match, raw: string) => {
    const key = raw.trim().toLowerCase();
    let n = index.get(key);
    if (!n) {
      for (const [k, i] of index) {
        if (k.includes(key) || key.includes(k)) {
          n = i;
          break;
        }
      }
    }
    return n ? `⟦CITE-${n}⟧` : match;
  });
}

/** 12-char id for a fresh thread candidate; the Postgres row appears with the first message. */
export function shortId(): string {
  const uuid = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `t-${Date.now()}-1`;
  return uuid.replaceAll("-", "").slice(0, 12);
}

/** "New" for a candidate thread that has no saved turns yet; trimmed otherwise. */
export function threadLabel(id: string): string {
  if (!id) return "New";
  return id.length > THREAD_LABEL_MAX ? `${id.slice(0, THREAD_LABEL_MAX)}…` : id;
}

/** Picker row title: first question, or the placeholder when a thread somehow has none. */
export function threadPreview(firstQuestion: string): string {
  return firstQuestion ? firstQuestion.slice(0, 64) : "(no title)";
}

/** Picker row meta: "2 turns · 3h ago". */
export function threadRowMeta(row: Pick<ThreadSummary, "turn_count" | "last_seen">): string {
  const turns = `${row.turn_count} turn${row.turn_count === 1 ? "" : "s"}`;
  return `${turns} · ${relTime(row.last_seen)}`;
}

/** Scope trigger label: "All channels" / one name / up to two names / "first +N more". */
export function scopeLabel(
  selected: readonly string[],
  nameFor: (id: string) => string,
  loaded: boolean,
): string {
  const n = selected.length;
  if (n === 0) return "All channels";
  if (!loaded) return n === 1 ? "Loading channel…" : `${n} channels (loading…)`;
  const first = selected[0];
  if (n === 1 && first) return nameFor(first);
  const names = selected.map(nameFor);
  const head = names[0] ?? "";
  return n <= 2 ? names.join(", ") : `${head} +${n - 1} more`;
}

/** True when a persisted snapshot represents a mid-stream pipeline (no answer, some stage active). */
export function isTurnInProgress(state: AskThinkingState | null | undefined, answer: string): boolean {
  if (answer) return false;
  if (!state) return false;
  return Object.values(state.stages ?? {}).some((stage) => stage?.status === "active");
}

/** Cheap fingerprint of an in-progress snapshot for the stall detector: the backend's `_seq`
 * heartbeat, the answer length, stage statuses + actions and the per-sub-question statuses. */
export function snapshotKey(row: HistoryRow | null | undefined): string {
  if (!row) return "";
  const ts = row.thinking_state;
  const stages = ts?.stages ?? {};
  const stageKey = ASK_STAGES.map((s) => {
    const entry = stages[s.id];
    return `${s.id}:${entry?.status ?? ""}:${entry?.action ?? ""}`;
  }).join("|");
  const subKey = (ts?.deep?.sub_questions ?? []).map((q) => q.status).join(",");
  return `seq:${ts?._seq ?? ""}|${row.answer.length}|${stageKey}|${subKey}`;
}

export const STALLED_SUBTITLE = "Stalled — pipeline lost (likely backend restart)";

/** Stage pills for a history turn. No persisted state + an answer = all done (the legacy default);
 * a mid-stream snapshot keeps its active/queued pills as persisted. */
export function stagesFromThinking(
  state: AskThinkingState | null | undefined,
): Record<AskStageId, AskStageView> {
  const stages = freshStages();
  if (!state) {
    for (const stage of ASK_STAGES) stages[stage.id as AskStageId] = DONE;
    return stages;
  }
  for (const stage of ASK_STAGES) {
    const entry = state.stages?.[stage.id];
    stages[stage.id as AskStageId] =
      entry?.status === "active"
        ? { status: "active", action: entry.action ?? "" }
        : entry?.status === "done"
          ? DONE
          : QUEUED;
  }
  return stages;
}

/** Build a turn from one history row (hydrate + recovery polls). */
export function turnFromHistory(row: HistoryRow): AskTurn {
  const ts = row.thinking_state;
  const deep = ts?.deep ?? null;
  const hasDeep = Boolean(deep && deep.sub_questions.length);
  return {
    question: row.question,
    mode: row.mode,
    stages: stagesFromThinking(ts),
    deep: hasDeep
      ? {
          banner:
            deep?.confidence_score != null
              ? deepConfidenceBanner(deep.confidence_score)
              : deepPlanBanner(deep?.sub_questions.length ?? 0),
          planText: deep?.research_plan ?? "",
          cards: (deep?.sub_questions ?? []).map((sq) => ({
            question: sq.question,
            status: sq.status === "done" ? ("done" as const) : ("queued" as const),
            answer: sq.answer ?? sq.answer_preview ?? "",
          })),
        }
      : null,
    answer: row.answer,
    citations: ts?.citations ?? [],
    usage: ts?.usage?.total ?? null,
    durationMs: ts?.duration_ms ?? null,
    confidence: deep?.confidence_score ?? null,
    followups: [],
    turnId: row.id ?? null,
    streaming: false,
    recovered: isTurnInProgress(ts, row.answer),
    stalled: false,
    error: null,
    createdAt: row.created_at,
    hasThinking: Boolean(row.answer) || Boolean(ts),
  };
}

/** The status line under the composer (one line, aria-live). */
export type AskStatus = { text: string; tone: "" | "running" | "error" };

/** Derive the next status line from one SSE node update (mirrors the legacy applyUpdate writes). */
export function statusFromEvent(prev: AskStatus, event: AskEvent): AskStatus {
  if (event.node === "end") return { text: "Done.", tone: "" };
  if (event.node === "error") return { text: "Stream error", tone: "error" };
  if (event.confidence_score != null) {
    return { text: `Confidence: ${(event.confidence_score * 100).toFixed(0)}%`, tone: "running" };
  }
  if (event.documents != null && event.document_count != null) {
    return { text: `Retrieved ${event.document_count} document(s).`, tone: "running" };
  }
  if (event.mode) return { text: `Mode: ${event.mode}`, tone: "running" };
  return prev;
}

/** Shape an SSE-reader failure into one readable line — the reader throws `StreamHttpError` (duck-typed:
 * `status` + up to 500 chars of the response body) or a plain fetch error. */
export function streamErrorText(error: unknown): string {
  const candidate = error as { status?: unknown; body?: unknown; message?: unknown } | null;
  if (candidate && typeof candidate === "object" && typeof candidate.body === "string" && candidate.body) {
    try {
      const parsed = JSON.parse(candidate.body) as { detail?: unknown };
      if (typeof parsed.detail === "string") return parsed.detail;
      if (Array.isArray(parsed.detail)) {
        const lines = parsed.detail
          .map((item) =>
            typeof item === "object" && item && "msg" in item ? String((item as { msg: unknown }).msg) : "",
          )
          .filter(Boolean);
        if (lines.length) return lines.join("; ");
      }
    } catch {
      // not JSON — fall through to the message
    }
  }
  return typeof candidate?.message === "string" && candidate.message ? candidate.message : "request failed";
}
