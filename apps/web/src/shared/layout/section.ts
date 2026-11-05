/** The second navbar's data: one section per console app (title, one-line description, stage tabs). Each feature owns
 * its own table; the app layout hands them all to the bar, which picks the one the pathname is in. */

export interface SectionTab {
  key: string;
  label: string;
  href: string;
  /** Shown in the strip. A tab without it is a deep link that appears only while it is the current page. */
  tab: boolean;
  /** Keeps the section's `carryParam` (the framework slug) when moving to this tab. */
  carry: boolean;
}

export interface Section {
  href: string;
  title: string;
  description: string;
  /** The tab marked current on a path that is none of the tabs (`/bd` opens the Ingestion stage). */
  defaultTab?: string;
  /** The search parameter that follows the reader from tab to tab. */
  carryParam?: string;
  tabs: readonly SectionTab[];
}

/** The section a path belongs to: its own path and everything below it, as the primary nav marks it. */
export function sectionFor(sections: readonly Section[], pathname: string): Section | null {
  return (
    sections.find((section) => pathname === section.href || pathname.startsWith(`${section.href}/`)) ?? null
  );
}

export function currentTabKey(section: Section, pathname: string): string | null {
  return section.tabs.find((tab) => tab.href === pathname)?.key ?? section.defaultTab ?? null;
}

/** The strip's tabs: every `tab`, plus a deep link while the reader is on it. */
export function visibleTabs(section: Section, pathname: string): SectionTab[] {
  const current = currentTabKey(section, pathname);
  return section.tabs.filter((tab) => tab.tab || tab.key === current);
}

export function tabHref(section: Section, tab: SectionTab, carried: string | null): string {
  if (!tab.carry || !section.carryParam || !carried) return tab.href;
  return `${tab.href}?${section.carryParam}=${encodeURIComponent(carried)}`;
}
