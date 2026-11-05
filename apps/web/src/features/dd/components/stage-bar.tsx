"use client";

import type { ReactNode } from "react";

import { ContextBar } from "@/shared/layout/context-bar";

import { frameworkInfo } from "../domain/ingestion";
import { useManifest } from "../hooks/use-dd-data";
import type { DdStage } from "../params";
import { FrameworkPicker } from "./framework-picker";

/** The third navbar of every stage that works on one framework: the framework picker and its facts on the left
 * ("Locust · 24 pages · 213.6 KB"), the stage's own views (`end`) on the right. A stage that already holds the
 * manifest it shows passes the facts as `info`; the others read them from the library. */
export function StageBar({
  stage,
  slug,
  info,
  end,
}: {
  stage: DdStage;
  slug: string | null;
  info?: string;
  end?: ReactNode;
}) {
  return (
    <ContextBar
      start={
        <>
          <FrameworkPicker stage={stage} currentSlug={slug} />
          {slug ? info === undefined ? <LibraryFacts slug={slug} /> : <Facts text={info} /> : null}
        </>
      }
      end={end}
    />
  );
}

function LibraryFacts({ slug }: { slug: string }) {
  const manifest = useManifest(slug);
  return <Facts text={frameworkInfo(manifest.data)} />;
}

function Facts({ text }: { text: string }) {
  return text ? <p className="min-w-0 text-sm text-muted-foreground">{text}</p> : null;
}
