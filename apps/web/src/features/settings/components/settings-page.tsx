"use client";

import { useState } from "react";

import { ContextBar } from "@/shared/layout/context-bar";

import { SETTINGS_TABS, type SettingsTabKey } from "../params";
import { ConnectionCard } from "./connection-card";
import { SearchConnections } from "./search-connections";

/** Saved connection settings with their view selector in the third navigation row. */
export function SettingsPage() {
  const [selectedTab, setSelectedTab] = useState<SettingsTabKey>("llm");

  return (
    <>
      <ContextBar
        start={<span className="mono-label">Connection settings</span>}
        end={
          <nav aria-label="Settings views" className="flex w-full items-center sm:w-auto">
            {SETTINGS_TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                aria-pressed={selectedTab === tab.key}
                onClick={() => setSelectedTab(tab.key)}
                className={
                  selectedTab === tab.key
                    ? "inline-flex h-9 items-center px-2 text-base font-semibold text-foreground transition-colors sm:px-3"
                    : "inline-flex h-9 items-center px-2 text-base font-medium text-muted-foreground transition-colors hover:text-foreground sm:px-3"
                }
              >
                {tab.label}
              </button>
            ))}
          </nav>
        }
      />

      <div className="flex max-w-4xl flex-col gap-8">
        {selectedTab === "llm" ? (
          <ConnectionCard
            kind="llm"
            title="LLM"
            intro="Set the chat connection used by Docs Distiller and YouTube Content Search. This setting takes precedence over the deployment default."
          />
        ) : null}
        {selectedTab === "embedding" ? (
          <ConnectionCard
            kind="embedding"
            title="Embedding"
            intro="Set a separate OpenAI-compatible embedding connection for transcript search. Its URL, key, and model can differ from the chat connection."
          />
        ) : null}
        {selectedTab === "search" ? <SearchConnections /> : null}
      </div>
    </>
  );
}
