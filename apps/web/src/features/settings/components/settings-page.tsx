import { ConnectionCard } from "./connection-card";
import { SearchConnections } from "./search-connections";

/** The saved connections, one per tool. Server component; the cards hydrate their own forms. The page title sits in
 * the app's second navbar. */
export function SettingsPage() {
  return (
    <div className="flex max-w-4xl flex-col gap-8">
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
      <SearchConnections />
    </div>
  );
}
