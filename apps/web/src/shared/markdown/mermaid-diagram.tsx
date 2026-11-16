"use client";

import { useEffect, useId, useState } from "react";

import { VisualizationFrame } from "./visualization-frame";

type Drawn = { svg: string } | { failed: true };

/** The mermaid library is large, so it is fetched the first time a diagram appears, not with the page. */
export function MermaidDiagram({
  source,
  label = "Diagram",
  onExpand,
}: {
  source: string;
  label?: string;
  onExpand?: () => void;
}) {
  const id = `mermaid-${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const [drawn, setDrawn] = useState<Drawn | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const { default: mermaid } = await import("mermaid");
        // `strict` makes mermaid sanitise labels and refuse scripts and click handlers in a diagram's source.
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: document.documentElement.dataset.theme === "light" ? "default" : "dark",
        });
        const { svg } = await mermaid.render(id, source);
        if (alive) setDrawn({ svg });
      } catch {
        if (alive) setDrawn({ failed: true });
      }
    })();
    return () => {
      alive = false;
    };
  }, [id, source]);

  if (drawn && "failed" in drawn) {
    return (
      <VisualizationFrame label={label} onExpand={onExpand}>
        <figure className="md-code">
          <figcaption className="md-code-bar">
            <span>mermaid — the diagram could not be drawn</span>
          </figcaption>
          {/* biome-ignore lint/a11y/noNoninteractiveTabindex: long lines scroll sideways, so keyboard users must be able to focus the block to scroll it (WCAG 2.1.1) */}
          <pre tabIndex={0}>{source}</pre>
        </figure>
      </VisualizationFrame>
    );
  }
  if (!drawn) {
    return (
      <VisualizationFrame label={label} onExpand={onExpand}>
        <p className="md-diagram text-sm text-muted-foreground" role="status">
          Drawing diagram…
        </p>
      </VisualizationFrame>
    );
  }
  return (
    <VisualizationFrame label={label} onExpand={onExpand}>
      <figure
        className="md-diagram"
        role="img"
        aria-label={label}
        // The SVG is mermaid's own output from a `strict` render, never raw user HTML.
        // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid's sanitised SVG
        dangerouslySetInnerHTML={{ __html: drawn.svg }}
      />
    </VisualizationFrame>
  );
}
