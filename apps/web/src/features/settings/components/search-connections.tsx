"use client";

import { useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import type { SearchProvider, SearchSettings, SearchTestResult } from "../entities";
import { useSearchConnections } from "../hooks/use-search-connections";
import { CUSTOM_SEARCH_PROVIDER_ID, type SearchProviderId } from "../params";
import { SettingsActionButton, settingsActionButtonClassName } from "./settings-action-button";

export function SearchConnections() {
  const { query, save, setDefault, test } = useSearchConnections();
  const [selectedProviderId, setSelectedProviderId] = useState<SearchProviderId | null>(null);
  const settings = query.data;
  const activeProviderId = selectedProviderId ?? settings?.default_provider ?? CUSTOM_SEARCH_PROVIDER_ID;
  const selectedProvider = settings?.providers.find((provider) => provider.provider_id === activeProviderId);
  const saveErrorProvider = save.variables?.providerId;
  const testErrorProvider = test.variables;

  return (
    <section aria-labelledby="search-connections-heading" className="border border-border bg-card">
      <header className="border-b border-border px-5 py-4 sm:px-6">
        <h2 id="search-connections-heading" className="text-xl font-semibold tracking-tight">
          Search
        </h2>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Manage saved search connections for COELHO Nexus integrations. These settings are shared across
          future research features; Distill runs do not call search yet. Some connection checks send a
          lightweight request that may count toward the service&apos;s usage.
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
            <SettingsActionButton
              type="button"
              onClick={() => void query.refetch()}
              disabled={query.isRefetching}
            >
              {query.isRefetching ? "Loading…" : "Retry"}
            </SettingsActionButton>
          </div>
        ) : null}

        {settings ? (
          <>
            <div className="grid grid-cols-1 gap-x-4 gap-y-4 border-b border-border pb-4 sm:grid-cols-[6rem_minmax(0,1fr)] sm:items-start">
              <Label htmlFor="search-provider-select" className="sm:h-9">
                Service
              </Label>
              <div className="flex min-w-0 flex-col gap-1.5">
                <select
                  id="search-provider-select"
                  value={activeProviderId}
                  onChange={(event) => {
                    const providerId = event.currentTarget.value as SearchProviderId;
                    setSelectedProviderId(providerId);
                    setDefault.reset();
                    const provider = settings.providers.find((item) => item.provider_id === providerId);
                    if (provider?.connected && providerId !== settings.default_provider) {
                      setDefault.mutate(providerId);
                    }
                  }}
                  className="h-9 min-w-0 rounded-sm border border-border bg-background px-2 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60"
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
                <p id="search-provider-help" className="text-xs text-muted-foreground">
                  Choose a service to manage its connection. A successful check sets it as the default.
                </p>
              </div>
              {setDefault.isError ? (
                <p role="alert" className="text-sm text-destructive-text sm:col-start-2">
                  The selected service could not be saved — {apiErrorMessage(setDefault.error)}
                </p>
              ) : null}
              {setDefault.isSuccess && setDefault.variables === activeProviderId ? (
                <p role="status" className="text-sm text-success-text sm:col-start-2">
                  This is now the default search service.
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
                onSave={(connection, onSaved) =>
                  save.mutate(
                    { providerId: selectedProvider.provider_id, ...connection },
                    { onSuccess: onSaved },
                  )
                }
                onTest={() => {
                  setDefault.reset();
                  test.mutate(selectedProvider.provider_id, {
                    onSuccess: (result) => {
                      if (result.ok) setDefault.mutate(selectedProvider.provider_id);
                    },
                  });
                }}
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
  onSave: (
    connection: { apiKey?: string; endpoint?: string },
    onSaved: (settings: SearchSettings) => void,
  ) => void;
  onTest: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [endpointDraft, setEndpointDraft] = useState(provider.endpoint ?? "");
  const isCustom = provider.provider_id === CUSTOM_SEARCH_PROVIDER_ID;
  const canRemoveKey = provider.has_key && provider.key_source === "user";
  const hasUnsavedKey = apiKey.trim().length > 0;
  const hasUnsavedEndpoint = isCustom && endpointDraft.trim() !== (provider.endpoint ?? "");
  const hasUnsavedConnection = hasUnsavedKey || hasUnsavedEndpoint;
  const prefix = `search-${provider.provider_id}`;
  const accountUrl = provider.account_url;
  const displayName = isCustom ? "Custom" : provider.name;
  const statusText = hasUnsavedConnection
    ? "Unsaved changes"
    : provider.connected
      ? "Connected"
      : provider.available
        ? "Not tested"
        : isCustom
          ? "Setup required"
          : "Endpoint unavailable";

  return (
    <article aria-labelledby={`${prefix}-heading`} className="py-5 first:pt-5 last:pb-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id={`${prefix}-heading`} className="font-semibold">
              {displayName}
            </h3>
            <span
              aria-live="polite"
              className={
                provider.connected && !hasUnsavedConnection
                  ? "text-xs text-success-text"
                  : "text-xs text-muted-foreground"
              }
            >
              {statusText}
            </span>
          </div>
          {!isCustom ? (
            <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{provider.endpoint}</p>
          ) : null}
        </div>
        {accountUrl ? (
          <a href={accountUrl} target="_blank" rel="noreferrer" className={settingsActionButtonClassName}>
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

      {provider.available || isCustom ? (
        <>
          <div className="mt-4 grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-[6rem_minmax(0,1fr)] sm:items-start">
            {isCustom ? (
              <>
                <Label htmlFor={`${prefix}-endpoint`} className="sm:h-9">
                  MCP endpoint
                </Label>
                <div className="flex min-w-0 flex-col gap-1.5">
                  <Input
                    id={`${prefix}-endpoint`}
                    name={`${prefix}-endpoint`}
                    type="url"
                    autoComplete="url"
                    value={endpointDraft}
                    onChange={(event) => setEndpointDraft(event.target.value)}
                    placeholder="https://search.example.com/mcp"
                    aria-describedby={`${prefix}-endpoint-help`}
                    className="h-9"
                  />
                  <span className="text-xs text-muted-foreground" id={`${prefix}-endpoint-help`}>
                    Enter the full MCP endpoint URL. HTTPS is required except for localhost and trusted
                    internal services.
                  </span>
                </div>
              </>
            ) : null}
            <Label htmlFor={`${prefix}-key`} className="sm:h-9">
              API key
            </Label>
            <div className="flex min-w-0 flex-col gap-1.5">
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
              <span id={`${prefix}-key-help`}>
                <KeySummary provider={provider} />
              </span>
            </div>
            {hasUnsavedKey || hasUnsavedEndpoint ? (
              <p className="text-xs text-muted-foreground sm:col-start-2">
                Save the connection details before testing.
              </p>
            ) : null}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 sm:pl-[7rem]">
            {isCustom ? (
              <>
                {hasUnsavedKey || hasUnsavedEndpoint ? (
                  <SettingsActionButton
                    type="button"
                    disabled={saving}
                    aria-busy={saving}
                    onClick={() => {
                      const connection: { apiKey?: string; endpoint?: string } = {};
                      if (hasUnsavedKey) connection.apiKey = apiKey;
                      if (hasUnsavedEndpoint) connection.endpoint = endpointDraft.trim();
                      onSave(connection, (settings) => {
                        const savedProvider = settings.providers.find(
                          (item) => item.provider_id === provider.provider_id,
                        );
                        setEndpointDraft(savedProvider?.endpoint ?? "");
                        setApiKey("");
                      });
                    }}
                  >
                    {saving ? "Saving…" : "Save connection"}
                  </SettingsActionButton>
                ) : null}
                {canRemoveKey ? (
                  <SettingsActionButton
                    type="button"
                    disabled={saving}
                    aria-busy={saving}
                    onClick={() => onSave({ apiKey: "" }, () => setApiKey(""))}
                  >
                    {saving ? "Saving…" : "Remove key"}
                  </SettingsActionButton>
                ) : null}
              </>
            ) : hasUnsavedKey || canRemoveKey ? (
              <SettingsActionButton
                type="button"
                disabled={saving}
                aria-busy={saving}
                onClick={() => onSave({ apiKey }, () => setApiKey(""))}
              >
                {saving ? "Saving…" : hasUnsavedKey ? "Save key" : "Remove key"}
              </SettingsActionButton>
            ) : null}
            <SettingsActionButton
              type="button"
              disabled={
                !provider.available ||
                testing ||
                hasUnsavedKey ||
                hasUnsavedEndpoint ||
                (provider.key_required && !provider.has_key)
              }
              aria-busy={testing}
              onClick={onTest}
            >
              {testing ? "Testing…" : "Test connection"}
            </SettingsActionButton>
          </div>
        </>
      ) : null}

      {saveError ? (
        <p role="alert" className="mt-2 text-sm text-destructive-text">
          Connection details could not be saved — {apiErrorMessage(saveError)}
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
              ? testResult.tools.length > 0
                ? `MCP connection ready · ${testResult.latency_ms} ms · ${testResult.tools.length} tools found`
                : `Search connection ready · ${testResult.latency_ms} ms`
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
      <span className="text-xs text-muted-foreground">
        {provider.key_required
          ? "No saved key. Save a key before testing."
          : "No saved key. This service supports keyless access."}
      </span>
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
