import { Check } from "lucide-react";

/** The check beside a box's title once its work finished cleanly (Ingestion's boxes, and the Planner's and Synth's). */
export function SuccessMark() {
  return (
    <span
      role="img"
      aria-label="Completed successfully"
      title="Completed successfully"
      className="inline-flex text-success-text"
    >
      <Check className="size-4" aria-hidden="true" />
    </span>
  );
}
