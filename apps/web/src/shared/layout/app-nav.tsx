"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { APP_NAV } from "./nav";

/** Marks the current page with `aria-current` and the foreground colour — never colour alone. A page is
 * current for its own path and everything below it (`/dd` stays marked on `/dd/planner`). */
export function AppNav() {
  const pathname = usePathname();

  return (
    <>
      {APP_NAV.map((item) => {
        const current = pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={current ? "page" : undefined}
            className={
              current
                ? "inline-flex h-9 items-center px-2 text-sm text-foreground transition-colors sm:px-3"
                : "inline-flex h-9 items-center px-2 text-sm text-muted-foreground transition-colors hover:text-foreground sm:px-3"
            }
          >
            {item.label}
          </Link>
        );
      })}
    </>
  );
}
