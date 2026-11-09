import { Ban, Check, Circle, CircleDot, TriangleAlert, X } from "lucide-react";
import type { JSX } from "react";

import type { StatusWord } from "../domain";

const GLYPH_CLASS = "size-3.5 shrink-0";

export function IngestionStatusGlyph({ status }: { status: StatusWord }): JSX.Element {
  if (status === "done") return <Check aria-hidden className={`${GLYPH_CLASS} text-success-text`} />;
  if (status === "running") {
    return <CircleDot aria-hidden className={`${GLYPH_CLASS} animate-status text-running-text`} />;
  }
  if (status === "partial")
    return <TriangleAlert aria-hidden className={`${GLYPH_CLASS} text-warning-text`} />;
  if (status === "failed") return <X aria-hidden className={`${GLYPH_CLASS} text-destructive-text`} />;
  if (status === "cancelled") return <Ban aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
  return <Circle aria-hidden className={`${GLYPH_CLASS} text-muted-foreground`} />;
}
