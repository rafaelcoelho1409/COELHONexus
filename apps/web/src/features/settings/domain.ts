import type { EmbeddingTestResult, KeyStatus, LlmTestResult, MigrationStatus, TaskStatus } from "./entities";
import { DEFAULT_MODEL, type SaveBody, TERMINAL_TASK_STATES } from "./params";

/** The trust-safe pill text for the API-key field — the field itself never carries the key back. */
export function keyPillLabel(status: KeyStatus): { tone: "custom" | "env" | "none"; text: string } {
  if (status.has_key && status.source === "user") {
    return { tone: "custom", text: status.last4 ? `Custom key ••••${status.last4}` : "Custom key" };
  }
  if (status.has_key && status.source === "env") {
    return { tone: "env", text: status.last4 ? `Env key ••••${status.last4}` : "Env key" };
  }
  return { tone: "none", text: "No key" };
}

/** Returns an error message for a non-URL, or null when the value is an http(s) address. */
export function validateBaseUrl(value: string): string | null {
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    return "Enter a URL that starts with http:// or https://";
  }
  return parsed.protocol === "http:" || parsed.protocol === "https:"
    ? null
    : "Enter a URL that starts with http:// or https://";
}

/** api_key semantics: omit = keep the stored key, "" = delete it, non-blank = replace it. */
export function buildSavePayload(input: {
  url: string;
  model: string;
  apiKey: string;
  clearKey: boolean;
}): SaveBody {
  const url = input.url.trim();
  const model = input.model.trim() || DEFAULT_MODEL;
  if (input.clearKey) return { url, model, api_key: "" };
  const apiKey = input.apiKey.trim();
  return apiKey ? { url, model, api_key: apiKey } : { url, model };
}

/** One line per test outcome. Success text always contains "OK"; failure always contains "Failed". */
export function describeTest(
  kind: "llm" | "embedding",
  result: LlmTestResult | EmbeddingTestResult,
): { ok: boolean; text: string } {
  if (result.ok === false) return { ok: false, text: `Failed: ${result.error}` };
  const model = result.model ? ` · ${result.model}` : "";
  if (kind === "llm") return { ok: true, text: `OK — ${result.latency_ms} ms${model}` };
  if ("dimensions" in result) {
    return { ok: true, text: `OK — ${result.dimensions} dimensions${model} · ${result.latency_ms} ms` };
  }
  return { ok: true, text: `OK — ${result.latency_ms} ms${model}` };
}

/** Worker progress line, or the plain Celery state, or "Starting…" before the task reports anything. */
export function progressText(task: TaskStatus | undefined): string {
  if (!task) return "Starting…";
  if (task.state === "PROGRESS" && task.meta?.total != null) {
    return `${task.meta.current ?? 0}/${task.meta.total} transcripts re-embedded`;
  }
  return task.state || "Starting…";
}

const terminalStates: readonly string[] = TERMINAL_TASK_STATES;

export function isTerminalTaskState(state: string | undefined): boolean {
  return state !== undefined && terminalStates.includes(state);
}

export type MigrationView =
  | { kind: "hidden" }
  | { kind: "needed"; from: string; to: string }
  | { kind: "running"; from: string; to: string; taskId: string | undefined };

/** What the banner should show for this status. An empty `task_id` means "no task yet". */
export function migrationView(status: MigrationStatus): MigrationView {
  const state = status.state;
  if (state?.status === "running") {
    return {
      kind: "running",
      from: state.from_model,
      to: state.to_model,
      taskId: state.task_id || undefined,
    };
  }
  if (status.needed && status.mismatch) {
    return { kind: "needed", from: status.mismatch.from_model, to: status.mismatch.to_model };
  }
  return { kind: "hidden" };
}
