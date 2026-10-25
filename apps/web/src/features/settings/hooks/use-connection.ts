"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getYcsContentEmbeddingMigrationStatusQueryKey } from "@/shared/api";

import { CONNECTION_API } from "../api";
import { type ConnectionView, connectionViewSchema } from "../entities";
import type { ConnectionKind, SaveBody } from "../params";

/** One connection (LLM or embedding): the saved view, plus save and test against the saved config. */
export function useConnection(kind: ConnectionKind) {
  const queryClient = useQueryClient();
  const api = CONNECTION_API[kind];

  const query = useQuery({
    queryKey: api.queryKey(),
    retry: false,
    queryFn: async ({ signal }) => connectionViewSchema.parse(await api.read(signal)),
  });

  const save = useMutation<ConnectionView, unknown, SaveBody>({
    mutationFn: async (body) => connectionViewSchema.parse(await api.write(body)),
    onSuccess: (view) => {
      queryClient.setQueryData(api.queryKey(), view);
      if (kind === "embedding") {
        // A different embedding model may leave already-ingested transcripts behind.
        void queryClient.invalidateQueries({ queryKey: getYcsContentEmbeddingMigrationStatusQueryKey() });
      }
    },
  });

  const test = useMutation({
    mutationFn: async () => api.parseTest(await api.test()),
  });

  return { query, save, test };
}

export type SaveMutation = ReturnType<typeof useConnection>["save"];
export type TestMutation = ReturnType<typeof useConnection>["test"];
