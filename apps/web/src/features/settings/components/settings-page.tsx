import { ConnectionCard } from "./connection-card";

/** The saved connections, one per tool. Server component; the cards hydrate their own forms. */
export function SettingsPage() {
  return (
    <div className="flex max-w-4xl flex-col gap-8">
      <h1 id="page-title" className="text-3xl font-semibold tracking-tight">
        Settings
      </h1>
      <ConnectionCard
        kind="llm"
        title="LLM Endpoint"
        intro="Point Docs Distiller and YouTube Content Search at any OpenAI-compatible chat endpoint — this configuration wins over the deployment default."
      />
      <ConnectionCard
        kind="embedding"
        title="Embedding"
        intro="Give the transcript search its own OpenAI-compatible embedding endpoint, separate from the LLM endpoint — its URL, key and model can differ."
      />
    </div>
  );
}
