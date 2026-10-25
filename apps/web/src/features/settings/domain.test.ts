import { describe, expect, it } from "vitest";

import {
  buildSavePayload,
  describeTest,
  isTerminalTaskState,
  keyPillLabel,
  migrationView,
  progressText,
  validateBaseUrl,
} from "./domain";
import type { ConnectionView, MigrationStatus, TaskStatus } from "./entities";
import {
  connectionViewSchema,
  embeddingTestSchema,
  keyStatusSchema,
  llmTestSchema,
  migrationStatusSchema,
  taskStatusSchema,
} from "./entities";
import { MIGRATION_POLL_MS, MIGRATION_RECHECK_MS } from "./params";

describe("entities", () => {
  it("parses a saved connection view, key status masked", () => {
    expect(
      connectionViewSchema.parse({
        url: "http://g/v1",
        model: "m",
        has_key: true,
        source: "user",
        last4: "abcd",
      }),
    ).toEqual({ url: "http://g/v1", model: "m", has_key: true, source: "user", last4: "abcd" });
  });

  it("parses key status without a key and rejects a raw key if one sneaks in", () => {
    expect(keyStatusSchema.parse({ has_key: false, source: null, last4: null })).toEqual({
      has_key: false,
      source: null,
      last4: null,
    });
  });

  it("yields typed views to build on", () => {
    const view: ConnectionView = connectionViewSchema.parse({
      url: "http://g/v1",
      model: "m",
      has_key: false,
      source: null,
      last4: null,
    });
    expect(view.has_key).toBe(false);
  });

  it("splits LLM test results by the ok discriminant", () => {
    expect(llmTestSchema.parse({ ok: true, reply: "hi", latency_ms: 412 })).toMatchObject({ reply: "hi" });
    expect(llmTestSchema.parse({ ok: false, error: "boom", latency_ms: 3 })).toMatchObject({ error: "boom" });
  });

  it("parses embedding test results the same way", () => {
    expect(
      embeddingTestSchema.parse({ ok: true, dimensions: 1024, latency_ms: 87, model: null }),
    ).toMatchObject({
      dimensions: 1024,
    });
  });

  it("keeps migration status lenient: unknown task fields survive, mismatch may be absent", () => {
    const status = migrationStatusSchema.parse({
      needed: false,
      mismatch: null,
      state: { status: "idle", from_model: "a", to_model: "b", celery_meta: 1 },
      active_collection: null,
    });
    expect(status.state).toMatchObject({ status: "idle", celery_meta: 1 });
  });

  it("accepts a task payload with progress metadata", () => {
    expect(
      taskStatusSchema.parse({ task_id: "t", state: "PROGRESS", meta: { current: 3, total: 10 } }),
    ).toMatchObject({
      meta: { current: 3, total: 10 },
    });
  });

  it("polling windows are fixed: 3s poll, 2s re-check after a terminal state", () => {
    expect(MIGRATION_POLL_MS).toBe(3000);
    expect(MIGRATION_RECHECK_MS).toBe(2000);
  });
});

describe("keyPillLabel", () => {
  it("names a user-saved key with its masked digits", () => {
    expect(keyPillLabel({ has_key: true, source: "user", last4: "abcd" })).toEqual({
      tone: "custom",
      text: "Custom key ••••abcd",
    });
  });

  it("names a deployment-managed key", () => {
    expect(keyPillLabel({ has_key: true, source: "env", last4: "ef01" })).toEqual({
      tone: "env",
      text: "Env key ••••ef01",
    });
  });

  it("says no key when there is none, even with a null source", () => {
    expect(keyPillLabel({ has_key: false, source: null, last4: null })).toEqual({
      tone: "none",
      text: "No key",
    });
  });

  it("never shows stray dots when last4 is missing", () => {
    expect(keyPillLabel({ has_key: true, source: "user", last4: null }).text).toBe("Custom key");
  });
});

describe("validateBaseUrl", () => {
  it("accepts http and https URLs and trims whitespace", () => {
    expect(validateBaseUrl("  http://gateway.internal:8000/v1  ")).toBeNull();
    expect(validateBaseUrl("https://example.com/api")).toBeNull();
  });

  it("rejects non-http schemes", () => {
    expect(validateBaseUrl("ftp://cdn.example.com")).toBe("Enter a URL that starts with http:// or https://");
  });

  it("rejects blank and malformed values", () => {
    expect(validateBaseUrl("   ")).not.toBeNull();
    expect(validateBaseUrl("gateway.internal:8000")).not.toBeNull();
  });
});

