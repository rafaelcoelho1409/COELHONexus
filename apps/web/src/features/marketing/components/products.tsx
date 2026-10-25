import { ArrowRight } from "lucide-react";

import { PRODUCTS } from "../content";

export function Products() {
  return (
    <section id="products" aria-labelledby="products-title" className="border-b border-border">
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6 md:py-24">
        <p className="mono-label">Products</p>
        <h2 id="products-title" className="mt-4 max-w-2xl text-balance text-3xl font-semibold tracking-tight">
          Two tools, one platform.
        </h2>
        <ul className="mt-12 grid grid-cols-1 gap-px border border-border bg-border md:grid-cols-2">
          {PRODUCTS.map((product) => (
            <li key={product.name} className="flex flex-col bg-background p-7 sm:p-9">
              <p className="mono-label">{product.index}</p>
              <h3 className="mt-3 text-xl font-semibold tracking-tight">{product.name}</h3>
              <p className="mt-3 text-pretty text-base text-muted-foreground">{product.summary}</p>
              <ol
                aria-label={`${product.name} pipeline`}
                className="mt-8 flex flex-wrap items-center gap-x-2 gap-y-2 font-mono text-xs uppercase tracking-[0.08em]"
              >
                {product.pipeline.map((step, i) => (
                  <li key={step} className="flex items-center gap-2">
                    <span className="border border-border px-2.5 py-1 text-foreground">{step}</span>
                    {i < product.pipeline.length - 1 && (
                      <span aria-hidden="true" className="text-muted-foreground">
                        →
                      </span>
                    )}
                  </li>
                ))}
              </ol>
              <p className="mt-auto pt-8 font-mono text-xs text-muted-foreground">{product.stack}</p>
              {"open" in product && (
                <a
                  href={product.open.href}
                  className="mt-4 inline-flex h-9 items-center gap-1.5 self-start text-sm font-medium text-primary-text underline-offset-4 hover:underline"
                >
                  {product.open.label}
                  <ArrowRight className="size-4" aria-hidden="true" />
                </a>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
