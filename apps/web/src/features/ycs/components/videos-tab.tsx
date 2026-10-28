"use client";

/** Source · Videos — paste many video IDs/URLs (or drop a .txt/.csv), Fetch to preview their metadata in
 * the picker, then dispatch a picked subset — or everything pasted — through the videos pipeline. */

import type { DragEvent, FormEvent } from "react";
import { useRef, useState } from "react";

import { Button } from "@/shared/ui/button";
import { Label } from "@/shared/ui/label";

import { previewVideos, queueVideosPipeline } from "../api";
import { fmtCount, parseLangs, parseTextareaIds } from "../domain";
import { useBrowserPref } from "../hooks/use-browser-pref";
import { useDispatchPipeline } from "../hooks/use-dispatch-pipeline";
import { pickerStatus, usePicker } from "../hooks/use-picker";
import { VIDEOS_BUFFER_STORAGE_KEY } from "../params";
import {
  IngestAllButton,
  OptionsBar,
  StartIngestionButton,
  StatusLine,
  TranscriptOptions,
} from "./options-bar";
import { PickerView } from "./picker-view";

const NO_IDS_MESSAGE = "Paste at least one valid YouTube video ID or URL.";

export function VideosTab({ initialIds }: { initialIds: string[] }) {
  const [buffer, setBuffer] = useBrowserPref(VIDEOS_BUFFER_STORAGE_KEY, "");
  // The routed batch is shown until the user edits the paste — then it becomes part of the buffer.
  const [consumed, setConsumed] = useState(initialIds.length === 0);
  const [status, setStatus] = useState<string | null>(null);
  const [include, setInclude] = useState(true);
  const [langs, setLangs] = useState("");
  const [fetched, setFetched] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const routedText = consumed ? "" : initialIds.join("\n");
  const value = buffer
    ? `${buffer}${routedText ? `${buffer.endsWith("\n") ? "" : "\n"}${routedText}` : ""}`
    : routedText;
  const ids = parseTextareaIds(value);

  const picker = usePicker((offset, limit) => previewVideos(ids, offset, limit));
  const dispatch = useDispatchPipeline();

  const onChange = (next: string) => {
    setConsumed(true);
    setBuffer(next);
  };

  const onDrop = async (ev: DragEvent<HTMLTextAreaElement>): Promise<void> => {
    ev.preventDefault();
    const file = ev.dataTransfer?.files?.[0];
    if (!file) return;
    if (!/\.(txt|csv)$/i.test(file.name)) {
      setStatus("Drop a .txt or .csv file.");
      return;
    }
    const text = await file.text();
    setConsumed(true);
    setBuffer(value ? `${value}\n${text}` : text);
  };

  const onFetch = (ev: FormEvent<HTMLFormElement>) => {
    ev.preventDefault();
    if (!ids.length) {
      setStatus(NO_IDS_MESSAGE);
      return;
    }
    setStatus(null);
    void picker
      .refetch()
      .then(() => setFetched(true))
      .catch(() => undefined);
  };

  const langsList = parseLangs(langs);

  const dispatchIds = async (videoIds: string[]): Promise<void> => {
    if (!videoIds.length) {
      setStatus(NO_IDS_MESSAGE);
      return;
    }
    const ok = await dispatch.dispatch(() =>
      queueVideosPipeline({
        video_ids: videoIds,
        include_transcription: include,
        transcription_languages: langsList,
      }),
    );
    if (ok) {
      setConsumed(true);
      setBuffer("");
    }
  };

  const ingestAllLabel = picker.total != null ? `Ingest all ${fmtCount(picker.total)}` : "Ingest all";

  return (
    <div>
      <form onSubmit={onFetch}>
        <Label htmlFor="ycs-videos-input">Video IDs or URLs</Label>
        <textarea
          ref={inputRef}
          id="ycs-videos-input"
          value={value}
          onChange={(e) => onChange(e.currentTarget.value)}
          onDrop={(e) => void onDrop(e)}
          onDragOver={(e) => e.preventDefault()}
          rows={4}
          placeholder="One video ID or URL per line — watch links, youtu.be links or bare IDs. Drop a .txt/.csv file to append."
          autoComplete="off"
          className="mt-1 w-full rounded-sm border border-border bg-background px-2.5 py-2 font-mono text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
        />
        <div className="mt-2 flex justify-end">
          <Button type="submit" disabled={dispatch.pending}>
            {dispatch.pending ? "Fetching…" : "Fetch videos"}
          </Button>
        </div>
      </form>
      {fetched ? (
        <div className="mt-4">
          <PickerView picker={picker} />
        </div>
      ) : null}
      <OptionsBar>
        <TranscriptOptions
          idPrefix="ycs-videos"
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
          onClick={() => void dispatchIds(parseTextareaIds(value))}
        />
        <StartIngestionButton
          selectedCount={picker.selected.size}
          pending={dispatch.pending}
          onClick={() => void dispatchIds(Array.from(picker.selected))}
        />
      </OptionsBar>
    </div>
  );
}
