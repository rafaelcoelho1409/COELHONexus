import { describe, expect, it } from "vitest";

import {
  cellDuration,
  detectMode,
  dropRouted,
  extractBarState,
  extractOutcome,
  extractSuccessLabel,
  filterChips,
  fmtCount,
  fmtDate,
  fmtElapsed,
  fmtInt,
  idsMasterState,
  libraryMasterState,
  libraryMetaLine,
  libraryStatsLine,
  libraryStatusTone,
  libraryUrl,
  masterState,
  modeHint,
  nextTiming,
  type PhaseSnapshot,
  panelFilterCount,
  parseChannel,
  parseLangs,
  parsePlaylist,
  parseTextareaIds,
  phaseAnnouncement,
  phaseLabel,
  phasePct,
  pipelineOverall,
  rangeLabel,
  readSearchRequest,
  resultMeta,
  routeItems,
  type StoreStatus,
  selectRange,
  selectSnippet,
  sortVideoRows,
  statusWord,
  storeStatus,
  streamBarState,
  streamLabel,
  streamPct,
  subPhaseLabel,
  subPhasePct,
  successHint,
  summarizePaste,
  type Timing,
  unfurlState,
  usageModelRows,
  videoCounts,
  wipeSummaryLine,
} from "./domain";
import { type LibraryItem, libraryItemSchema, type TaskMeta, taskSnapshotSchema } from "./entities";
import { snippet } from "./test-fixtures";

/** Snapshots are built through the schema so partial fixtures get the tolerant defaults. */
const snap = (over: Record<string, unknown> = {}): PhaseSnapshot =>
  taskSnapshotSchema.parse({ state: "RUNNING", ...over });

describe("source parsers", () => {
  it("parses playlist URLs and bare playlist ids", () => {
    expect(parsePlaylist("https://www.youtube.com/playlist?list=PLabc123def"))?.toEqual({
      kind: "playlist",
      identity: "PLabc123def",
      display: "PLabc123def",
    });
    expect(parsePlaylist("https://youtube.com/watch?v=abc&list=PLabc123def&t=5"))?.toMatchObject({
      kind: "playlist",
      identity: "PLabc123def",
    });
    expect(parsePlaylist("https://youtube.com/@handle")).toBeNull();
  });

  it("parses channel handles and UC ids, rejects watch URLs", () => {
    expect(parseChannel("https://youtube.com/@some.handle"))?.toMatchObject({
      kind: "channel",
      identity: "@some.handle",
    });
    expect(parseChannel("https://youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx"))?.toMatchObject({
      kind: "channel",
      identity: "UCxxxxxxxxxxxxxxxxxxxxxx",
    });
    expect(parseChannel("@bare.handle"))?.toMatchObject({ kind: "channel", identity: "@bare.handle" });
    expect(parseChannel("https://youtube.com/watch?v=abc")).toBeNull();
  });

  it("parses video ids from watch URLs, youtu.be and bare ids", () => {
    expect(parseTextareaIds("https://youtube.com/watch?v=dQw4w9WgXcQ")).toEqual(["dQw4w9WgXcQ"]);
    expect(parseTextareaIds("https://youtu.be/dQw4w9WgXcQ")).toEqual(["dQw4w9WgXcQ"]);
    expect(parseTextareaIds("dQw4w9WgXcQ")).toEqual(["dQw4w9WgXcQ"]);
    // 10 chars, not 11 — rejected
    expect(parseTextareaIds("dQw4w9WgXc")).toEqual([]);
  });

  it("dedupes a mixed paste of CSV, newlines and URLs", () => {
    expect(
      parseTextareaIds("dQw4w9WgXcQ, https://youtu.be/dQw4w9WgXcQ\ngarbage\nabc123def45\nabc123def45"),
    ).toEqual(["dQw4w9WgXcQ", "abc123def45"]);
  });

  it("unfurlState: hidden when empty, parsed or invalid otherwise", () => {
    expect(unfurlState("", parseChannel)).toEqual({ state: "hidden" });
    expect(unfurlState("@handle", parseChannel)).toEqual({
      state: "parsed",
      parsed: { kind: "channel", identity: "@handle", display: "@handle" },
    });
    expect(unfurlState("not a youtube thing", parseChannel)).toEqual({
      state: "invalid",
      raw: "not a youtube thing",
    });
  });
});

