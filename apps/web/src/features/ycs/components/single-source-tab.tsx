"use client";

/** The shared shape of the Channel and Playlist tabs: paste ONE source, a claim card replaces the input
 * once it parses, Fetch lists its videos in the picker, and the sticky bar offers the two-mode choice —
 * dispatch the picked subset through the videos pipeline, or ingest the whole container at once. */

import type { FormEvent, JSX } from "react";
import { useRef, useState } from "react";

import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";
import type { PipelineQueued } from "../api";
import { fmtCount, type ParsedSource, parseLangs, unfurlState } from "../domain";
import { useDispatchPipeline } from "../hooks/use-dispatch-pipeline";
import { type PickerFetch, pickerStatus, usePicker } from "../hooks/use-picker";
import {
  IngestAllButton,
  OptionsBar,
  StartIngestionButton,
  StatusLine,
  TranscriptOptions,
} from "./options-bar";
import { PickerView } from "./picker-view";
import { UrlPreview } from "./url-preview";

type SingleSourceTabProps = {
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
};

export function SingleSourceTab({
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
}: SingleSourceTabProps): JSX.Element {
  const [identity, setIdentity] = useState(initialIdentity ?? "");
  // The claim card replaces the input only once the paste is committed (on submit or on blur) —
  // swapping while typing would rip the field away mid-keystroke.
  const [committed, setCommitted] = useState(initialIdentity != null);
  const [status, setStatus] = useState<string | null>(routedWarn);
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

  const reset = () => {
    setIdentity("");
    setCommitted(false);
    setStatus(null);
    inputRef.current?.focus();
  };

  const onFetch = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    const text = (ev.currentTarget.elements.namedItem("source") as HTMLInputElement | null)?.value ?? "";
    if (!text.trim()) {
      setStatus(emptyMessage);
      return;
    }
    const parsed = parser(text);
    if (!parsed) {
      setStatus("Doesn't look like a YouTube URL or ID.");
      return;
    }
    setIdentity(text);
    setCommitted(true);
    setStatus(null);
    void picker
      .refetch()
      .then(() => setFetched(true))
      .catch(() => undefined);
  };

  const langsList = parseLangs(langs);

  const startSelected = async () => {
    const ids = Array.from(picker.selected);
    if (!ids.length) return;
    const ok = await dispatch.dispatch(() => queueSubset(ids, include, langsList));
    if (ok) setFetched(true);
  };

  const startAll = async () => {
    if (!resolved) return;
    await dispatch.dispatch(() => queueAll(resolved.identity, include, langsList));
  };

  const selectedCount = picker.selected.size;
  const ingestAllLabel = picker.total != null ? `Ingest all ${fmtCount(picker.total)}` : "Ingest all";

  return (
    <div>
      <form onSubmit={onFetch} className="flex flex-wrap items-end gap-x-3 gap-y-2">
        {swapped ? (
          <div className="min-w-0 flex-1">
            <Label htmlFor={`${idPrefix}-source`} className="mb-1 text-xs text-muted-foreground">
              {inputLabel}
            </Label>
            <UrlPreview state={preview} onReset={reset} />
            <input type="hidden" name="source" id={`${idPrefix}-source`} value={identity} />
          </div>
        ) : (
          <div className="min-w-0 flex-1">
            <Label htmlFor={`${idPrefix}-source`} className="mb-1 text-xs text-muted-foreground">
              {inputLabel}
            </Label>
            {preview.state === "invalid" ? <UrlPreview state={preview} onReset={reset} /> : null}
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
            />
          </div>
        )}
        <Button type="submit" disabled={dispatch.pending || preview.state === "invalid"}>
          {dispatch.pending ? "Fetching…" : "Fetch videos"}
        </Button>
      </form>
      {fetched ? (
        <div className="mt-4">
          <PickerView picker={picker} />
        </div>
      ) : null}
      <OptionsBar>
        <TranscriptOptions
          idPrefix={idPrefix}
          include={include}
          onInclude={setInclude}
          langs={langs}
          onLangs={setLangs}
        />
        <StatusLine text={dispatch.error ?? status ?? picker.error ?? pickerStatus(picker)} />
        <IngestAllButton
          label={ingestAllLabel}
          pending={dispatch.pending}
          disabled={picker.items.length === 0}
          onClick={() => void startAll()}
        />
        <StartIngestionButton
          selectedCount={selectedCount}
          pending={dispatch.pending}
          onClick={() => void startSelected()}
        />
      </OptionsBar>
    </div>
  );
}
