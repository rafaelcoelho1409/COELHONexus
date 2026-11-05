"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";

import { getBdIngestionBookIdPagesPageNoQueryKey, getBdIngestionBookIdQueryKey } from "@/shared/api";
import { useEventStream } from "@/shared/sse";

import { agenticEventsUrl, fetchAgenticUsage } from "../api";
import type {
  AgenticEvent,
  AgenticProgress,
  AgenticRunStatus,
  AgenticUsage,
  BookSnapshot,
} from "../entities";
import { agenticEventSchema } from "../entities";

type EventState = {
  runId: string;
  status: AgenticRunStatus["status"];
  progress: AgenticProgress;
  error: string | null;
  cancelRequested: boolean;
};

const parseEvent = (data: unknown): AgenticEvent => agenticEventSchema.parse(data);
const terminalEvent = (event: AgenticEvent) =>
  event.kind === "run_done" || event.kind === "run_failed" || event.kind === "run_cancelled";

export type AgenticRunController = {
  run: AgenticRunStatus | null;
  status: AgenticRunStatus["status"] | null;
  progress: AgenticProgress | null;
  streaming: boolean;
  streamStatus: ReturnType<typeof useEventStream<AgenticEvent>>["status"];
  error: string | null;
  cancelRequested: boolean;
  restart: () => void;
  markCanceling: () => void;
};

export function useAgenticRun(bookId: string, snapshot: BookSnapshot): AgenticRunController {
  const queryClient = useQueryClient();
  const [eventState, setEventState] = useState<EventState | null>(null);
  const initial = snapshot.agentic;
  const runId = initial?.agentic_run_id ?? null;
  const restart = useCallback(() => {
    if (!initial) return;
    setEventState({
      runId: initial.agentic_run_id,
      status: "queued",
      progress: initial.progress,
      error: null,
      cancelRequested: false,
    });
  }, [initial]);
  const markCanceling = useCallback(() => {
    if (!initial) return;
    setEventState((previous) => ({
      runId: initial.agentic_run_id,
      status: previous?.runId === initial.agentic_run_id ? previous.status : initial.status,
      progress: previous?.runId === initial.agentic_run_id ? previous.progress : initial.progress,
      error: previous?.runId === initial.agentic_run_id ? previous.error : initial.error,
      cancelRequested: true,
    }));
  }, [initial]);
  const activeEventState = eventState?.runId === runId ? eventState : null;
  const status = activeEventState?.status ?? initial?.status ?? null;
  const progress = activeEventState?.progress ?? initial?.progress ?? null;
  const streaming = runId !== null && (status === "queued" || status === "running");
  const options = useMemo(
    () =>
      streaming && runId
        ? {
            url: agenticEventsUrl(bookId, runId),
            parse: parseEvent,
            isTerminal: terminalEvent,
          }
        : null,
    [bookId, runId, streaming],
  );

  const { status: streamStatus } = useEventStream(options, (event) => {
    if (!runId) return;
    setEventState((previous) => {
      const current =
        previous?.runId === runId
          ? previous
          : {
              runId,
              status: initial?.status ?? "queued",
              progress: initial?.progress ?? {
                current_node: null,
                node_status: {},
                audited_pages: [],
                audit_warning_pages: [],
                processed_pages: [],
                active_audit_pages: [],
                active_repair_pages: [],
              },
              error: null,
              cancelRequested: false,
            };
      const nextStatus =
        event.kind === "run_done"
          ? "done"
          : event.kind === "run_failed"
            ? "failed"
            : event.kind === "run_cancelled"
              ? "cancelled"
            : event.kind === "run_queued"
              ? "queued"
              : event.kind === "run_cancelling"
                ? current.status
                : "running";
      return {
        ...current,
        status: nextStatus,
        progress: event.progress ?? current.progress,
        cancelRequested:
          event.kind === "run_cancelling"
            ? true
            : terminalEvent(event) || event.kind === "run_queued"
              ? false
              : current.cancelRequested,
        error:
          event.kind === "run_queued"
            ? null
            : event.kind === "run_failed"
              ? (event.error ?? "Agentic processing failed.")
              : event.kind === "run_cancelled"
                ? null
                : current.error,
      };
    });

    if (event.kind === "page_repair" && event.status === "done" && event.page !== undefined) {
      void queryClient.invalidateQueries({
        queryKey: getBdIngestionBookIdPagesPageNoQueryKey({
          path: { book_id: bookId, page_no: event.page },
        }),
      });
    }
    if (terminalEvent(event)) {
      void queryClient.invalidateQueries({
        queryKey: getBdIngestionBookIdQueryKey({ path: { book_id: bookId } }),
      });
    }
  });

  return {
    run: initial,
    status,
    progress,
    streaming,
    streamStatus,
    error: activeEventState ? activeEventState.error : (initial?.error ?? null),
    cancelRequested: activeEventState?.cancelRequested ?? false,
    restart,
    markCanceling,
  };
}

export function useAgenticUsage(bookId: string, agenticRunId: string | null, live: boolean, enabled = true) {
  const query = useQuery<AgenticUsage>({
    queryKey: ["bd-agentic-usage", bookId, agenticRunId],
    queryFn: () => fetchAgenticUsage(bookId, agenticRunId ?? ""),
    enabled: enabled && agenticRunId !== null,
    refetchInterval: live ? 3_000 : false,
    retry: false,
  });
  return query;
}