describe("formatting", () => {
  it("abbreviates counts", () => {
    expect(fmtCount(0)).toBe("0");
    expect(fmtCount(999)).toBe("999");
    expect(fmtCount(1200)).toBe("1.2K");
    expect(fmtCount(2_500_000)).toBe("2.5M");
    expect(fmtCount(null)).toBe("");
  });

  it("formats upload dates", () => {
    expect(fmtDate("20240907")).toBe("2024-09-07");
    expect(fmtDate("202409")).toBe("");
    expect(fmtDate(null)).toBe("");
  });

  it("parses transcript languages", () => {
    expect(parseLangs("en, pt, es")).toEqual(["en", "pt", "es"]);
    expect(parseLangs("en pt")).toEqual(["en", "pt"]);
    expect(parseLangs("")).toBeNull();
  });

  it("builds the per-kind meta line", () => {
    const video = snippet({
      id: "v1",
      view_count: 1200,
      duration_string: "12:34",
      channel: "React",
      upload_date: "20240105",
    });
    expect(resultMeta(video)).toBe("1.2K views · 12:34 · React · Jan 5, 2024");
    const channel = snippet({ id: "c1", kind: "channel", video_count: 42 });
    expect(resultMeta(channel)).toBe("Channel · 42 videos");
  });
});

describe("readSearchRequest", () => {
  it("collects known fields, coerces numbers and dates, drops unknown ones", () => {
    const fd = new FormData();
    fd.append("query", "react hooks");
    fd.append("duration", "4 - 20 minutes");
    fd.append("min_views", "1000");
    fd.append("min_likes", "not-a-number");
    fd.append("date_after", "2024-01-05");
    fd.append("sort_by_date", "on");
    fd.append("mystery", "x");
    expect(readSearchRequest(fd, 25)).toEqual({
      query: "react hooks",
      duration: "4 - 20 minutes",
      min_views: 1000,
      date_after: "20240105",
      sort_by_date: true,
      max_results: 25,
    });
  });

  it("requires a query", () => {
    expect(() => readSearchRequest(new FormData(), 25)).toThrow();
  });
});

