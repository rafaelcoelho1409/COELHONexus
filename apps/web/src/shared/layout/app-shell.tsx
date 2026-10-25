import Link from "next/link";

import { BrandMark } from "@/shared/ui/brand-mark";
import { ThemeToggle } from "@/shared/ui/theme-toggle";

import { AppNav } from "./app-nav";

/** Solid, sticky operator console chrome — same hairline treatment as the marketing header. */
export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="sticky top-0 z-50 border-b border-border bg-background">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-6 px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2.5 text-foreground">
            <BrandMark className="size-5 text-primary-text" />
            {/* The wordmark gives way to the nav on a phone; it stays the link's accessible name. */}
            <span className="sr-only font-semibold tracking-tight sm:not-sr-only">COELHO Nexus</span>
          </Link>
          <div className="flex items-center gap-1 sm:gap-2">
            <nav aria-label="Primary" className="flex items-center">
              <AppNav />
            </nav>
            <ThemeToggle />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>
    </>
  );
}
