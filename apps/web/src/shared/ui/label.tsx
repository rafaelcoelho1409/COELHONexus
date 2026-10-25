import { cn } from "cn";
import type * as React from "react";

/** The settings forms always label a control: `htmlFor` is required, so Biome can verify the wiring. */
function Label({
  className,
  htmlFor,
  children,
  ...props
}: React.ComponentProps<"label"> & { htmlFor: string }) {
  return (
    <label
      {...props}
      htmlFor={htmlFor}
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
        className,
      )}
    >
      {children}
    </label>
  );
}

export { Label };
