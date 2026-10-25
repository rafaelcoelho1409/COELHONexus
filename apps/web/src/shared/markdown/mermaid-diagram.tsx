"use client";

import { useEffect, useId, useState } from "react";

type Drawn = { svg: string } | { failed: true };

/** The mermaid library is large, so it is fetched the first time a diagram appears, not with the page. */
export function MermaidDiagram({ source }: { source: string }) {
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
      <figure className="md-code">
        <figcaption className="md-code-bar">
          <span>mermaid — the diagram could not be drawn</span>
        </figcaption>
        {/* biome-ignore lint/a11y/noNoninteractiveTabindex: long lines scroll sideways, so keyboard users must be able to focus the block to scroll it (WCAG 2.1.1) */}
        <pre tabIndex={0}>{source}</pre>
      </figure>
    );
  }
  if (!drawn) {
    return (
      <p className="md-diagram text-sm text-muted-foreground" role="status">
        Drawing diagram…
      </p>
    );
  }
  return (
    <figure
      className="md-diagram"
      role="img"
      aria-label="Diagram"
      // The SVG is mermaid's own output from a `strict` render, never raw user HTML.
      // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid's sanitised SVG
      dangerouslySetInnerHTML={{ __html: drawn.svg }}
    />
  );
}