describe("the pipeline run as a whole", () => {
  it("names a bar's state in words", () => {
    expect(statusWord("success")).toBe("done");
    expect(statusWord("partial")).toBe("partial");
    expect(statusWord("failure")).toBe("failed");
    expect(statusWord("revoked")).toBe("failed");
    expect(statusWord("pending")).toBe("queued");
    expect(statusWord("progress")).toBe("running");
    expect(statusWord("started")).toBe("running");
    expect(statusWord("cancelled")).toBe("cancelled");
  });

  it("reads the four bars as one state", () => {
    expect(pipelineOverall(["pending", "pending", "pending", "pending"], false)).toBe("queued");
    expect(pipelineOverall(["success", "progress", "pending", "pending"], false)).toBe("running");
    expect(pipelineOverall(["success", "success", "pending", "pending"], false)).toBe("running"); // a phase waits behind a finished one
    expect(pipelineOverall(["success", "success", "success", "success"], false)).toBe("done");
    expect(pipelineOverall(["success", "partial", "success", "success"], false)).toBe("partial");
    expect(pipelineOverall(["success", "success", "success", "failure"], false)).toBe("partial");
    expect(pipelineOverall(["failure", "failure", "error", "revoked"], false)).toBe("failed");
    expect(pipelineOverall(["success", "progress", "pending", "pending"], true)).toBe("cancelled");
    expect(pipelineOverall(["success", "cancelled", "cancelled", "cancelled"], false)).toBe("cancelled");
  });

  it("announces the phases by their words only, so the line changes at a transition and not at every poll", () => {
    const phases = [
      { title: "Phase 1 · Playwright", barState: "progress" },
      { title: "Phase 2 · ElasticSearch", barState: "pending" },
    ];
    expect(phaseAnnouncement(phases, "running")).toBe(
      "Pipeline running. Phase 1 · Playwright: running. Phase 2 · ElasticSearch: queued.",
    );
  });

  it("lists failed videos first, then running, queued and done, keeping dispatch order within a group", () => {
    const cells = (a: StoreStatus, b: StoreStatus = a) => ({
      playwright: a,
      elasticsearch: a,
      qdrant: b,
      neo4j: b,
    });
    const rows = [
      { id: "done1", cells: cells("done") },
      { id: "queued1", cells: cells("queued") },
      { id: "running1", cells: cells("done", "running") },
      { id: "failed1", cells: cells("done", "failed") },
      { id: "done2", cells: cells("done", "unavailable") },
      { id: "failed2", cells: cells("failed") },
    ];
    expect(sortVideoRows(rows).map((row) => row.id)).toEqual([
      "failed1",
      "failed2",
      "running1",
      "queued1",
      "done1",
      "done2",
    ]);
    expect(videoCounts(rows)).toEqual({ failed: 2, running: 1, queued: 1, done: 2 });
  });

  it("sums a run's usage by model, busiest first, ignoring models that made no call", () => {
    const rows = usageModelRows({
      by_node: {
        v1: {
          by_model: {
            "vendor-a/model-x": { calls: 2, tokens_in: 100, tokens_out: 10, reasoning_tokens: 0 },
            "vendor-b/model-y": { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0 },
          },
        },
        v2: {
          by_model: {
            "vendor-a/model-x": { calls: 1, tokens_in: 50, tokens_out: 5, reasoning_tokens: 3 },
            "vendor-c/model-z": { calls: 5, tokens_in: 10, tokens_out: 1, reasoning_tokens: 0 },
          },
        },
      },
    });
    expect(rows.map((row) => row.model)).toEqual(["vendor-c/model-z", "vendor-a/model-x"]);
    expect(rows[1]).toEqual({
      model: "vendor-a/model-x",
      calls: 3,
      tokens_in: 150,
      tokens_out: 15,
      reasoning_tokens: 3,
    });
    expect(usageModelRows(null)).toEqual([]);
  });
});

