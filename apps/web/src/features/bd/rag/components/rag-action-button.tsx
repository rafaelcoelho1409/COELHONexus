"use client";

import type { ComponentProps, JSX } from "react";
import type { Button as SharedButton } from "@/shared/ui/button";
import { ActionButton } from "../../components/action-button";

type SharedButtonProps = ComponentProps<typeof SharedButton>;

/**
 * Ask and Query use the same outlined, square-corner action control as DD's pages.
 * Keep existing shared Button call sites compatible while imposing the same visual
 * treatment across Ask and Query. Icon sizes map to the matching square control;
 * other sizes share DD's 40px minimum target.
 */
export function RagActionButton({
  variant,
  size,
  iconOnly,
  ...props
}: SharedButtonProps & { iconOnly?: boolean }): JSX.Element {
  void variant;
  return (
    <ActionButton
      iconOnly={
        iconOnly ?? (size === "icon" || size === "icon-sm" || size === "icon-lg" || size === "icon-xs")
      }
      {...props}
    />
  );
}
