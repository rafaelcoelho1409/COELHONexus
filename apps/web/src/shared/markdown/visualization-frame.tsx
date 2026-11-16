"use client";

import { Maximize2 } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/shared/ui/button";

export function VisualizationFrame({
  label,
  onExpand,
  children,
}: {
  label: string;
  onExpand?: () => void;
  children: ReactNode;
}) {
  return (
    <div>
      {onExpand ? (
        <div className="md-diagram-bar">
          <span>{label}</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Expand ${label.toLowerCase()}`}
            onClick={onExpand}
          >
            <Maximize2 aria-hidden="true" />
            <span>Expand</span>
          </Button>
        </div>
      ) : null}
      {children}
    </div>
  );
}