describe("buildSavePayload", () => {
  it("omits api_key entirely when nothing is typed, so the stored key survives", () => {
    const payload = buildSavePayload({ url: " http://g/v1 ", model: " m1 ", apiKey: "   ", clearKey: false });
    expect(payload).toEqual({ url: "http://g/v1", model: "m1" });
    expect("api_key" in payload).toBe(false);
  });

  it("sends a typed key once, trimmed", () => {
    expect(buildSavePayload({ url: "http://g", model: "m", apiKey: "  sk-1  ", clearKey: false })).toEqual({
      url: "http://g",
      model: "m",
      api_key: "sk-1",
    });
  });

  it("clearKey wins and sends an empty string", () => {
    expect(buildSavePayload({ url: "http://g", model: "", apiKey: "", clearKey: true })).toEqual({
      url: "http://g",
      model: "auto",
      api_key: "",
    });
  });

  it("falls back to the default model when the field is blank", () => {
    expect(buildSavePayload({ url: "http://g", model: "   ", apiKey: "", clearKey: false }).model).toBe(
      "auto",
    );
  });
});

describe("describeTest", () => {
  it("formats a passing LLM test with latency and, when present, the model", () => {
    expect(describeTest("llm", { ok: true, reply: "hi", latency_ms: 412, model: "model-id" })).toEqual({
      ok: true,
      text: "OK — 412 ms · model-id",
    });
    expect(describeTest("llm", { ok: true, reply: "hi", latency_ms: 412, model: null }).text).toBe(
      "OK — 412 ms",
    );
  });

  it("formats a passing embedding test with dimensions first and latency last", () => {
    expect(describeTest("embedding", { ok: true, dimensions: 1024, latency_ms: 87, model: "e5" })).toEqual({
      ok: true,
      text: "OK — 1024 dimensions · e5 · 87 ms",
    });
  });

  it("carries the failure text verbatim", () => {
    expect(describeTest("llm", { ok: false, error: "Connection refused", latency_ms: 3 })).toEqual({
      ok: false,
      text: "Failed: Connection refused",
    });
  });
});

describe("progressText", () => {
  it("counts progress from a PROGRESS meta", () => {
    const task: TaskStatus = { task_id: "t", state: "PROGRESS", meta: { current: 3, total: 10 } };
    expect(progressText(task)).toBe("3/10 transcripts re-embedded");
  });

  it("defaults current to 0 when PROGRESS has a total but no current", () => {
    expect(progressText({ task_id: "t", state: "PROGRESS", meta: { total: 10 } })).toBe(
      "0/10 transcripts re-embedded",
    );
  });

  it("falls back to the raw state without meta", () => {
    expect(progressText({ task_id: "t", state: "PENDING" })).toBe("PENDING");
  });

  it("is Starting… before the task exists", () => {
    expect(progressText(undefined)).toBe("Starting…");
    expect(progressText({ task_id: "t", state: "" })).toBe("Starting…");
  });
});

describe("isTerminalTaskState", () => {
  it("recognises the states that end polling", () => {
    for (const state of ["SUCCESS", "FAILURE", "REVOKED"]) {
      expect(isTerminalTaskState(state)).toBe(true);
    }
  });

  it("returns false for live or unknown states", () => {
    expect(isTerminalTaskState("PENDING")).toBe(false);
    expect(isTerminalTaskState("PROGRESS")).toBe(false);
    expect(isTerminalTaskState("")).toBe(false);
    expect(isTerminalTaskState(undefined)).toBe(false);
  });
});

describe("migrationView", () => {
  const status = (over: Partial<MigrationStatus>): MigrationStatus => ({
    needed: false,
    mismatch: null,
    state: null,
    active_collection: null,
    ...over,
  });

  it("keeps the banner hidden when everything is fine", () => {
    expect(migrationView(status({}))).toEqual({ kind: "hidden" });
    expect(migrationView(status({ needed: false, mismatch: { from_model: "a", to_model: "b" } }))).toEqual({
      kind: "hidden",
    });
  });

  it("shows needed when a mismatch is waiting, regardless of a non-running state", () => {
    expect(migrationView(status({ needed: true, mismatch: { from_model: "old", to_model: "new" } }))).toEqual(
      {
        kind: "needed",
        from: "old",
        to: "new",
      },
    );
    expect(
      migrationView(
        status({
          needed: true,
          mismatch: { from_model: "old", to_model: "new" },
          state: { status: "FAILURE", from_model: "old", to_model: "new" },
        }),
      ),
    ).toEqual({ kind: "needed", from: "old", to: "new" });
  });

  it("running wins over needed and turns an empty task_id into no task yet", () => {
    expect(
      migrationView(
        status({
          state: { status: "running", from_model: "old", to_model: "new", task_id: "" },
          needed: true,
          mismatch: { from_model: "old", to_model: "new" },
        }),
      ),
    ).toEqual({ kind: "running", from: "old", to: "new", taskId: undefined });
    expect(
      migrationView(
        status({ state: { status: "running", from_model: "old", to_model: "new", task_id: "t9" } }),
      ),
    ).toEqual({ kind: "running", from: "old", to: "new", taskId: "t9" });
  });
});
