import { keyPillLabel } from "../domain";
import type { KeyStatus } from "../entities";
import type { ConnectionKind } from "../params";

const TONE_CLASS = {
  custom: "text-success-text",
  env: "text-warning-text",
  none: "text-muted-foreground",
} as const;

/** The pill beside the API-key field: the key's source and last four characters, never the key itself. */
export function KeyStatusPill({ kind, status }: { kind: ConnectionKind; status: KeyStatus }) {
  const pill = keyPillLabel(status);
  return (
    <span id={`connection-${kind}-key-status`} className={`font-mono text-xs ${TONE_CLASS[pill.tone]}`}>
      {pill.text}
    </span>
  );
}
