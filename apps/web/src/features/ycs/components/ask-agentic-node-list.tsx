import type { AskAgenticNode, AskAgenticNodeStatus } from "./ask-agentic-types";
import { StatusBadge } from "./status-badge";

const GROUPS = [
  { id: "shared", label: "Question and route" },
  { id: "conversation", label: "Conversation route" },
  { id: "standard", label: "Video evidence route" },
  { id: "deep", label: "Deep research route" },
] as const;

const STATUS: Record<
  AskAgenticNodeStatus,
  { tone: "queued" | "running" | "done" | "failed" | "na"; label: string }
> = {
  queued: { tone: "queued", label: "Queued" },
  active: { tone: "running", label: "Running" },
  done: { tone: "done", label: "Done" },
  failed: { tone: "failed", label: "Failed" },
  skipped: { tone: "na", label: "Not selected" },
};

export function AskAgenticNodeList({
  nodes,
  onOpen,
}: {
  nodes: readonly AskAgenticNode[];
  onOpen: (nodeId: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3" aria-label="Adaptive Agentic RAG nodes">
      {GROUPS.map((group) => {
        const groupNodes = nodes.filter((node) => node.group === group.id);
        if (groupNodes.length === 0) return null;
        return (
          <section key={group.id} aria-label={group.label}>
            <h4 className="mb-1.5 text-xs font-medium text-muted-foreground">{group.label}</h4>
            <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
              {groupNodes.map((node) => (
                <li key={node.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(node.id)}
                    aria-haspopup="dialog"
                    aria-label={`Open ${node.label} node details — ${STATUS[node.status].label}`}
                    className={`flex min-h-10 w-full min-w-0 items-start justify-between gap-2 rounded-sm border bg-card px-2.5 py-2 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring ${
                      node.status === "active"
                        ? "border-running"
                        : node.status === "failed"
                          ? "border-destructive"
                          : "border-border"
                    }`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm leading-5">{node.label}</span>
                      <span className="mt-0.5 block font-mono text-xs text-muted-foreground">{node.id}</span>
                      {node.activity ? (
                        <span className="mt-1 block break-words text-xs text-muted-foreground">
                          {node.activity}
                        </span>
                      ) : null}
                    </span>
                    <StatusBadge
                      tone={STATUS[node.status].tone}
                      label={STATUS[node.status].label}
                      className="shrink-0"
                    />
                  </button>
                </li>
              ))}
            </ol>
          </section>
        );
      })}
    </div>
  );
}
