import type { ComponentProps } from "react";

import { Button } from "@/shared/ui/button";

/** The one button look of the Ingestion tab: outlined, square corners, a neutral border that darkens on hover. A
 * pressed toggle gets the primary border and a muted fill, and `aria-pressed` keeps the state in the markup, never
 * only in colour. */
const LOOK =
  "h-auto min-h-10 gap-2 rounded-sm border-border bg-background px-3 text-sm font-medium text-foreground hover:border-neutral-8 hover:bg-background hover:text-foreground aria-expanded:bg-background aria-pressed:border-primary aria-pressed:bg-muted dark:border-border dark:bg-background dark:hover:border-neutral-8 dark:hover:bg-background [&_svg:not([class*='size-'])]:text-muted-foreground";
const ICON_ONLY = "size-10 min-h-0 px-0";

export function ActionButton({
  iconOnly = false,
  className,
  ...props
}: Omit<ComponentProps<typeof Button>, "variant" | "size"> & { iconOnly?: boolean }) {
  return (
    <Button
      variant="outline"
      size="default"
      className={`${LOOK} ${iconOnly ? ICON_ONLY : ""} ${className ?? ""}`}
      {...props}
    />
  );
}
