"use client";

import { useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";
import { buttonVariants } from "@/shared/ui/button-variants";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import type { SearchProvider, SearchTestResult } from "../entities";
import { useSearchConnections } from "../hooks/use-search-connections";
import { CUSTOM_SEARCH_PROVIDER_ID, SEARCH_PROVIDER_ACCOUNT_URLS, type SearchProviderId } from "../params";

export function SearchConnections() {
  const { query, save, setDefault, test } = useSearchConnections();
  const [selectedProviderId, setSelectedProviderId] = useState<SearchProviderId>(CUSTOM_SEARCH_PROVIDER_ID);
  const settings = query.data;
  const connectedProviders = settings?.providers.filter((provider) => provider.connected) ?? [];
  const selectedProvider = settings?.providers.find(
    (provider) => provider.provider_id === selectedProviderId,
  );
  const customProvider = settings?.providers.find(
    (provider) => provider.provider_id === CUSTOM_SEARCH_PROVIDER_ID,
  );
  const saveErrorProvider = save.variables?.providerId;
  const testErrorProvider = test.variables;

  return (
    <section aria-labelledby="search-connections-heading" className="border border-border bg-card">
      <header className="border-b border-border px-5 py-4 sm:px-6">
        <p className="mono-label">Search services</p>
        <h2 id="search-connections-heading" className="mt-1 text-xl font-semibold tracking-tight">
          Search
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Manage saved search connections for research features across COELHO Nexus. Any tool that integrates
          search can use them; the settings aren&apos;t limited to book distillation.
        </p>
      </header>

      <div className="px-5 py-5 sm:px-6">
        {query.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">
            Loading connections…
          </p>
        ) : null}
        {query.isError ? (
          <div role="alert" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
            <p className="text-destructive-text">
              Couldn&apos;t load search settings — {apiErrorMessage(query.error)}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void query.refetch()}
              disabled={query.isRefetching}
            >
              {query.isRefetching ? "Loading…" : "Retry"}
            </Button>
          </div>
        ) : null}

        {settings ? (
          <>
            <div className="grid grid-cols-1 gap-2 border-b border-border pb-4 sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-center">
              <Label htmlFor="search-provider-select">Service to configure</Label>
              <select
                id="search-provider-select"
                value={selectedProviderId}
                onChange={(event) => setSelectedProviderId(event.currentTarget.value as SearchProviderId)}
                className="h-9 min-w-0 rounded-sm border border-border bg-background px-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                aria-describedby="search-provider-help"
              >
                <option value={CUSTOM_SEARCH_PROVIDER_ID}>Custom</option>
                {settings.providers
                  .filter((provider) => provider.provider_id !== CUSTOM_SEARCH_PROVIDER_ID)
                  .map((provider) => (
                    <option key={provider.provider_id} value={provider.provider_id}>
                      {provider.name}
                    </option>
                  ))}
              </select>
              <p id="search-provider-help" className="text-xs text-muted-foreground sm:col-start-2">
                Choose a service to manage its saved key and connection check.
                {customProvider ? ` Custom uses ${customProvider.name}.` : ""}
              </p>
            </div>

            <div className="grid grid-cols-1 gap-2 border-b border-border py-4 sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-center">
              <Label htmlFor="search-default-provider">Default service</Label>
              <select
                id="search-default-provider"
                value={settings.default_provider ?? ""}
                onChange={(event) => {
                  const providerId = event.currentTarget.value || null;
                  setDefault.mutate(providerId as SearchProviderId | null);
                }}
                disabled={setDefault.isPending}
                className="h-9 min-w-0 rounded-sm border border-border bg-background px-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60"
                aria-describedby="search-default-help"
              >
                <option value="">No preferred service</option>
                {connectedProviders.map((provider) => (
                  <option key={provider.provider_id} value={provider.provider_id}>
                    {provider.provider_id === CUSTOM_SEARCH_PROVIDER_ID ? "Custom" : provider.name}
                  </option>
                ))}
              </select>
              <p id="search-default-help" className="text-xs text-muted-foreground sm:col-start-2">
                Only successfully tested connections can be selected.
              </p>
              {setDefault.isError ? (
                <p role="alert" className="text-sm text-destructive-text sm:col-start-2">
                  The default service could not be saved — {apiErrorMessage(setDefault.error)}
                </p>
              ) : null}
              {setDefault.isSuccess ? (
                <p role="status" className="text-sm text-success-text sm:col-start-2">
                  Default service saved.
                </p>
              ) : null}
            </div>

            {selectedProvider ? (
              <SearchProviderCard
                key={selectedProvider.provider_id}
                provider={selectedProvider}
                saving={save.isPending && saveErrorProvider === selectedProvider.provider_id}
                saveError={
                  save.isError && saveErrorProvider === selectedProvider.provider_id ? save.error : null
                }
                testing={test.isPending && testErrorProvider === selectedProvider.provider_id}
                testError={
                  test.isError && testErrorProvider === selectedProvider.provider_id ? test.error : null
                }
                testResult={test.data?.provider_id === selectedProvider.provider_id ? test.data : null}
                onSave={(apiKey, onSaved) =>
                  save.mutate({ providerId: selectedProvider.provider_id, apiKey }, { onSuccess: onSaved })
                }
                onTest={() => test.mutate(selectedProvider.provider_id)}
              />
            ) : (
              <p role="status" className="pt-4 text-sm text-muted-foreground">
                This search service is not available in the current settings response.
              </p>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}

function SearchProviderCard({
  provider,
  saving,
  saveError,
  testing,
  testError,
  testResult,
  onSave,
  onTest,
}: {
  provider: SearchProvider;
  saving: boolean;
  saveError: unknown;
  testing: boolean;
  testError: unknown;
  testResult: SearchTestResult | null;
  onSave: (apiKey: string, onSaved: () => void) => void;
  onTest: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const canRemoveKey = provider.has_key && provider.key_source === "user";
  const hasUnsavedKey = apiKey.trim().length > 0;
  const prefix = `search-${provider.provider_id}`;
  const accountUrl = SEARCH_PROVIDER_ACCOUNT_URLS[provider.provider_id];
  const displayName = provider.provider_id === CUSTOM_SEARCH_PROVIDER_ID ? "Custom" : provider.name;
  const statusText = !provider.available
    ? "Endpoint not configured"
    : provider.connected
      ? "Connected"
      : "Not tested";

  return (
    <article aria-labelledby={`${prefix}-heading`} className="py-5 first:pt-5 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id={`${prefix}-heading`} className="font-semibold">
              {displayName}
            </h3>
            <span
              className={provider.connected ? "text-xs text-success-text" : "text-xs text-muted-foreground"}
            >
              {statusText}
            </span>
          </div>
          <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
            {provider.endpoint ?? "The endpoint is configured by the deployment."}
          </p>
        </div>
        {accountUrl ? (
          <a
            href={accountUrl}
            target="_blank"
            rel="noreferrer"
            className={buttonVariants({ variant: "outline", size: "lg" })}
          >
            Get API key
          </a>
        ) : null}
        {provider.last_checked_at ? (
          <p className="text-xs text-muted-foreground">
            Checked{" "}
            <time dateTime={new Date(provider.last_checked_at * 1000).toISOString()}>
              {new Date(provider.last_checked_at * 1000).toLocaleString()}
            </time>
          </p>
        ) : null}
      </div>

      {provider.available ? (
        <>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-[8rem_minmax(0,1fr)] sm:items-center">
            <Label htmlFor={`${prefix}-key`}>API key</Label>
            <Input
              id={`${prefix}-key`}
              name={`${prefix}-key`}
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              aria-describedby={`${prefix}-key-help`}
              className="h-9"
            />
            <span className="sm:col-start-2" id={`${prefix}-key-help`}>
              <KeySummary provider={provider} />
            </span>
          </div>
          {hasUnsavedKey ? (
            <p className="mt-1 text-xs text-muted-foreground sm:ml-[8rem]">
              Save this key before testing the connection.
            </p>
          ) : null}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {hasUnsavedKey || canRemoveKey ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={saving}
                aria-busy={saving}
                onClick={() => {
                  onSave(apiKey, () => setApiKey(""));
                }}
              >
                {saving ? "Saving…" : hasUnsavedKey ? "Save key" : "Remove key"}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!provider.available || testing || hasUnsavedKey}
              aria-busy={testing}
              onClick={onTest}
            >
              {testing ? "Testing…" : "Test connection"}
            </Button>
          </div>
        </>
      ) : null}

      {saveError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Key could not be saved — {apiErrorMessage(saveError)}
        </p>
      ) : null}
      {testError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Connection check failed — {apiErrorMessage(testError)}
        </p>
      ) : null}
      {testResult ? (
        <div role={testResult.ok ? "status" : "alert"} className="mt-3 border-t border-border pt-3">
          <p className={testResult.ok ? "text-sm text-success-text" : "text-sm text-warning-text"}>
            {testResult.ok
              ? `Connection ready · ${testResult.latency_ms} ms · ${testResult.tools.length} tools found`
              : (testResult.error ?? "The connection check did not succeed.")}
          </p>
          {testResult.tools.length > 0 ? (
            <p className="mt-1 break-words font-mono text-xs text-muted-foreground">
              Available tools: {testResult.tools.join(", ")}
            </p>
          ) : null}
        </div>
      ) : provider.last_test_error ? (
        <p className="mt-2 text-sm text-warning-text">Last check: {provider.last_test_error}</p>
      ) : null}
    </article>
  );
}

function KeySummary({ provider }: { provider: SearchProvider }) {
  if (!provider.has_key) {
    return (
      <span className="text-xs text-muted-foreground">No saved key. Some services allow keyless search.</span>
    );
  }
  const source = provider.key_source === "env" ? "Server key" : "Saved key";
  return (
    <span className="text-xs text-muted-foreground">
      {source}
      {provider.key_last4 ? ` ····${provider.key_last4}` : ""}. The full key is never shown again.
    </span>
  );
}
