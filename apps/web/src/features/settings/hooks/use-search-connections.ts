"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { chooseDefaultSearchProvider, readSearchSettings, saveSearchProvider, searchSettingsQueryKey, testSearchProvider } from "../search-api";
import type { SearchProviderId } from "../params";

export function useSearchConnections() {
  const queryClient = useQueryClient();
  const queryKey = searchSettingsQueryKey();
  const query = useQuery({
    queryKey,
    retry: false,
    queryFn: ({ signal }) => readSearchSettings(signal),
  });
  const save = useMutation({
    mutationFn: ({ providerId, apiKey }: { providerId: SearchProviderId; apiKey: string }) =>
      saveSearchProvider(providerId, apiKey),
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
