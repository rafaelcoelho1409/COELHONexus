import { ArrowRight } from "lucide-react";

import { buttonVariants } from "@/shared/ui/button-variants";

import { REPO_URL } from "../params";
import { PlatformPanel } from "./platform-panel";

export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-title" className="border-b border-border">
      <div className="mx-auto grid max-w-screen-2xl grid-cols-1 gap-12 px-4 py-16 sm:px-6 md:py-24 lg:grid-cols-[1.15fr_1fr] lg:items-center">
        <div>
          <p className="mono-label">YouTube Content Search · Book Distiller</p>
          <h1
            id="hero-title"
            className="mt-5 text-balance text-4xl font-semibold leading-[1.08] tracking-tight sm:text-5xl"
          >
            YouTube channels, searchable. Books, distilled.
          </h1>
          <p className="mt-6 max-w-xl text-pretty text-base text-muted-foreground sm:text-lg">
            YouTube Content Search indexes a whole channel and answers questions across it, with citations.
            Book Distiller, in development, turns a book into a chapter-by-chapter study guide and researches
            across it with citations.
          </p>
          <div className="mt-9 flex flex-wrap gap-3">
            <a href="/ycs" className={buttonVariants({ size: "lg", className: "h-11 px-5 text-sm" })}>
              Open YouTube Search
              <ArrowRight className="size-4" aria-hidden="true" />
            </a>
            <a
              href="#products"
              className={buttonVariants({ variant: "outline", size: "lg", className: "h-11 px-5 text-sm" })}
            >
              See the products
            </a>
            <a
              href={REPO_URL}
              className={buttonVariants({ variant: "outline", size: "lg", className: "h-11 px-5 text-sm" })}
            >
              Read the source
            </a>
          </div>
        </div>
        <PlatformPanel />
      </div>
    </section>
  );
}
