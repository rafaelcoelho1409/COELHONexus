"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { SearchProviderId } from "../params";
import {
  chooseDefaultSearchProvider,
  readSearchSettings,
  saveSearchProvider,
  searchSettingsQueryKey,
  testSearchProvider,
} from "../search-api";

export function useSearchConnections() {
  const queryClient = useQueryClient();
  const queryKey = searchSettingsQueryKey();
  const query = useQuery({
    queryKey,
    retry: false,
    queryFn: ({ signal }) => readSearchSettings(signal),
  });
  const save = useMutation({
    mutationFn: ({
      providerId,
      apiKey,
      endpoint,
    }: {
      providerId: SearchProviderId;
      apiKey?: string;
      endpoint?: string;
    }) => saveSearchProvider(providerId, apiKey, endpoint),
    onSuccess: (settings) => queryClient.setQueryData(queryKey, settings),
  });
  const setDefault = useMutation({
    mutationFn: (providerId: SearchProviderId | null) => chooseDefaultSearchProvider(providerId),
    onSuccess: (settings) => queryClient.setQueryData(queryKey, settings),
  });
  const test = useMutation({
    mutationFn: (providerId: SearchProviderId) => testSearchProvider(providerId),
    onSettled: () => queryClient.invalidateQueries({ queryKey }, { cancelRefetch: false }),
  });

  return { query, save, setDefault, test };
}
