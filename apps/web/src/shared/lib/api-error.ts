const FALLBACK_MESSAGE = "Something went wrong. Try again.";

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function readDetail(body: Record<string, unknown>): string | null {
  const { detail } = body;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const first: unknown = detail[0];
    if (isRecord(first) && typeof first.msg === "string" && first.msg.trim()) return first.msg;
  }
  return null;
}

/**
 * One readable line from a FastAPI error — `{"detail": "text"}` or `{"detail": [{"msg": "…"}]}`.
 * The generated client throws the parsed body itself; an `Error` may carry that body as JSON text.
 * Anything else (network failure, stack trace, unknown shape) becomes a neutral sentence.
 */
export function apiErrorMessage(error: unknown): string {
  if (isRecord(error)) {
    const message = readDetail(error);
    if (message) return message;
  }
  if (error instanceof Error) {
    try {
      const body: unknown = JSON.parse(error.message);
      if (isRecord(body)) return readDetail(body) ?? FALLBACK_MESSAGE;
    } catch {
      // not JSON — fall through to the neutral message
    }
  }
  return FALLBACK_MESSAGE;
}
