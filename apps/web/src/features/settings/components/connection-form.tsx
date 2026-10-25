"use client";

import { type FormEvent, useState } from "react";

import { apiErrorMessage } from "@/shared/lib/api-error";
import { Button } from "@/shared/ui/button";
import { Input } from "@/shared/ui/input";
import { Label } from "@/shared/ui/label";

import { buildSavePayload, describeTest, validateBaseUrl } from "../domain";
import type { ConnectionView } from "../entities";
import type { SaveMutation, TestMutation } from "../hooks/use-connection";
import type { ConnectionKind } from "../params";
import { KeyStatusPill } from "./key-status-pill";

/** The form itself: state starts from `initial`; the card remounts it after each save, which clears the key field. */
export function ConnectionForm({
  kind,
  initial,
  save,
  test,
}: {
  kind: ConnectionKind;
  initial: ConnectionView;
  save: SaveMutation;
  test: TestMutation;
}) {
  const [url, setUrl] = useState(initial.url);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(initial.model);
  const [urlError, setUrlError] = useState<string | null>(null);

  const prefix = `connection-${kind}`;
  const canRemoveKey = initial.has_key && initial.source === "user";
  const unsaved = url !== initial.url || model !== initial.model || apiKey !== "";

  const submit = (clearKey: boolean) => {
    const invalid = validateBaseUrl(url);
    setUrlError(invalid);
    if (invalid) return;
    save.mutate(buildSavePayload({ url, model, apiKey, clearKey }), {
      onSuccess: () => setApiKey(""),
    });
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); // Enter in any field saves, like the Save button
    if (!save.isPending) submit(false);
  };

  const saveStatus = save.isPending
    ? "Saving…"
    : save.isError
      ? apiErrorMessage(save.error)
      : save.isSuccess
        ? "Saved."
        : "";

  const testResult = test.data
    ? describeTest(kind, test.data)
    : test.isError
      ? { ok: false, text: apiErrorMessage(test.error) }
      : null;

  return (
    <form className="flex max-w-3xl flex-col gap-5" onSubmit={onSubmit} noValidate>
      {/* Label beside its field from `sm` up, above it on a phone. `minmax(0, 1fr)` lets a long URL shrink
          the field instead of widening the page. */}
      <div className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-[6rem_minmax(0,1fr)] sm:items-start">
        <Label htmlFor={`${prefix}-url`} className="sm:h-9">
          Base URL
        </Label>
        <div className="flex min-w-0 flex-col gap-1.5">
          <Input
            id={`${prefix}-url`}
            name={`${prefix}-url`}
            type="text"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            aria-invalid={urlError ? true : undefined}
            aria-describedby={urlError ? `${prefix}-url-error` : undefined}
            className="h-9"
          />
          {urlError && (
            <p id={`${prefix}-url-error`} className="text-xs text-destructive-text">
              {urlError}
            </p>
          )}
        </div>

        <Label htmlFor={`${prefix}-key`} className="sm:h-9">
          API key
        </Label>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <Input
            id={`${prefix}-key`}
            name={`${prefix}-key`}
            type="password"
            autoComplete="new-password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            className="h-9 min-w-48 flex-1"
          />
          <KeyStatusPill kind={kind} status={initial} />
        </div>

        <Label htmlFor={`${prefix}-model`} className="sm:h-9">
          Model
        </Label>
        <Input
          id={`${prefix}-model`}
          name={`${prefix}-model`}
          type="text"
          autoComplete="off"
          spellCheck={false}
          value={model}
          onChange={(event) => setModel(event.target.value)}
          className="h-9"
        />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 sm:pl-[7rem]">
        <Button type="submit" size="lg" disabled={save.isPending} aria-busy={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        {canRemoveKey && (
          <Button
            type="button"
            size="lg"
            variant="outline"
            onClick={() => submit(true)}
            disabled={save.isPending}
          >
            Remove key
          </Button>
        )}
        <Button
          type="button"
          size="lg"
          variant="outline"
          onClick={() => test.mutate()}
          disabled={test.isPending}
          aria-busy={test.isPending}
        >
          {test.isPending ? "Testing…" : "Test"}
        </Button>
        {unsaved && (
          <p id={`${prefix}-unsaved`} className="text-xs text-warning-text">
            Unsaved changes — Test uses the saved settings.
          </p>
        )}
      </div>

      {/* One block for both outcomes: an empty live region takes no height, so nothing is reserved. */}
      <div className="flex flex-col gap-1 sm:pl-[7rem]">
        <p
          id={`${prefix}-save-status`}
          aria-live="polite"
          className={`text-sm ${
            save.isError
              ? "text-destructive-text"
              : save.isSuccess
                ? "text-success-text"
                : "text-muted-foreground"
          }`}
        >
          {saveStatus}
        </p>
        <p
          id={`${prefix}-test-status`}
          aria-live="polite"
          className={`text-sm ${testResult?.ok === false ? "text-destructive-text" : "text-success-text"}`}
        >
          {testResult?.text ?? ""}
        </p>
      </div>
    </form>
  );
}
