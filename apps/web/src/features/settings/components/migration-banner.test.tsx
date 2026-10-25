import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { client } from "@/shared/api";
import { QueryProvider } from "@/shared/providers/query-provider";

import { useEmbeddingMigration } from "../hooks/use-embedding-migration";
import { MIGRATION_POLL_MS, MIGRATION_RECHECK_MS } from "../params";
import { MigrationBanner } from "./migration-banner";

const ORIGIN = "http://nexus.test";
const STATUS_URL = `${ORIGIN}/api/v1/ycs/content/embedding-migration/status`;
const START_URL = `${ORIGIN}/api/v1/ycs/content/embedding-migration/start`;
const TASK_URL = `${ORIGIN}/api/v1/ycs/admin/task/task-1`;

const COLLECTION = "ycs-transcripts-2026-10";

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
beforeEach(() => {
  client.setConfig({ baseUrl: ORIGIN });
  server.use(
    http.get(STATUS_URL, () =>
      HttpResponse.json({ needed: false, mismatch: null, state: null, active_collection: COLLECTION }),
    ),
    http.get(TASK_URL, () => HttpResponse.json({ task_id: "task-1", state: "STARTED", meta: null })),
    http.post(START_URL, () => HttpResponse.json({})),
  );
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function Banner() {
  useEmbeddingMigration();
  return <MigrationBanner />;
}

const renderBanner = () =>
  render(
    <QueryProvider>
      <Banner />
    </QueryProvider>,
  );

describe("MigrationBanner", () => {
  it("banner hides, but the active collection stays visible", async () => {
    renderBanner();

    expect(await screen.findByText(COLLECTION)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Migrate now" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Embedding model changed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Migrating /)).not.toBeInTheDocument();
  });

  it("shows the change and the button when the models mismatch", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          needed: true,
          mismatch: { from_model: "integral-a", to_model: "integral-b" },
          state: null,
          active_collection: COLLECTION,
        }),
      ),
    );
    renderBanner();

    expect(await screen.findByText(/Embedding model changed from/)).toBeInTheDocument();
    expect(screen.getByText(/integral-a/)).toBeInTheDocument();
    expect(screen.getByText(/integral-b/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Migrate now" })).toBeInTheDocument();
    expect(screen.queryByText(/Migrating /)).not.toBeInTheDocument();
  });

  it("migrate now posts the start and refetches the status", async () => {
    let statusCalls = 0;
    let startCalls = 0;
    server.use(
      http.get(STATUS_URL, () => {
        statusCalls += 1;
        return HttpResponse.json(
          startCalls > 0
            ? {
                needed: true,
                mismatch: { from_model: "integral-a", to_model: "integral-b" },
                state: {
                  status: "running",
                  from_model: "integral-a",
                  to_model: "integral-b",
                  task_id: "task-1",
                },
                active_collection: COLLECTION,
              }
            : {
                needed: true,
                mismatch: { from_model: "integral-a", to_model: "integral-b" },
                state: null,
                active_collection: COLLECTION,
              },
        );
      }),
      http.post(START_URL, () => {
        startCalls += 1;
        return HttpResponse.json({ queued: true });
      }),
    );
    renderBanner();

    await screen.findByText(/Embedding model changed from/);
    const before = statusCalls;
    await userEvent.click(screen.getByRole("button", { name: "Migrate now" }));

    expect(startCalls).toBe(1);
    await screen.findByText(/Migrating integral-a → integral-b/);
    expect(statusCalls).toBeGreaterThan(before);
  });

  it("counts progress from a PROGRESS task", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          needed: true,
          mismatch: { from_model: "integral-a", to_model: "integral-b" },
          state: { status: "running", from_model: "integral-a", to_model: "integral-b", task_id: "task-1" },
          active_collection: COLLECTION,
        }),
      ),
      http.get(TASK_URL, () =>
        HttpResponse.json({ task_id: "task-1", state: "PROGRESS", meta: { current: 3, total: 10 } }),
      ),
    );
    renderBanner();

    expect(await screen.findByText(/Migrating integral-a → integral-b/)).toBeInTheDocument();
    expect(await screen.findByText("3/10 transcripts re-embedded")).toBeInTheDocument();
  });

  it("says so before the task reports anything", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          needed: true,
          mismatch: { from_model: "integral-a", to_model: "integral-b" },
          state: { status: "running", from_model: "integral-a", to_model: "integral-b", task_id: "task-1" },
          active_collection: COLLECTION,
        }),
      ),
      http.get(TASK_URL, () => HttpResponse.json({ task_id: "task-1", state: "PENDING" })),
    );
    renderBanner();

    expect(await screen.findByText(/Migrating integral-a → integral-b/)).toBeInTheDocument();
    expect(await screen.findByText("PENDING")).toBeInTheDocument();
  });

  it("stops polling at a terminal state, reports it, then re-checks the status once", async () => {
    vi.useFakeTimers();
    try {
      let statusCalls = 0;
      let taskCalls = 0;
      server.use(
        http.get(STATUS_URL, () => {
          statusCalls += 1;
          return HttpResponse.json({
            needed: true,
            mismatch: { from_model: "integral-a", to_model: "integral-b" },
            state: { status: "running", from_model: "integral-a", to_model: "integral-b", task_id: "task-1" },
            active_collection: COLLECTION,
          });
        }),
        http.get(TASK_URL, () => {
          taskCalls += 1;
          return HttpResponse.json({ task_id: "task-1", state: "SUCCESS" });
        }),
      );
      renderBanner();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(taskCalls).toBe(1);
      expect(screen.getByText("Embedding migration complete")).toBeInTheDocument();
      const statusBeforeRecheck = statusCalls;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(MIGRATION_RECHECK_MS + 200);
      });
      expect(statusCalls).toBe(statusBeforeRecheck + 1);

      const statusAfterRecheck = statusCalls;
      const taskCallsAfterStop = taskCalls;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(MIGRATION_POLL_MS * 3);
      });
      expect(taskCalls).toBe(taskCallsAfterStop);
      expect(statusCalls).toBe(statusAfterRecheck);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports a failed worker run instead of pretending all is well", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          needed: true,
          mismatch: { from_model: "integral-a", to_model: "integral-b" },
          state: { status: "running", from_model: "integral-a", to_model: "integral-b", task_id: "task-1" },
          active_collection: COLLECTION,
        }),
      ),
      http.get(TASK_URL, () => HttpResponse.json({ task_id: "task-1", state: "FAILURE", error: "boom" })),
    );
    renderBanner();

    expect(await screen.findByText("Embedding migration failed — check the worker logs")).toBeInTheDocument();
  });
});
