/** One framework in the catalog: its logo(s), name, category/tier, and whether it is already ingested. */

import { cn } from "cn";

import { Badge } from "@/shared/ui/badge";

import { tileLogos, tileName, tileTier } from "../domain/catalog";
import type { CatalogEntry } from "../entities";
import { LogoStrip } from "./logo-strip";

type CatalogTileProps = {
  entry: CatalogEntry;
  ingested: boolean;
  selected: boolean;
  onPick: () => void;
};

export function CatalogTile({ entry, ingested, selected, onPick }: CatalogTileProps) {
  return (
    <button
      type="button"
      onClick={onPick}
      aria-pressed={selected}
      className={cn(
        "flex h-full w-full flex-col rounded-md border p-3 text-left transition-colors hover:border-foreground/40",
        selected ? "border-primary bg-primary/5" : "border-border bg-card",
      )}
    >
      <span aria-hidden="true" className="flex h-8 w-full items-center gap-2 overflow-hidden">
        <LogoStrip logos={tileLogos(entry)} name={tileName(entry)} />
      </span>
      <span className="mt-3 flex flex-wrap items-start justify-between gap-x-2 gap-y-1">
        <span className="min-w-0 break-words text-sm font-medium">{tileName(entry)}</span>
        {ingested ? <Badge className="bg-success/10 text-success-text">Ingested</Badge> : null}
      </span>
      <span className="mt-1 text-xs text-muted-foreground">
        {entry.category ?? "Other"} · tier {tileTier(entry)}
      </span>
    </button>
  );
}
