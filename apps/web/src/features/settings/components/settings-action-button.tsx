import { cn } from "cn";
import type { ComponentProps } from "react";

import { Button } from "@/shared/ui/button";
import { buttonVariants } from "@/shared/ui/button-variants";

/** Match the neutral outlined action controls used throughout BD and DD. */
const LOOK =
  "h-auto min-h-10 gap-2 rounded-sm border-border bg-background px-3 text-sm font-medium text-foreground hover:border-neutral-8 hover:bg-background hover:text-foreground aria-expanded:bg-background aria-pressed:border-primary aria-pressed:bg-muted dark:border-border dark:bg-background dark:hover:border-neutral-8 dark:hover:bg-background [&_svg:not([class*='size-'])]:text-muted-foreground";

export const settingsActionButtonClassName = cn(
  buttonVariants({ variant: "outline", size: "default" }),
  LOOK,
);

export function SettingsActionButton({
  className,
  ...props
}: Omit<ComponentProps<typeof Button>, "variant" | "size">) {
  return <Button variant="outline" size="default" className={`${LOOK} ${className ?? ""}`} {...props} />;
}
