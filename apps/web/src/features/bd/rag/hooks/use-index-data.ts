"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { cancelRagIndex, getRagCapabilities, getRagIndex, startRagIndex, wipeRagIndex } from "../index-api";
import { ragIndexStatusSchema } from "../index-entities";
import { RAG_INDEX_REFRESH_MS } from "../params";

export function useRagIndex(bookId: string, runId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["bd", "rag", "index", bookId, runId],
    queryFn: () => getRagIndex(bookId, runId),
    enabled,
    retry: false,
    select: (data) => ragIndexStatusSchema.parse(data),
    refetchInterval: (query) => {
      const parsed = ragIndexStatusSchema.safeParse(query.state.data);
      return !parsed.success || ["not_started", "queued", "running"].includes(parsed.data.status)
        ? RAG_INDEX_REFRESH_MS
        : false;
    },
  });
}

export function useRagCapabilities() {
  return useQuery({
    queryKey: ["bd", "rag", "capabilities"],
    queryFn: getRagCapabilities,
    retry: false,
  });
}

export function useRagIndexActions(bookId: string, runId: string) {
  const queryClient = useQueryClient();
  const queryKey = ["bd", "rag", "index", bookId, runId];
  const refresh = () => void queryClient.invalidateQueries({ queryKey }, { cancelRefetch: false });
  const start = useMutation({
    mutationFn: () => startRagIndex(bookId, runId),
    onSuccess: (status) => {
      queryClient.setQueryData(queryKey, status);
      refresh();
    },
    onError: refresh,
  });
  const cancel = useMutation({
    mutationFn: () => cancelRagIndex(bookId, runId),
    onSuccess: (status) => {
      queryClient.setQueryData(queryKey, status);
      refresh();
    },
    onError: refresh,
  });
  const wipe = useMutation({
    mutationFn: () => wipeRagIndex(bookId, runId),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey });
      refresh();
    },
    onError: refresh,
  });
  return { start, cancel, wipe };
}
