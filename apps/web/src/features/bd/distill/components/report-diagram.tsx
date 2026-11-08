import Link from "next/link";

import { Markdown } from "@/shared/markdown";

import { diagramMermaidSource } from "../domain/visualization";
import type { ReportDiagram as ReportDiagramData } from "../entities";

export function ReportDiagram({
  diagram,
  pageHref,
}: {
  diagram: ReportDiagramData;
  pageHref: (page: number) => string;
}) {
  const source = diagramMermaidSource(diagram);

  return (
    <section aria-label={`Diagram: ${diagram.title}`} className="border-t border-border pt-4">
      <h4 className="font-medium">{diagram.title}</h4>
      <p className="mt-1 text-sm text-muted-foreground">{diagram.accessible_description}</p>
      {diagram.nodes.length > 0 ? (
        <section
          className="mt-3 min-w-0 overflow-x-auto rounded-sm border border-border p-3"
          // biome-ignore lint/a11y/noNoninteractiveTabindex: large diagrams can scroll horizontally, so keyboard users need to focus the region to scroll it (WCAG 2.1.1)
          tabIndex={0}
          aria-label={`${diagram.title} visual diagram`}
        >
          <Markdown text={`\`\`\`mermaid\n${source}\n\`\`\``} />
        </section>
      ) : null}
      <ol className="mt-3 list-decimal space-y-3 pl-5 text-sm">
        {diagram.nodes.map((node) => (
          <li key={node.node_id}>
            <span className="font-medium">{node.label}</span>
            <Attribution value={node.attribution} />
            <EvidenceQuotes evidence={node.evidence} pageHref={pageHref} />
          </li>
        ))}
      </ol>
      {diagram.edges.length > 0 ? (
        <ul
          aria-label={`${diagram.title} relationships`}
          className="mt-3 space-y-3 border-l border-border pl-4 text-sm text-muted-foreground"
        >
          {diagram.edges.map((edge) => {
            const sourceNode = diagram.nodes.find((node) => node.node_id === edge.source_id);
            const targetNode = diagram.nodes.find((node) => node.node_id === edge.target_id);
            return (
              <li
                key={`${edge.source_id}-${edge.target_id}-${edge.relation}-${edge.learning_unit_ids.join("-")}`}
              >
                {sourceNode?.label ?? edge.source_id} — {edge.relation.replaceAll("_", " ")} →{" "}
                {targetNode?.label ?? edge.target_id}
                <EvidenceQuotes evidence={edge.evidence} pageHref={pageHref} />
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}

function Attribution({ value }: { value: "book" | "agent_inference" }) {
  return (
    <p className="mt-1 text-xs text-muted-foreground">
      {value === "book" ? "From the book" : "AI inference"}
    </p>
  );
}

function EvidenceQuotes({
  evidence,
  pageHref,
}: {
  evidence: readonly { page_number: number; quote: string }[];
  pageHref: (page: number) => string;
}) {
  if (evidence.length === 0) return null;
  return (
    <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
      {evidence.map((item) => (
        <li key={`${item.page_number}-${item.quote}`}>
          “{item.quote}” —{" "}
          <Link href={pageHref(item.page_number)} className="underline underline-offset-2">
            Page {item.page_number}
          </Link>
        </li>
      ))}
    </ul>
  );
}
