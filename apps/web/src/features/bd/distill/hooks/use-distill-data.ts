"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  getBdDistillBookIdRunsOptions,
  getBdDistillBookIdRunsQueryKey,
  getBdDistillBookIdRunsRunIdQueryKey,
  getBdDistillBookIdRunsRunIdReportOptions,
  getBdDistillBookIdRunsRunIdReportQueryKey,
} from "@/shared/api";
import { useEventStream } from "@/shared/sse";

import { cancelDistillRun, distillEventsUrl, resumeDistillRun, startDistillRun } from "../api";
import {
  initialDistillRunState,
  isDistillLive,
  isDistillTerminalEvent,
  reduceDistillRunState,
} from "../domain/run-state";
import { type DistillRun, distillEventSchema, distillRunListSchema, learningGuideSchema } from "../entities";
import { DISTILL_REFRESH_MS } from "../params";

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

export function useDistillRunStream(bookId: string, run: DistillRun) {
  const queryClient = useQueryClient();
  const [eventState, setEventState] = useState<ReturnType<typeof initialDistillRunState> | null>(null);
  const current = eventState?.runId === run.run_id ? eventState : initialDistillRunState(run);
  const options = useMemo(
    () =>
      isDistillLive(current.status)
        ? {
            url: distillEventsUrl(bookId, run.run_id),
            parse: (data: unknown) => distillEventSchema.parse(data),
            isTerminal: isDistillTerminalEvent,
          }
        : null,
    [bookId, current.status, run.run_id],
  );
  const stream = useEventStream(options, (event) => {
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
    void queryClient.invalidateQueries({
      queryKey: getBdDistillBookIdRunsQueryKey({ path: { book_id: bookId } }),
    });
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
  return { start, resume, cancel };
}
