"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  getBdDistillBookIdRunsOptions,
  getBdDistillBookIdRunsQueryKey,
  getBdDistillBookIdRunsRunIdQueryKey,
  getBdDistillBookIdRunsRunIdReportOptions,
  getBdDistillBookIdRunsRunIdReportQueryKey,
  getBdDistillBookIdRunsRunIdRagIndexOptions,
  getBdDistillBookIdRunsRunIdRagIndexQueryKey,
  getBdDistillCapabilitiesOptions,
  getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionOptions,
  getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionQueryKey,
} from "@/shared/api";
import { useEventStream } from "@/shared/sse";

import {
  cancelDistillRun,
  distillEventsUrl,
  getDistillRagIndex,
  resumeDistillRun,
  startDistillRun,
  wipeDistillRun,
  retryDistillRagIndex,
  saveContentSelection,
} from "../api";
import {
  initialDistillRunState,
  isDistillLive,
  isDistillTerminalEvent,
  reduceDistillRunState,
} from "../domain/run-state";
import {
  type DistillRun,
  contentSelectionSchema,
  distillCapabilitiesSchema,
  distillEventSchema,
  distillRunListSchema,
  learningGuideSchema,
  ragIndexStatusSchema,
} from "../entities";
import { DISTILL_REFRESH_MS } from "../params";

export function useDistillCapabilities() {
  return useQuery({
    ...getBdDistillCapabilitiesOptions(),
    retry: false,
    select: (data) => distillCapabilitiesSchema.parse(data),
  });
}

export function useDistillRuns(bookId: string) {
  return useQuery({
    ...getBdDistillBookIdRunsOptions({ path: { book_id: bookId } }),
    retry: false,
    select: (data) => distillRunListSchema.parse(data),
    refetchInterval: (query) => {
      const parsed = distillRunListSchema.safeParse(query.state.data);
      return parsed.success && parsed.data.some((run) => isDistillLive(run.status))
        ? DISTILL_REFRESH_MS
        : false;
    },
  });
}

export function useLearningGuide(bookId: string, runId: string, enabled: boolean) {
  return useQuery({
    ...getBdDistillBookIdRunsRunIdReportOptions({ path: { book_id: bookId, run_id: runId } }),
    enabled,
    retry: false,
    select: (data) => learningGuideSchema.parse(data),
  });
}

export function useRagIndex(bookId: string, runId: string, enabled: boolean) {
  return useQuery({
    ...getBdDistillBookIdRunsRunIdRagIndexOptions({ path: { book_id: bookId, run_id: runId } }),
    queryFn: () => getDistillRagIndex(bookId, runId),
    enabled,
    retry: false,
    select: (data) => ragIndexStatusSchema.parse(data),
    refetchInterval: (query) => {
      const parsed = ragIndexStatusSchema.safeParse(query.state.data);
      return !parsed.success || ["not_started", "queued", "running"].includes(parsed.data.status)
        ? DISTILL_REFRESH_MS
        : false;
    },
  });
}

export function useRetryRagIndex(bookId: string, runId: string) {
  const queryClient = useQueryClient();
  const queryKey = getBdDistillBookIdRunsRunIdRagIndexQueryKey({ path: { book_id: bookId, run_id: runId } });
  return useMutation({
    mutationFn: () => retryDistillRagIndex(bookId, runId),
    onSuccess: (status) => {
      queryClient.setQueryData(queryKey, status);
      void queryClient.invalidateQueries({ queryKey }, { cancelRefetch: false });
    },
    onError: () => void queryClient.invalidateQueries({ queryKey }),
  });
}

export function useContentSelection(bookId: string, agenticRunId: string | null) {
  const options = getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionOptions({
    path: { book_id: bookId, agentic_run_id: agenticRunId ?? "" },
  });
  return useQuery({
    ...options,
    enabled: agenticRunId !== null,
    retry: false,
    select: (data) => contentSelectionSchema.parse(data),
  });
}

