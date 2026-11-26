"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import {
  getYcsAdminTaskTaskIdOptions,
  getYcsContentEmbeddingMigrationStatusOptions,
  getYcsContentEmbeddingMigrationStatusQueryKey,
  postYcsContentEmbeddingMigrationStart,
} from "@/shared/api";

import { isTerminalTaskState, migrationView } from "../domain";
import { type MigrationStatus, migrationStatusSchema, type TaskStatus, taskStatusSchema } from "../entities";
import { MIGRATION_POLL_MS, MIGRATION_RECHECK_MS } from "../params";

// A payload that doesn't match degrades to "nothing to show", not to an error state.
const parseStatus = (raw: unknown): MigrationStatus | null => {
  const parsed = migrationStatusSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
};

const parseTask = (raw: unknown): TaskStatus | null => {
  const parsed = taskStatusSchema.safeParse(raw);
  return parsed.success ? parsed.data : null;
};

/**
 * Status, start and progress of the embedding migration. A failed status check hides the banner; it never
 * breaks the card around it.
 */
export function useEmbeddingMigration() {
  const queryClient = useQueryClient();

  const status = useQuery({
    ...getYcsContentEmbeddingMigrationStatusOptions(),
    retry: false,
    select: parseStatus,
  });

  const view = status.data ? migrationView(status.data) : ({ kind: "hidden" } as const);
  const taskId = view.kind === "running" ? view.taskId : undefined;

  const task = useQuery({
    ...getYcsAdminTaskTaskIdOptions({ path: { task_id: taskId ?? "" } }),
    enabled: taskId !== undefined,
    retry: false,
    select: parseTask,
    refetchInterval: (current) => {
      const latest = taskStatusSchema.safeParse(current.state.data);
      return latest.success && isTerminalTaskState(latest.data.state) ? false : MIGRATION_POLL_MS;
    },
  });

  const bookState = status.data?.book_migration?.state;
  const bookTaskId =
    bookState?.status === "running" || bookState?.status === "queued" ? bookState.task_id : undefined;
  const bookTask = useQuery({
    ...getYcsAdminTaskTaskIdOptions({ path: { task_id: bookTaskId ?? "" } }),
    enabled: bookTaskId !== undefined && bookTaskId.length > 0,
    retry: false,
    select: parseTask,
    refetchInterval: (current) => {
      const latest = taskStatusSchema.safeParse(current.state.data);
      return latest.success && isTerminalTaskState(latest.data.state) ? false : MIGRATION_POLL_MS;
    },
  });

  const start = useMutation<void, unknown>({
    mutationFn: async () => {
      await postYcsContentEmbeddingMigrationStart({ throwOnError: true });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: getYcsContentEmbeddingMigrationStatusQueryKey() });
    },
  });

  // Once the worker reports a final state, re-read the status shortly after so the banner can go away.
  useEffect(() => {
    const finished =
      (task.data && isTerminalTaskState(task.data.state)) ||
      (bookTask.data && isTerminalTaskState(bookTask.data.state));
    if (!finished) return;
    const timer = setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: getYcsContentEmbeddingMigrationStatusQueryKey() });
    }, MIGRATION_RECHECK_MS);
    return () => clearTimeout(timer);
  }, [task.data, bookTask.data, queryClient]);

  return {
    status: status.data,
    view,
    start,
    task: task.data ?? undefined,
    bookTask: bookTask.data ?? undefined,
  };
}
