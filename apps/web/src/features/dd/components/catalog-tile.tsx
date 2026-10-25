/** One framework in the catalog: its logo(s), name, category/tier, and whether it is already ingested. */

import { cn } from "cn";

import { Badge } from "@/shared/ui/badge";

import { tileLogos, tileName, tileTier } from "../domain/catalog";
import type { CatalogEntry } from "../entities";

type CatalogTileProps = {
  entry: CatalogEntry;
  ingested: boolean;
  selected: boolean;
  onPick: () => void;
};

/** The logos are decorative (the name sits right below), so they carry an empty alt. They are hot-linked from
 * each project's own site, so they say nothing about us to it (`no-referrer`) and load only when scrolled to. */
function TileLogos({ entry }: { entry: CatalogEntry }) {
  const logos = tileLogos(entry);
  if (logos.length === 0) {
    return (
      <span className="grid size-8 place-items-center rounded-sm border border-border font-mono text-sm text-muted-foreground">
        {tileName(entry).slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    <>
      {logos.map((src) => (
        // biome-ignore lint/performance/noImgElement: remote logos of arbitrary size and host — next/image would need every host allow-listed
        <img // eslint-disable-line @next/next/no-img-element
          key={src}
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className="h-8 w-auto max-w-28 object-contain"
        />
      ))}
    </>
  );
}

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
        <TileLogos entry={entry} />
      </span>
      <span className="mt-3 flex items-start justify-between gap-2">
        <span className="min-w-0 break-words text-sm font-medium">{tileName(entry)}</span>
        {ingested ? <Badge className="bg-success/10 text-success-text">Ingested</Badge> : null}
      </span>
      <span className="mt-1 text-xs text-muted-foreground">
        {entry.category ?? "Other"} · tier {tileTier(entry)}
      </span>
    </button>
  );
}
