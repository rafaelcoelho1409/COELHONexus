import { buttonVariants } from "@/shared/ui/button-variants";

import { QUICKSTART } from "../content";
import { REPO_URL } from "../params";

export function SourceCta() {
  return (
    <section id="source" aria-labelledby="source-title">
      <div className="mx-auto grid max-w-6xl grid-cols-1 gap-10 px-4 py-16 sm:px-6 md:py-24 lg:grid-cols-[1fr_1.2fr] lg:items-center">
        <div>
          <p className="mono-label">Source</p>
          <h2 id="source-title" className="mt-4 text-balance text-3xl font-semibold tracking-tight">
            Run it yourself.
          </h2>
          <p className="mt-4 max-w-md text-pretty text-base text-muted-foreground">
            The platform, its infrastructure and this interface live in one repository. Three commands bring
            up the cluster.
          </p>
          <a href={REPO_URL} className={buttonVariants({ size: "lg", className: "mt-8 h-11 px-5 text-sm" })}>
            Open the repository
          </a>
        </div>
        <figure className="border border-border bg-card">
          <figcaption className="border-b border-border px-5 py-3 mono-label">Quick start</figcaption>
          {/* biome-ignore lint/a11y/noNoninteractiveTabindex: scrolls sideways on narrow screens, so keyboard users must be able to focus it to scroll (WCAG 2.1.1) */}
          <pre tabIndex={0} className="overflow-x-auto px-5 py-5 font-mono text-sm leading-7 text-foreground">
            <code>
              {QUICKSTART.map((line) => (
                <span key={line} className="block">
                  <span aria-hidden="true" className="select-none text-muted-foreground">
                    ${" "}
                  </span>
                  {line}
                </span>
              ))}
            </code>
          </pre>
        </figure>
      </div>
    </section>
  );
}
