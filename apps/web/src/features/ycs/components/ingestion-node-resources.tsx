import { ingestionNodeResources } from "../domain/ingestion-nodes";

const RESOURCE_ROWS = [
  { label: "Agent", key: "agents" },
  { label: "Skills", key: "skills" },
  { label: "Tools", key: "tools" },
] as const;

/** The same Agent, Skills, and Tools rows appear on each graph node and in its detail sheet. */
export function IngestionNodeResources({ nodeId, compact = false }: { nodeId: string; compact?: boolean }) {
  const resources = ingestionNodeResources(nodeId);
  if (!resources) return null;

  return (
    <span className={`grid min-w-0 gap-y-0.5 text-xs leading-4 ${compact ? "px-3 pt-1" : "mt-2"}`}>
      {RESOURCE_ROWS.map(({ label, key }) => {
        const value = resources[key].join(", ") || "None";
        return (
          <span key={key} className="grid min-w-0 grid-cols-[3.5rem_minmax(0,1fr)] gap-2">
            <span className="text-muted-foreground">{label}</span>
            <span title={value} className={`min-w-0 ${compact ? "truncate" : "break-words"}`}>
              {value}
            </span>
          </span>
        );
      })}
    </span>
  );
}
