import { LAYERS } from "../content";

export function System() {
  return (
    <section id="system" aria-labelledby="system-title" className="border-b border-border">
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 md:py-24">
        <p className="mono-label">System</p>
        <h2 id="system-title" className="mt-4 max-w-2xl text-balance text-3xl font-semibold tracking-tight">
          Six layers, each replaceable on its own.
        </h2>
        <ol className="mt-12 border border-border">
          {LAYERS.map((layer) => (
            <li
              key={layer.label}
              className="grid grid-cols-1 gap-3 border-b border-border px-5 py-5 last:border-b-0 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-8 sm:px-7"
            >
              <div className="flex items-baseline gap-3">
                <span className="mono-label">{layer.index}</span>
                <h3 className="text-sm font-semibold uppercase tracking-[0.08em]">{layer.label}</h3>
              </div>
              <ul className="flex flex-wrap gap-2">
                {layer.nodes.map((node) => (
                  <li
                    key={node}
                    className={
                      "highlight" in layer && layer.highlight === node
                        ? "border border-primary bg-accent px-2.5 py-1 font-mono text-xs text-foreground"
                        : "border border-border px-2.5 py-1 font-mono text-xs text-muted-foreground"
                    }
                  >
                    {node}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
