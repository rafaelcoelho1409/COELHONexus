"use client";

/** The shared shape of the Channel and Playlist tabs: paste ONE source, a claim card replaces the input once it parses, Fetch
 * lists its videos in the picker, and the ingest bar offers the two-mode choice — dispatch the picked subset through the videos
 * pipeline, or ingest the whole container at once (after asking, when it is a lot). */

import type { FormEvent, JSX } from "react";
import { useRef, useState } from "react";

import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import type { PipelineQueued } from "../api";
import { fmtCount, fmtInt, type ParsedSource, parseLangs, unfurlState } from "../domain";
import { useDispatchPipeline } from "../hooks/use-dispatch-pipeline";
import { useYcsOverlays } from "../hooks/use-overlays";
import { type PickerFetch, pickerStatus, usePicker } from "../hooks/use-picker";
import { INGEST_ALL_CONFIRM_ABOVE, type SourceMode } from "../params";
import { ModeHint } from "./mode-hint";
import { IngestBar, optionsSummary, StatusLine, TranscriptOptions } from "./options-bar";
import { PickerView } from "./picker-view";
import { UrlPreview } from "./url-preview";

type SingleSourceTabProps = {
  /** Which tab this is, so a paste that belongs to another one can be suggested there. */
  mode: "channel" | "playlist";
  idPrefix: string;
  inputLabel: string;
  placeholder: string;
  parser: (text: string) => ParsedSource | null;
  emptyMessage: string;
  fetchVideos: (identity: string) => PickerFetch;
  queueSubset: (
    ids: string[],
    include: boolean,
    langs: Array<string> | null,
  ) => Promise<PipelineQueued | null>;
  queueAll: (
    identity: string,
    include: boolean,
    langs: Array<string> | null,
  ) => Promise<PipelineQueued | null>;
  initialIdentity: string | null;
  routedWarn: string | null;
  onSwitch: (mode: Exclude<SourceMode, "search">, text: string) => void;
};

export function SingleSourceTab({
  mode,
  idPrefix,
  inputLabel,
  placeholder,
  parser,
  emptyMessage,
  fetchVideos,
  queueSubset,
  queueAll,
  initialIdentity,
  routedWarn,
  onSwitch,
}: SingleSourceTabProps): JSX.Element {
  const [identity, setIdentity] = useState(initialIdentity ?? "");
  // The claim card replaces the input only once the paste is committed (on submit or on blur) —
  // swapping while typing would rip the field away mid-keystroke.
  const [committed, setCommitted] = useState(initialIdentity != null);
  // What the reader should know about the paste itself: the routed-batch notice, or why it was rejected.
  const [note, setNote] = useState<{ text: string; error: boolean } | null>(
    routedWarn ? { text: routedWarn, error: false } : null,
  );
  const [include, setInclude] = useState(true);
  const [langs, setLangs] = useState("");
  const [fetched, setFetched] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const preview = unfurlState(identity, parser);
  const resolved = preview.state === "parsed" ? preview.parsed : null;
  const swapped = committed && preview.state === "parsed";

  const picker = usePicker(
    resolved ? fetchVideos(resolved.identity) : () => Promise.reject(new Error("no source")),
  );
  const dispatch = useDispatchPipeline();
  const overlays = useYcsOverlays();

  const reset = () => {
    setIdentity("");
    setCommitted(false);
    setNote(null);
    inputRef.current?.focus();
  };

  const onFetch = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    const text = (ev.currentTarget.elements.namedItem("source") as HTMLInputElement | null)?.value ?? "";
    if (!text.trim()) {
      setNote({ text: emptyMessage, error: true });
      return;
    }
    const parsed = parser(text);
    if (!parsed) {
      setNote({ text: "Doesn't look like a YouTube URL or ID.", error: true });
      return;
    }
    setIdentity(text);
    setCommitted(true);
    setNote(null);
    void picker
      .refetch()
      .then(() => setFetched(true))
      .catch(() => undefined);
  };

  const langsList = parseLangs(langs);

  const startSelected = async () => {
    const ids = Array.from(picker.selected);
    if (!ids.length) return;
    await dispatch.dispatch(() => queueSubset(ids, include, langsList));
  };

  const startAll = async () => {
    if (!resolved) return;
    if (picker.total != null && picker.total > INGEST_ALL_CONFIRM_ABOVE) {
      const confirmed = await overlays.confirm({
        title: `Ingest all ${fmtInt(picker.total)} videos?`,
        message: `Queues every video of “${picker.title ?? resolved.display}”, not only the ones listed below. ${optionsSummary(include, langsList)}.`,
        confirmLabel: `Ingest ${fmtInt(picker.total)} videos`,
        tone: "primary",
      });
      if (!confirmed) return;
    }
    await dispatch.dispatch(() => queueAll(resolved.identity, include, langsList));
  };

  const ingestAllLabel = picker.total != null ? `Ingest all ${fmtCount(picker.total)}` : "Ingest all";
  const problem = dispatch.error ?? picker.error;

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={onFetch} className="flex flex-col gap-3 rounded-md border border-border bg-card p-3">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <div className="min-w-0 flex-[1_1_20rem]">
            <Label
              htmlFor={`${idPrefix}-source`}
              className="mb-1.5 block text-xs font-medium text-muted-foreground"
            >
              {inputLabel}
            </Label>
            {swapped ? (
              <>
                <UrlPreview state={preview} onReset={reset} />
                <input type="hidden" name="source" id={`${idPrefix}-source`} value={identity} />
              </>
            ) : (
              <>
                {preview.state === "invalid" ? (
                  <div className="mb-2">
                    <UrlPreview state={preview} onReset={reset} expected={mode} />
                  </div>
                ) : null}
                <Input
                  ref={inputRef}
                  id={`${idPrefix}-source`}
                  name="source"
                  value={identity}
                  onChange={(e) => setIdentity(e.currentTarget.value)}
                  onBlur={() => {
                    if (preview.state === "parsed") setCommitted(true);
                  }}
                  placeholder={placeholder}
                  autoComplete="off"
                  spellCheck={false}
                  className="h-10 text-sm"
                />
              </>
            )}
          </div>
          <Button
            type="submit"
            size="lg"
            className="h-10"
            disabled={picker.loading || preview.state === "invalid"}
          >
            {picker.loading ? "Fetching…" : "Fetch videos"}
          </Button>
        </div>

        <ModeHint text={identity} own={mode} onSwitch={onSwitch} />

        <TranscriptOptions
          idPrefix={idPrefix}
          include={include}
          onInclude={setInclude}
          langs={langs}
          onLangs={setLangs}
        />
        <StatusLine
          text={problem ?? note?.text ?? pickerStatus(picker)}
          error={problem !== null || note?.error === true}
        />
      </form>

      {fetched ? (
        <>
          <PickerView picker={picker} fallbackTitle={resolved?.display ?? inputLabel} />
          <IngestBar
            selectedCount={picker.selected.size}
            ingestAllLabel={ingestAllLabel}
            summary={optionsSummary(include, langsList)}
            pending={dispatch.pending}
            canIngestAll={picker.items.length > 0}
            onStart={() => void startSelected()}
            onIngestAll={() => void startAll()}
          />
        </>
      ) : null}
    </div>
  );
}
