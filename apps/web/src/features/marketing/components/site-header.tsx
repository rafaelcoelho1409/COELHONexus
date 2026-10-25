import { ArrowUpRight } from "lucide-react";

import { ThemeToggle } from "@/shared/ui/theme-toggle";

import { NAV } from "../content";
import { REPO_URL } from "../params";
import { BrandMark } from "./brand-mark";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-6 px-4 sm:px-6">
        <a href="#top" className="flex items-center gap-2.5 text-foreground">
          <BrandMark className="size-5 text-primary-text" />
          <span className="font-semibold tracking-tight">COELHO Nexus</span>
        </a>
        <nav aria-label="Primary" className="hidden items-center gap-7 md:flex">
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <ThemeToggle />
          <a
            href={REPO_URL}
            className="inline-flex h-9 items-center gap-2 border border-border px-3 text-sm text-foreground transition-colors hover:border-neutral-8"
          >
            <span>Source</span>
            <ArrowUpRight className="size-4" aria-hidden="true" />
          </a>
        </div>
      </div>
    </header>
  );
}
