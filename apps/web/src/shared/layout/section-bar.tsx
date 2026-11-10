"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { currentTabKey, type Section, sectionFor, tabHref, visibleTabs } from "./section";

/** The second navbar, right under the app header: the page title and one-line description on the left, the stage
 * tabs on the right, drawn like the primary nav (`aria-current` plus the foreground colour, never colour alone).
 * Sticky from `sm` up; a phone lets it scroll away so the narrow screen keeps its height. */
export function SectionBar({ sections }: { sections: readonly Section[] }) {
  const pathname = usePathname() ?? "";
  const section = sectionFor(sections, pathname);
  if (!section) return null;

  // The tabs follow `?slug=`, which Next only reads inside a Suspense boundary; until then they carry nothing.
  return (
    <Suspense fallback={<Bar section={section} pathname={pathname} carried={null} />}>
      <CarryingBar section={section} pathname={pathname} />
    </Suspense>
  );
}

function CarryingBar({ section, pathname }: { section: Section; pathname: string }) {
  const params = useSearchParams();
  const carried = section.carryParam ? params.get(section.carryParam) : null;
  return <Bar section={section} pathname={pathname} carried={carried} />;
}

function Bar({ section, pathname, carried }: { section: Section; pathname: string; carried: string | null }) {
  const current = currentTabKey(section, pathname);
  const tabs = visibleTabs(section, pathname);

  return (
    <div data-section-bar className="border-b border-border bg-background sm:sticky sm:top-14 sm:z-40">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-1 px-4 py-2 sm:h-12 sm:flex-nowrap sm:py-0 sm:px-6">
        <div className="flex min-w-0 items-baseline gap-x-3">
          <h1 className="shrink-0 text-lg font-semibold tracking-tight">{section.title}</h1>
          {/* Room is tight below `lg`, so the description gives way there and stays the page's accessible summary. */}
          <p className="sr-only min-w-0 truncate text-sm text-muted-foreground lg:not-sr-only">
            {section.description}
          </p>
        </div>
        {tabs.length > 0 ? (
          <nav
            aria-label={`${section.title} stages`}
            className="flex min-w-0 flex-wrap items-center max-sm:w-full sm:shrink-0"
          >
            {tabs.map((tab) => (
              <Link
                key={tab.key}
                href={tabHref(section, tab, carried)}
                aria-current={tab.key === current ? "page" : undefined}
                className={
                  tab.key === current
                    ? "inline-flex h-9 items-center px-2 text-base font-semibold text-foreground transition-colors sm:px-3"
                    : "inline-flex h-9 items-center px-2 text-base font-medium text-muted-foreground transition-colors hover:text-foreground sm:px-3"
                }
              >
                {tab.label}
              </Link>
            ))}
          </nav>
        ) : null}
      </div>
    </div>
  );
}
