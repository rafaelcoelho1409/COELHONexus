import { describe, expect, it } from "vitest";

import type { StageEvent } from "../entities";
import { plannerLiveText, synthLiveText } from "./live-text";

const event = (fields: Record<string, unknown>): StageEvent => fields as StageEvent;

describe("plannerLiveText", () => {
  it("describes each node's start and done events", () => {
    expect(plannerLiveText(event({ step: "corpus_load", kind: "start" }))).toBe("· reading manifest…");
    expect(
      plannerLiveText(event({ step: "corpus_load", kind: "done", files: 1200, total_bytes: 20480 })),
    ).toBe("✓ 1,200 files, 20 KB");
    expect(
      plannerLiveText(
        event({ step: "off_topic", kind: "llm_progress", judged: 5, total: 50, llm_keep: 4, llm_drop: 1 }),
      ),
    ).toBe("· judged 5 / 50 (keep 4, drop 1)");
    expect(
      plannerLiveText(
        event({ step: "doc_distill", kind: "done", n_distilled: 8, n_failed: 2, cache_hit: true }),
      ),
    ).toBe("✓ 8 distilled · 2 failed (cache hit)");
    expect(
      plannerLiveText(
        event({ step: "chapter_propose", kind: "done", n_proposals: 5, titles: ["A", "B", "C", "D", 7] }),
      ),
    ).toBe("✓ 5 chapters proposed: A, B, C");
    expect(
      plannerLiveText(event({ step: "chapter_select", kind: "done", n_chapters: 6, coverage: 0.874 })),
    ).toBe("✓ 6 chapters selected · 87% coverage");
    expect(
      plannerLiveText(event({ step: "plan_write", kind: "start", manifest_hash: "0123456789abcdef" })),
    ).toBe("· hashing inputs… (01234567)");
    expect(
      plannerLiveText(event({ step: "plan_write", kind: "sanitized", n_chapters: 7, n_dropped: 1 })),
    ).toBe("· sanitized · 7 chapters · 1 empty dropped");
  });

  it("says nothing for an unknown step or kind", () => {
    expect(plannerLiveText(event({ step: "mystery", kind: "done" }))).toBe("");
    expect(plannerLiveText(event({ step: "corpus_load", kind: "heartbeat" }))).toBe("");
    expect(plannerLiveText(event({}))).toBe("");
  });
});

describe("synthLiveText", () => {
  it("describes chapter substeps", () => {
    expect(synthLiveText(event({ step: "sawc_write", kind: "start", chapter_title: "Intro" }))).toBe(
      "· starting sawc_write for Intro…",
    );
    expect(synthLiveText(event({ step: "synth", kind: "start", chapter_id: "01_a" }))).toBe(
      "· starting 01_a…",
    );
    expect(synthLiveText(event({ step: "sawc_write", kind: "done", wall_ms: 900 }))).toBe("✓ done (900 ms)");
    expect(synthLiveText(event({ step: "digest_construct", kind: "sources_loaded", n_sources: 12 }))).toBe(
      "· sources loaded (12 files)",
    );
    expect(synthLiveText(event({ step: "sawc_write", kind: "section_done", section: "Setup" }))).toBe(
      '· section "Setup" done',
    );
    expect(synthLiveText(event({ step: "render_audit_write", kind: "artifacts_written", n_files: 3 }))).toBe(
      "· artifacts written (3 files)",
    );
    expect(synthLiveText(event({ step: "sawc_write", kind: "other" }))).toBe("");
  });
});
