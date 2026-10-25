import { ArrowUpRight } from "lucide-react";

import { APP_NAV } from "@/shared/layout/nav";
import { BrandMark } from "@/shared/ui/brand-mark";
import { ThemeToggle } from "@/shared/ui/theme-toggle";

import { NAV } from "../content";
import { REPO_URL } from "../params";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-6 px-4 sm:px-6">
        <a href="#top" className="flex items-center gap-2.5 text-foreground">
          <BrandMark className="size-5 text-primary-text" />
          {/* The wordmark gives way to the app links on a phone; it stays the link's accessible name. */}
          <span className="sr-only font-semibold tracking-tight sm:not-sr-only">COELHO Nexus</span>
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
        <div className="flex items-center gap-1 sm:gap-2">
          {/* The way in: the two pages that exist as apps, on every screen size. */}
          <nav aria-label="Console" className="flex items-center">
            {APP_NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="inline-flex h-9 items-center px-2 text-sm text-foreground transition-colors hover:text-primary-text sm:px-3"
              >
                {item.label}
              </a>
            ))}
          </nav>
          <ThemeToggle />
          <a
            href={REPO_URL}
            className="hidden h-9 items-center gap-2 border border-border px-3 text-sm text-foreground transition-colors hover:border-neutral-8 sm:inline-flex"
          >
            <span>Source</span>
            <ArrowUpRight className="size-4" aria-hidden="true" />
          </a>
        </div>
      </div>
    </header>
  );
}