export function useSaveContentSelection(bookId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      agenticRunId,
      expectedRevision,
      overrides,
    }: {
      agenticRunId: string;
      expectedRevision: number;
      overrides: { page_number: number; disposition: "include" | "exclude" }[];
    }) => saveContentSelection(bookId, agenticRunId, expectedRevision, overrides),
    onSuccess: (selection) => {
      const queryKey = getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionQueryKey({
        path: { book_id: bookId, agentic_run_id: selection.agentic_run_id },
      });
      queryClient.setQueryData(queryKey, selection);
      void queryClient.invalidateQueries({ queryKey }, { cancelRefetch: false });
    },
    onError: (_error, variables) => {
      const queryKey = getBdIngestionBookIdAgenticRunsAgenticRunIdContentSelectionQueryKey({
        path: { book_id: bookId, agentic_run_id: variables.agenticRunId },
      });
      void queryClient.invalidateQueries({ queryKey });
    },
  });
}

export function useDistillRunStream(bookId: string, run: DistillRun | null) {
  const queryClient = useQueryClient();
  const [eventState, setEventState] = useState<ReturnType<typeof initialDistillRunState> | null>(null);
  const current = run ? (eventState?.runId === run.run_id ? eventState : initialDistillRunState(run)) : null;
  const runId = run?.run_id ?? null;
  const runStatus = current?.status ?? null;
  const options = useMemo(
    () =>
      runId && runStatus && isDistillLive(runStatus)
        ? {
            url: distillEventsUrl(bookId, runId),
            parse: (data: unknown) => distillEventSchema.parse(data),
            isTerminal: isDistillTerminalEvent,
          }
        : null,
    [bookId, runId, runStatus],
  );
  const stream = useEventStream(options, (event) => {
    if (!run) return;
    setEventState((previous) => {
      const state = previous?.runId === run.run_id ? previous : initialDistillRunState(run);
      return reduceDistillRunState(state, event);
    });
    if (isDistillTerminalEvent(event)) {
      void queryClient.invalidateQueries({
        queryKey: getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }),
      });
      void queryClient.invalidateQueries({
        queryKey: getBdDistillBookIdRunsRunIdQueryKey({ path: { book_id: bookId, run_id: run.run_id } }),
      });
      void queryClient.invalidateQueries({
        queryKey: getBdDistillBookIdRunsRunIdReportQueryKey({
          path: { book_id: bookId, run_id: run.run_id },
        }),
      });
    }
  });
  return { ...stream, state: current };
}

export function useDistillActions(bookId: string) {
  const queryClient = useQueryClient();
  const refresh = () => {
    void queryClient.invalidateQueries(
      { queryKey: getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }) },
      { cancelRefetch: false },
    );
  };
  const start = useMutation({
    mutationFn: (ingestionAiRunId: string) => startDistillRun(bookId, ingestionAiRunId),
    onSuccess: refresh,
    onError: refresh,
  });
  const resume = useMutation({
    mutationFn: (runId: string) => resumeDistillRun(bookId, runId),
    onSuccess: refresh,
    onError: refresh,
  });
  const cancel = useMutation({
    mutationFn: (runId: string) => cancelDistillRun(bookId, runId),
    onSuccess: refresh,
    onError: refresh,
  });
  const wipe = useMutation({
    mutationFn: (runId: string) => wipeDistillRun(bookId, runId),
    onSuccess: (_result, runId) => {
      queryClient.setQueryData(
        getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }),
        (current: unknown) => {
          const parsed = distillRunListSchema.safeParse(current);
          return parsed.success ? parsed.data.filter((run) => run.run_id !== runId) : undefined;
        },
      );
      queryClient.removeQueries({
        queryKey: getBdDistillBookIdRunsRunIdQueryKey({ path: { book_id: bookId, run_id: runId } }),
      });
      queryClient.removeQueries({
        queryKey: getBdDistillBookIdRunsRunIdReportQueryKey({ path: { book_id: bookId, run_id: runId } }),
      });
      refresh();
    },
    onError: refresh,
  });
  return { start, resume, cancel, wipe };
}