describe("pasting into the Source tabs", () => {
  it("reads a pasted batch like the fetch does, and says what repeated and what was not understood", () => {
    const summary = summarizePaste(
      "aaaaaaaaaaa, https://youtu.be/bbbbbbbbbbb\nhttps://www.youtube.com/watch?v=aaaaaaaaaaa\n\nwhat is this\nccccccccccc,xyz",
    );
    expect(summary.ids).toEqual(["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]);
    expect(summary.duplicates).toBe(1);
    expect(summary.invalid).toEqual([
      { line: 4, text: "what is this" },
      { line: 5, text: "xyz" },
    ]);
    expect(parseTextareaIds("aaaaaaaaaaa\naaaaaaaaaaa")).toEqual(["aaaaaaaaaaa"]);
    expect(summarizePaste("")).toEqual({ ids: [], duplicates: 0, invalid: [] });
  });

  it("tells which tab a paste belongs to, playlist before channel before video", () => {
    expect(detectMode("https://www.youtube.com/playlist?list=PLabc123defghij")).toBe("playlist");
    expect(detectMode("https://www.youtube.com/watch?v=aaaaaaaaaaa&list=PLabc123defghij")).toBe("playlist");
    expect(detectMode("@anthropicai")).toBe("channel");
    expect(detectMode("https://youtu.be/aaaaaaaaaaa")).toBe("videos");
    expect(detectMode("hello there")).toBeNull();
    expect(detectMode("  ")).toBeNull();
  });

  it("suggests another tab only for a single token that belongs there", () => {
    expect(modeHint("@anthropicai", "playlist")).toBe("channel");
    expect(modeHint("@anthropicai", "channel")).toBeNull();
    expect(modeHint("aaaaaaaaaaa", "playlist")).toBe("videos");
    // a list of lines is the Videos tab's own input
    expect(modeHint("aaaaaaaaaaa\nbbbbbbbbbbb", "channel")).toBeNull();
    expect(modeHint("aaaaaaaaaaa, bbbbbbbbbbb", "channel")).toBeNull();
    expect(modeHint("gibberish", "videos")).toBeNull();
  });

  it("states the master checkbox over ids: none is not 'some'", () => {
    expect(idsMasterState([], new Set())).toBe("none");
    expect(idsMasterState(["a", "b"], new Set())).toBe("none");
    expect(idsMasterState(["a", "b"], new Set(["a"]))).toBe("some");
    expect(idsMasterState(["a", "b"], new Set(["a", "b", "z"]))).toBe("all");
  });
});

describe("filter chips and the range line", () => {
  it("names each filter the request carries, in the panel's order, and nothing else", () => {
    expect(filterChips({ query: "hooks", max_results: 25 })).toEqual([]);
    const chips = filterChips({
      query: "hooks",
      max_results: 25,
      sort_by_date: true,
      exclude_shorts: true,
      kind_filter: "channel",
      duration: "4 - 20 minutes",
      date_after: "20240105",
      date_before: "20250301",
      min_views: 10000,
      max_views: 2000000,
      min_likes: 250,
      title_contains: "hooks",
      channel_name: "React",
    });
    expect(chips.map((chip) => chip.key)).toEqual([
      "kind_filter",
      "duration",
      "date_after",
      "date_before",
      "min_views",
      "max_views",
      "min_likes",
      "title_contains",
      "channel_name",
      "sort_by_date",
      "exclude_shorts",
    ]);
    expect(chips.map((chip) => chip.label)).toEqual([
      "Kind: Channels",
      "Duration: 4 - 20 minutes",
      "Uploaded after 2024-01-05",
      "Uploaded before 2025-03-01",
      "Min views: 10,000",
      "Max views: 2,000,000",
      "Min likes: 250",
      "Title: hooks",
      "Channel: React",
      "Newest first",
      "No shorts",
    ]);
  });

  it("counts only the filters that live in the panel, not the quick ones", () => {
    const chips = filterChips({
      query: "x",
      kind_filter: "video",
      duration: "Over 20 minutes",
      sort_by_date: true,
      min_views: 5000,
      exclude_shorts: true,
    });
    expect(chips).toHaveLength(5);
    expect(panelFilterCount(chips)).toBe(2);
    expect(panelFilterCount([])).toBe(0);
  });

  it("does not make a chip of a zero floor", () => {
    expect(filterChips({ query: "x", min_views: 0, min_likes: 0 })).toEqual([]);
  });

  it("words where the page sits, with a plus while the source may hold more", () => {
    expect(rangeLabel(1, 25, 0, false)).toBe("No results");
    expect(rangeLabel(1, 25, 25, false)).toBe("1–25 of 25+");
    expect(rangeLabel(2, 25, 40, true)).toBe("26–40 of 40");
  });
});

describe("selection rules", () => {
  const videoA = snippet({ id: "vidA" });
  const videoB = snippet({ id: "vidB" });
  const chanA = snippet({ id: "chaA", kind: "channel", video_count: 3 });
  const chanB = snippet({ id: "chaB", kind: "channel", video_count: 4 });

  it("keeps at most one channel and one playlist, videos unrestricted", () => {
    let selected = selectSnippet(new Map(), chanA, true);
    selected = selectSnippet(selected, chanB, true);
    expect(Array.from(selected.keys())).toEqual(["chaB"]);
    selected = selectSnippet(selected, videoA, true);
    selected = selectSnippet(selected, videoB, true);
    expect(Array.from(selected.keys())).toEqual(["chaB", "vidA", "vidB"]);
  });

  it("selects and deselects shift ranges", () => {
    let selected = selectRange(new Map(), [videoA, videoB], true);
    expect(Array.from(selected.keys())).toEqual(["vidA", "vidB"]);
    selected = selectRange(selected, [videoA], false);
    expect(Array.from(selected.keys())).toEqual(["vidB"]);
  });

  it("routes per kind and drops routed rows", () => {
    let selected = selectRange(new Map(), [videoA, videoB, chanA], true);
    expect(routeItems("videos", selected)).toEqual(["vidA", "vidB"]);
    expect(routeItems("channel", selected)).toEqual(["chaA"]);
    expect(routeItems("playlist", selected)).toEqual([]);
    selected = dropRouted(selected, "videos", ["vidA", "vidB"]);
    expect(Array.from(selected.keys())).toEqual(["chaA"]);
  });

  it("routes a channel row by its channel_id when the id is empty", () => {
    const chanNoId = snippet({ id: "", kind: "channel", channel_id: "UCfallback" });
    expect(routeItems("channel", selectSnippet(new Map(), chanNoId, true))).toEqual(["UCfallback"]);
  });

  it("computes the master checkbox state", () => {
    expect(masterState([], new Map())).toBe("none");
    const selected = selectRange(new Map(), [videoA, videoB], true);
    expect(masterState([videoA], selected)).toBe("all");
    expect(masterState([videoA, videoB, chanA], selected)).toBe("some");
    expect(masterState([chanA], selected)).toBe("none");
  });
});

describe("pipeline bar maths", () => {
  const meta = {
    phase: "transcription",
    current: 5,
    total: 10,
  } satisfies TaskMeta;

  it("terminal snapshots read 100%", () => {
    for (const state of ["SUCCESS", "FAILURE", "REVOKED", "ERROR"]) {
      expect(phasePct(snap({ state }))).toBe(100);
      expect(subPhasePct(snap({ state }), "elasticsearch")).toBe(100);
      expect(streamPct(snap({ state }), "neo4j")).toBe(100);
    }
  });

  it("progress counters drive the percentage", () => {
    expect(phasePct(snap({ meta, state: "PROGRESS" }))).toBe(50);
    expect(phasePct(snap({ meta: { ...meta, current: 0, total: 0 }, state: "PROGRESS" }))).toBe(8);
    expect(
      phasePct(snap({ meta: { phase: "metadata", current: null, total: null }, state: "PROGRESS" })),
    ).toBe(8);
  });

  it("labels mirror the legacy phase text", () => {
    expect(phaseLabel(snap({ state: "SUCCESS" }))).toBe("Done");
    expect(phaseLabel(snap({ state: "PENDING" }))).toBe("Queued");
    expect(phaseLabel(snap({ state: "PROGRESS", meta }))).toBe("transcription · 5/10");
    expect(phaseLabel(snap({ state: "PROGRESS", meta: { phase: "metadata" } }))).toBe("metadata");
    expect(phaseLabel(snap({ state: "FAILURE", error: "boom" }))).toBe("Failed: boom");
    expect(phaseLabel(snap({ state: "ERROR" }))).toBe("Failed: see logs");
  });

  it("split bars read the shared extract snapshot against their own target", () => {
    const before = snap({ meta: { ...meta, phase: "metadata" }, state: "PROGRESS" });
    expect(subPhasePct(before, "playwright")).toBe(0);
    expect(subPhasePct(before, "elasticsearch")).toBe(0);
    expect(subPhaseLabel(before, "elasticsearch")).toBe("Queued");
    const at = snap({ meta, state: "PROGRESS" });
    expect(subPhasePct(at, "playwright")).toBe(50);
    expect(subPhaseLabel(at, "playwright")).toBe("5/10");
    const past = snap({ meta: { ...meta, phase: "es_indexing" }, state: "PROGRESS" });
    expect(subPhasePct(past, "playwright")).toBe(100);
    expect(subPhaseLabel(past, "playwright")).toBe("Done");
    expect(subPhaseLabel(past, "elasticsearch")).toBe("5/10");
  });

  it("honest extract outcomes", () => {
    const empty = snap({ state: "SUCCESS", result: { total_videos: 0 } });
    expect(extractOutcome(empty, "playwright")).toBe("success");
    expect(extractOutcome(empty, "elasticsearch")).toBe("success");
    const allFetched = snap({
      state: "SUCCESS",
      result: { total_videos: 10, transcriptions: { indexed: 9, cached: 1 } },
    });
    expect(extractOutcome(allFetched, "playwright")).toBe("success");
    const noCaptions = snap({
      state: "SUCCESS",
      result: {
        total_videos: 10,
        transcriptions: { indexed: 9, no_transcript: 1 },
      },
    });
    expect(extractOutcome(noCaptions, "playwright")).toBe("partial"); // 9 of 10 fetched
    expect(extractOutcome(noCaptions, "elasticsearch")).toBe("success"); // 9 of 9 eligible
    const fetchFailed = snap({
      state: "SUCCESS",
      result: { total_videos: 10, transcriptions: { indexed: 9, fetch_failed: 1 } },
    });
    expect(extractOutcome(fetchFailed, "playwright")).toBe("partial");
    const nothing = snap({ state: "SUCCESS", result: { total_videos: 10 } });
    expect(extractOutcome(nothing, "playwright")).toBe("failure");
    expect(extractOutcome(nothing, "elasticsearch")).toBe("failure");
    const someIndexFailed = snap({
      state: "SUCCESS",
      result: { total_videos: 10, transcriptions: { indexed: 9, failed: 1 } },
    });
    expect(extractOutcome(someIndexFailed, "elasticsearch")).toBe("partial");
    expect(extractBarState(snap({ state: "RUNNING" }), "playwright")).toBe("running");
    expect(extractBarState(nothing, "playwright")).toBe("failure");
    expect(extractSuccessLabel(nothing, "playwright")).toBe("Failed: 0 fetched");
    expect(extractSuccessLabel(noCaptions, "playwright")).toBe("9/10 fetched");
    expect(extractSuccessLabel(nothing, "elasticsearch")).toBe("Failed: 0 indexed");
    expect(extractSuccessLabel(someIndexFailed, "elasticsearch")).toBe("9/10 indexed");
  });
});

describe("stream bars", () => {
  it("neo4j prefers piece counters", () => {
    const running = (meta: TaskMeta, state = "PROGRESS"): PhaseSnapshot => ({ state, meta });
    expect(streamPct(running({ piece_current: 2, piece_total: 5 }), "neo4j")).toBe(40);
    expect(streamLabel(running({ piece_current: 2, piece_total: 5 }), "neo4j")).toBe("running · 2/5 pieces");
    expect(streamPct(running({ current: 3, total: 4 }), "qdrant")).toBe(75);
    expect(streamLabel(running({ current: 3, total: 4 }), "qdrant")).toBe("running · 3/4");
    expect(streamLabel(running({ phase: "embedding" }), "qdrant")).toBe("embedding");
  });

  it("lossy success reads partial with an explicit split", () => {
    const lossy: PhaseSnapshot = {
      state: "SUCCESS",
      meta: { completed_ids: ["a", "b", "c"], failed_ids: ["d", "e"] },
    };
    expect(streamBarState(lossy)).toBe("partial");
    expect(streamLabel(lossy, "qdrant")).toBe("3/5 done · 2 failed");
    const clean: PhaseSnapshot = { state: "SUCCESS", meta: { completed_ids: ["a"] } };
    expect(streamBarState(clean)).toBe("success");
    expect(streamLabel(clean, "neo4j")).toBe("Done");
    expect(streamBarState({ state: "RUNNING" })).toBe("running");
  });
});

describe("success hints", () => {
  it("playwright line counts no-transcript and failures only when present", () => {
    const full = snap({
      state: "SUCCESS",
      result: {
        total_videos: 10,
        metadata: { indexed: 10 },
        transcriptions: { indexed: 6, cached: 2, no_transcript: 1, fetch_failed: 1, failed: 1 },
      },
    });
    expect(successHint(full, "playwright")).toBe(
      "10 metadata · 8 transcripts fetched · 1 no transcript · 1 fetch failed",
    );
    expect(successHint(full, "elasticsearch")).toBe(
      "8 transcripts in ES (6 new · 2 cached) · 1 index failed",
    );
  });

  it("qdrant and neo4j lines", () => {
    const qdrant = snap({
      state: "SUCCESS",
      result: {
        total_transcripts: 5,
        total_chunks: 120,
        points_upserted: 118,
        videos_unchanged: 2,
        failed_ids: ["z"],
      },
    });
    expect(successHint(qdrant, "qdrant")).toBe(
      "5 transcripts · 120 chunks · 118 points · 2 unchanged · 1 failed",
    );
    const neo4j = snap({
      state: "SUCCESS",
      result: {
        total_transcripts: 5,
        total_chunks: 120,
        points_upserted: 118,
        nodes_created: 30,
        relationships_created: 12,
        entities_merged: 4,
        completed_ids: ["a", "b"],
        failed_ids: ["z"],
      },
    });
    expect(successHint(neo4j, "neo4j")).toBe("2 videos ok · 1 failed · 30 nodes · 12 rels · 4 merged");
    expect(successHint({ state: "RUNNING" }, "neo4j")).toBeNull();
  });
});

describe("store cell status", () => {
  const queued: PhaseSnapshot = { state: "PENDING" };
  const running: PhaseSnapshot = {
    state: "PROGRESS",
    meta: {
      completed_ids: ["done1"],
      failed_ids: ["bad1"],
      current_item: { id: "busy1", title: "Busy" },
    },
  };
  const done: PhaseSnapshot = { state: "SUCCESS" };

  it("no-transcript videos read N/A everywhere except the playwright column", () => {
    const none = new Set(["na1"]);
    expect(storeStatus("na1", "elasticsearch", running, none)).toBe("unavailable");
    expect(storeStatus("na1", "neo4j", running, none)).toBe("unavailable");
    expect(storeStatus("busy1", "playwright", running, null)).toBe("running");
  });

  it("failed, running, done, queued in legacy priority order", () => {
    expect(storeStatus("bad1", "qdrant", running, null)).toBe("failed");
    expect(storeStatus("busy1", "qdrant", running, null)).toBe("running");
    expect(storeStatus("done1", "qdrant", running, null)).toBe("done");
    expect(storeStatus("idle1", "qdrant", running, null)).toBe("queued");
    expect(storeStatus("idle1", "qdrant", queued, null)).toBe("queued");
    expect(storeStatus("idle1", "qdrant", done, null)).toBe("done");
    expect(storeStatus("idle1", "qdrant", { state: "FAILURE" }, null)).toBe("failed");
  });

  it("elasticsearch commits fetched videos only at es_indexing", () => {
    const phase = (p: string): PhaseSnapshot => ({
      state: "PROGRESS",
      meta: { phase: p, completed_ids: ["fetched"] },
    });
    expect(storeStatus("fetched", "elasticsearch", phase("transcription"), null)).toBe("running");
    expect(storeStatus("fetched", "elasticsearch", phase("es_indexing"), null)).toBe("done");
    expect(storeStatus("idle", "elasticsearch", phase("transcription"), null)).toBe("queued");
  });
});

describe("misc pipeline helpers", () => {
  it("elapsed clock and cell durations", () => {
    expect(fmtElapsed(0)).toBe("0:00");
    expect(fmtElapsed(65)).toBe("1:05");
    expect(fmtElapsed(-3)).toBe("");
    expect(cellDuration(1500)).toBe("1.5s");
    expect(cellDuration(65_000)).toBe("1:05");
    expect(cellDuration(0)).toBe("—");
  });

  it("wipe summary line", () => {
    expect(
      wipeSummaryLine({
        status: "wiped",
        summary: {
          es: { metadata_deleted: 2, transcripts_deleted: 5 },
          qdrant: { qdrant_deleted: 12 },
          neo4j: { documents_deleted: 30, videos_deleted: 2, entities_swept: 8 },
        },
      }),
    ).toBe("Wiped: ES 2m + 5t · Qdrant 12 · Neo4j 30d + 2v + 8e");
    expect(wipeSummaryLine({ status: "wiped", summary: null })).toBe("Wiped (wiped).");
  });

  it("integer formatting", () => {
    expect(fmtInt(1234567)).toBe("1,234,567");
    expect(fmtInt(12)).toBe("12");
  });

  it("nextTiming stamps starts and finishes copy-on-write", () => {
    const initial: Timing = { started: new Map(), finished: new Map() };
    const rows = [
      { videoId: "a", anyRunning: true, allDone: false, anyFailed: false },
      { videoId: "b", anyRunning: false, allDone: true, anyFailed: false },
    ];
    const after = nextTiming(initial, rows, 100);
    expect(after.started.get("a")).toBe(100);
    expect(after.finished.get("b")).toBe(100);
    expect(nextTiming(after, rows, 200)).toBe(after);
    const doneRows = [{ videoId: "a", anyRunning: false, allDone: true, anyFailed: false }];
    const finished = nextTiming(after, doneRows, 200);
    expect(finished.finished.get("a")).toBe(200);
  });
});

describe("library row helpers", () => {
  const item = (over: Record<string, unknown>): LibraryItem =>
    libraryItemSchema.parse({
      video_id: "vid1",
      title: "A talk",
      status: "done",
      ...over,
    });

  it("meta line joins present fields", () => {
    expect(
      libraryMetaLine(
        item({ channel: "Chan", view_count: 1500, duration: 95, like_count: 42, upload_date: "20240105" }),
      ),
    ).toBe("Chan · 1.5K views · 1:35 · 42 likes · 2024-01-05");
    expect(libraryMetaLine(item({}))).toBe("");
  });

  it("stats line and tone and url", () => {
    expect(libraryStatsLine(item({ transcript_length: 9000, transcript_langs: ["en", "de"] }))).toBe(
      "9.0K chars · en, de",
    );
    expect(libraryStatsLine(item({}))).toBeNull();
    expect(libraryStatusTone("done")).toBe("text-success-text");
    expect(libraryStatusTone("partial")).toBe("text-warning-text");
    expect(libraryStatusTone("error")).toBe("text-destructive-text");
    expect(libraryStatusTone("processing")).toBe("text-muted-foreground");
    expect(libraryUrl(item({ webpage_url: "https://x" }))).toBe("https://x");
    expect(libraryUrl(item({}))).toBe("https://www.youtube.com/watch?v=vid1");
  });

  it("master state over flat rows", () => {
    const rows = [item({}), item({ video_id: "vid2" })];
    expect(libraryMasterState(rows, new Set())).toBe("none");
    expect(libraryMasterState(rows, new Set(["vid1"]))).toBe("some");
    expect(libraryMasterState(rows, new Set(["vid1", "vid2"]))).toBe("all");
  });
});
