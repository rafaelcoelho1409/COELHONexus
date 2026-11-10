import {
  getSettingsSearch,
  getSettingsSearchQueryKey,
  postSettingsSearchProviderIdTest,
  putSettingsSearchDefault,
  putSettingsSearchProviderId,
} from "@/shared/api";

import { searchSettingsSchema, searchTestSchema } from "./entities";
import type { SearchProviderId } from "./params";

export const searchSettingsQueryKey = getSettingsSearchQueryKey;

export async function readSearchSettings(signal: AbortSignal) {
  const { data } = await getSettingsSearch({ signal, throwOnError: true });
  return searchSettingsSchema.parse(data);
}

export async function saveSearchProvider(providerId: SearchProviderId, apiKey?: string, endpoint?: string) {
  const { data } = await putSettingsSearchProviderId({
    path: { provider_id: providerId },
    body: { api_key: apiKey, endpoint },
    throwOnError: true,
  });
  return searchSettingsSchema.parse(data);
}

export async function chooseDefaultSearchProvider(providerId: SearchProviderId | null) {
  const { data } = await putSettingsSearchDefault({
    body: { provider_id: providerId },
    throwOnError: true,
  });
  return searchSettingsSchema.parse(data);
}

export async function testSearchProvider(providerId: SearchProviderId) {
  const { data } = await postSettingsSearchProviderIdTest({
    path: { provider_id: providerId },
    throwOnError: true,
  });
  return searchTestSchema.parse(data);
}
