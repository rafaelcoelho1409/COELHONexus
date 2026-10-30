import { Ban, Check, Circle, CircleDot, Minus, TriangleAlert, X } from "lucide-react";
import type { JSX } from "react";

import type { StatusWord } from "../domain";

export type BadgeTone = StatusWord | "na";

const ICON: Record<BadgeTone, (props: { className: string }) => JSX.Element> = {
  queued: (props) => <Circle aria-hidden {...props} />,
  running: (props) => <CircleDot aria-hidden {...props} />,
  done: (props) => <Check aria-hidden {...props} />,
  partial: (props) => <TriangleAlert aria-hidden {...props} />,
  failed: (props) => <X aria-hidden {...props} />,
  cancelled: (props) => <Ban aria-hidden {...props} />,
  na: (props) => <Minus aria-hidden {...props} />,
};

const TEXT: Record<BadgeTone, string> = {
  queued: "text-muted-foreground",
  running: "text-running-text",
  done: "text-success-text",
  partial: "text-warning-text",
  failed: "text-destructive-text",
  cancelled: "text-muted-foreground",
  na: "text-muted-foreground",
};

/** A status as an icon and a word — the state never rests on colour alone. The icon is decoration; the word names the state. */
export function StatusBadge({
  tone,
  label,
  className = "",
}: {
  tone: BadgeTone;
  label: string;
  className?: string;
}): JSX.Element {
  const Icon = ICON[tone];
  return (
    <span
      data-tone={tone}
      className={`inline-flex items-center gap-1.5 text-xs font-medium ${TEXT[tone]} ${className}`}
    >
      <Icon className={`size-3.5 shrink-0 ${tone === "running" ? "animate-status" : ""}`} />
      {label}
    </span>
  );
}
