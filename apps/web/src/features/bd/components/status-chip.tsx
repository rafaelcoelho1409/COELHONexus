import { Ban, Check, Circle, CircleDot, Clock, X } from "lucide-react";

import { type BookStatus, STATUS_LABEL } from "../domain/books";

const GLYPH = {
  ready: Circle,
  queued: Clock,
  extracting: CircleDot,
  done: Check,
  failed: X,
  cancelled: Ban,
  unknown: Circle,
} as const;

const TONE: Record<BookStatus, string> = {
  ready: "text-muted-foreground",
  queued: "text-muted-foreground",
  extracting: "text-running-text",
  done: "text-success-text",
  failed: "text-destructive-text",
  cancelled: "text-warning-text",
  unknown: "text-muted-foreground",
};

/** The state in words next to a glyph, so it never rests on the icon or its colour alone. */
export function StatusChip({ status }: { status: BookStatus }) {
  const Glyph = GLYPH[status];
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${TONE[status]}`}>
      <Glyph aria-hidden className={`size-3 shrink-0 ${status === "extracting" ? "animate-pulse" : ""}`} />
      {STATUS_LABEL[status]}
    </span>
  );
}
