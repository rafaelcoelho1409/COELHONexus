"use client";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { buttonVariants } from "@/shared/ui/button-variants";

import { useConnection } from "../hooks/use-connection";
import type { ConnectionKind } from "../params";
import { ConnectionForm } from "./connection-form";
import { MigrationBanner } from "./migration-banner";

/** The saved connection loads here; the form remounts whenever the saved view changes. */
export function ConnectionCard({
  kind,
  title,
  intro,
}: {
  kind: ConnectionKind;
  title: string;
  intro: string;
}) {
  const { query, save, test } = useConnection(kind);
  const view = query.data;
  const heading = `connection-${kind}-heading`;

  return (
    <section aria-labelledby={heading} className="border border-border bg-card">
      <header className="border-b border-border px-5 py-4 sm:px-6">
        <h2 id={heading} className="text-xl font-semibold tracking-tight">
          {title}
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">{intro}</p>
      </header>
      <div className="px-5 py-5 sm:px-6">
        {query.isPending && <p className="text-sm text-muted-foreground">Loading…</p>}
        {query.isError && (
          <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <p className="text-destructive-text">
              Couldn&apos;t load the saved settings — {apiErrorMessage(query.error)}
            </p>
            <button
              type="button"
              onClick={() => void query.refetch()}
              disabled={query.isRefetching}
              className={buttonVariants({ variant: "link", size: "sm" })}
            >
              Retry
            </button>
          </div>
        )}
        {view && (
          <>
            <ConnectionForm
              kind={kind}
              initial={view}
              save={save}
              test={test}
              key={`${view.url}|${view.model}|${view.has_key}|${view.last4}`}
            />
            {kind === "embedding" && (
              <div className="mt-6">
                <MigrationBanner />
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
