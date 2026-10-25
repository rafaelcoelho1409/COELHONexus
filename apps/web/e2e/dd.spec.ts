import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures";

const WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

// Logos are hot-linked from each project's own site, often with very long URLs. The page must draw them as
// images and never print the address.
const LONG_LOGO = `https://logos.test/${"very-long-path-segment-".repeat(8)}alpha.svg`;
const TILES = [
  { slug: "alpha", name: "Alpha", category: "Dev", tier: 1, logo: LONG_LOGO, logos: null },
  {
    slug: "beta",
    name: "Beta",
    category: "Ops",
    tier: null,
    logo: null,
    logos: ["https://logos.test/beta-mark.svg", "https://logos.test/beta-word.svg"],
  },
];
const LIBRARY = [
  {
    slug: "alpha",
    framework_name: "Alpha",
    framework_slug: "alpha",
    page_count: 2,
    ingested_at: Math.floor(Date.now() / 1000) - 120,
    tier_kind: "sitemap",
    logo: null,
    logos: null,
  },
];
const MANIFEST = {
  framework_name: "Alpha",
  framework_slug: "alpha",
  total_bytes: 640,
  tier_kind: "sitemap",
  ingested_at: Math.floor(Date.now() / 1000) - 120,
  entries: [
    {
      idx: 0,
      key: "ingest/alpha/pages/0000-quickstart.md",
      title: "Quickstart",
      slug: null,
      tier: "llms-full",
      bytes: 320,
      url: "https://docs.example.com/docs/quickstart",
      source_path: null,
      section: "Docs",
      notes: null,
    },
    {
      idx: 1,
      title: "CLI",
      slug: null,
      tier: "docs",
      bytes: 320,
      url: "https://docs.example.com/docs/cli",
      source_path: null,
      section: "Docs",
      notes: null,
    },
  ],
};

/** Contract-only API: every DD request the pages make is decided here. */
const json = (route: { fulfill: (o: { json: unknown }) => void }, body: unknown) =>
  route.fulfill({ json: body });

async function mockRoutes(page: Page) {
  await page.route("**/api/v1/dd/resolver", (route) => json(route, TILES));
  await page.route("https://logos.test/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="24"><rect width="64" height="24" fill="#888"/></svg>',
    }),
  );
  await page.route("**/api/v1/dd/ingestion", (route) => {
    if (route.request().method() === "DELETE") {
      json(route, { ok: true });
      return;
    }
    json(route, LIBRARY);
  });
  await page.route("**/api/v1/dd/runs/active", (route) => json(route, { active: [] }));
  await page.route("**/api/v1/dd/runs", (route) => {
    if (route.request().method() === "POST") {
      route.fulfill({ json: { status: "queued", run_id: "run-1" } });
      return;
    }
    route.continue();
  });
  await page.route("**/api/v1/dd/runs/run-1", (route) =>
    json(route, {
      progress: {
        tier: "post",
        status: "running",
        current: 2,
        total: 3,
        last_url: null,
        error: null,
        updated_at: null,
      },
      post: null,
    }),
  );
  await page.route("**/api/v1/dd/runs/run-1/cancel", (route) => json(route, { ok: true }));
  await page.route("**/api/v1/dd/ingestion/alpha/manifest", (route) => json(route, MANIFEST));
  await page.route("**/api/v1/dd/ingestion/beta/manifest", (route) =>
    route.fulfill({ status: 404, json: { detail: "not ingested" } }),
  );
  await page.route("**/api/v1/dd/ingestion/alpha/pages/*", (route) =>
    json(route, { body: "# Quickstart\n\n**alpha** quickstart body" }),
  );
}

/** Checkpoint keys as the backend's PlannerState names them. */
const PLANNER_RECOVERED_STATE = {
  values: {
    raw_files: ["ingest/alpha/docs/intro.md"],
    corpus_stats: {
      total_files: 12,
      total_bytes: 20480,
      median_bytes: 1500,
      min_bytes: 100,
      p10_bytes: 300,
      p90_bytes: 4000,
      max_bytes: 9000,
      load_ms: 40,
      tier_kind: "llms-full",
    },
    relevant_files: ["ingest/alpha/docs/intro.md"],
    off_topic_stats: {
      kept: 10,
      dropped: 2,
      total: 12,
      llm_judged: 12,
      llm_kept: 10,
      llm_dropped: 2,
      judge_decisions: [{ key: "ingest/alpha/docs/intro.md", verdict: "KEEP", latency_s: 0.8 }],
    },
    plan_path: "planner/alpha/plan-latest.json",
  },
  next: [],
};

async function mockPlannerRoutes(page: Page, opts: { recent?: boolean } = {}) {
  await mockRoutes(page);
  const recent = opts.recent === false ? [] : [{ slug: "alpha", thread_id: "planner/alpha/old" }];
  await page.route("**/api/v1/dd/planner/info", (route) =>
    json(route, {
      implemented: [
        "corpus_load",
        "off_topic",
        "doc_distill",
        "chapter_propose",
        "chapter_assign",
        "chapter_select",
        "order_chapters",
        "plan_write",
      ],
    }),
  );
  await page.route("**/api/v1/dd/planner/alpha/active", (route) => json(route, { active: false }));
  await page.route("**/api/v1/dd/planner/alpha/timing", (route) =>
    json(route, { total_wall_ms: opts.recent === false ? 0 : 4200 }),
  );
  await page.route("**/api/v1/dd/planner/recent", (route) => json(route, { recent }));
  // A fresh slug has no checkpoint until its run has been started; after that the plan is there.
  let runStarted = opts.recent !== false;
  await page.route("**/api/v1/dd/planner/debug/graph/**", (route) => {
    json(route, runStarted ? PLANNER_RECOVERED_STATE : { values: {}, next: [] });
  });
  await page.route("**/api/v1/dd/pipeline/active", (route) => json(route, { planner: null, synth: null }));
  await page.route("**/api/v1/dd/pipeline/alpha/state", (route) =>
    json(route, { ingestion: true, planner: true, synth: false, study: false }),
  );
  await page.route("**/api/v1/dd/planner/alpha?*", (route) => {
    if (route.request().method() === "POST") {
      runStarted = true;
      route.fulfill({ json: { status: "queued", thread_id: "planner/alpha/t1" } });
      return;
    }
    route.continue();
  });
  // registered after the graph-state route above, so it wins for `…/llm-counters`
  await page.route("**/api/v1/dd/planner/debug/graph/*/llm-counters", (route) =>
    json(route, {
      total: { calls: 14, tokens_in: 9000, tokens_out: 2100, reasoning_tokens: 0 },
      by_node: {
        off_topic: { calls: 12, tokens_in: 6000, tokens_out: 300, reasoning_tokens: 0 },
        chapter_propose: { calls: 2, tokens_in: 3000, tokens_out: 1800, reasoning_tokens: 0 },
      },
    }),
  );
  await page.route("**/api/v1/dd/planner/alpha/health", (route) =>
    json(route, { degraded: true, pipeline_health: { chapter_propose_fallback_used: true } }),
  );
  // thread ids contain slashes; the client sends them percent-encoded, so they are one path segment
  await page.route("**/api/v1/dd/planner/*/events", (route) =>
    route.fulfill({
      headers: { "content-type": "text/event-stream" },
      body:
        `data: ${JSON.stringify({ step: "corpus_load", kind: "start" })}\n\n` +
        `data: ${JSON.stringify({ step: "plan_write", kind: "done" })}\n\n` +
        `data: ${JSON.stringify({ step: "planner", kind: "terminal", status: "done", total_wall_ms: 4100 })}\n\n`,
    }),
  );
}

const SYNTH_CHAPTERS = {
  chapters: [
    {
      id: "01_hall",
      title: "Hallucinations",
      order: 1,
      rendered: true,
      wall_ms: 2000,
      thread_id: "synth/alpha/1",
      audit_passed: true,
      n_sections: 2,
      n_sources: 1,
      rendered_chars: 24_600,
    },
    { id: "02_eval", title: "Evaluations", order: 2, rendered: false, wall_ms: null, thread_id: null },
  ],
  study_total_wall_ms: 2000,
};

/** A chapter as synth writes it: an inline Contents block, math, a table, cited sources, code and a callout. */
const CHAPTER_README = [
  "# Hallucinations",
  "",
  "## Contents",
  "",
  "- [Verifiers](#verifiers)",
  "- [Retrieval](#retrieval)",
  "",
  "---",
  "",
  "## Verifiers",
  "",
  "Verifiers cut hallucination rates before collapse.",
  "",
  "$$",
  "p = \\frac{a}{b}",
  "$$",
  "",
  "| metric | value |",
  "| - | - |",
  "| rate | 0.1 |",
  "",
  "Sources for this section:",
  "",
  "- `0000-quickstart.md` — Quickstart",
  "",
  "## Retrieval",
  "",
  "```js",
  "const grounded = true;",
  "```",
  "",
  // synth writes its fences without a language tag
  "```",
  "from alpha import create_agent",
  "",
  "def answer(question: str) -> str:",
  "    return create_agent().run(question)",
  "```",
  "",
  "> [!NOTE]",
  "> Ground every answer.",
  "",
].join("\n");

async function mockSynthRoutes(page: Page) {
  await mockRoutes(page);
  await page.route("**/api/v1/dd/synth/info", (route) => json(route, { implemented: ["write"] }));
  await page.route("**/api/v1/dd/synth/alpha/study/chapters", (route) => json(route, SYNTH_CHAPTERS));
  await page.route("**/api/v1/dd/synth/alpha/active", (route) => json(route, { active: false }));
  await page.route("**/api/v1/dd/pipeline/active", (route) => json(route, { planner: null, synth: null }));
  await page.route("**/api/v1/dd/synth/alpha?*", (route) => {
    if (route.request().method() === "POST") {
      route.fulfill({ json: { status: "queued", thread_id: "synth/alpha/9" } });
      return;
    }
    route.continue();
  });
  await page.route("**/api/v1/dd/synth/*/events", (route) =>
    route.fulfill({
      headers: { "content-type": "text/event-stream" },
      body:
        `data: ${JSON.stringify({ step: "write", kind: "section_done" })}\n\n` +
        `data: ${JSON.stringify({ step: "synth", kind: "terminal", status: "done", wall_ms: 3000 })}\n\n`,
    }),
  );
  await page.route("**/api/v1/dd/synth/debug/graph/**", (route) => json(route, { values: {}, next: [] }));
  await page.route("**/api/v1/dd/synth/alpha/study/01_hall/artifact/README.md", (route) =>
    route.fulfill({
      headers: { "content-type": "text/plain; charset=utf-8" },
      body: CHAPTER_README,
    }),
  );
}

async function mockPipelineRoutes(page: Page, opts: { synthBusy?: boolean } = {}) {
  await mockPlannerRoutes(page, { recent: false });
  await mockSynthRoutes(page);
  await page.route("**/api/v1/dd/pipeline/active", (route) =>
    json(
      route,
      opts.synthBusy === false
        ? { planner: null, synth: null }
        : { planner: null, synth: { slug: "alpha", thread_id: "synth/alpha/z" } },
    ),
  );
}

test.describe("Docs Distiller", () => {
  test("catalog tiles render, facet and start a run", async ({ page }) => {
    await mockRoutes(page);
    await page.goto("/dd");

    await expect(page.getByRole("heading", { level: 1, name: "Docs Distiller" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Ingestion" })).toHaveAttribute("href", "/dd/ingestion");

    await expect(page.getByText("Alpha")).toBeVisible();
    await expect(page.getByText("2 of 2 frameworks shown")).toBeVisible();

    await page.getByLabel("Search the catalog").fill("beta");
    await expect(page.getByText("1 of 2 frameworks shown")).toBeVisible();

    await page.getByRole("button", { name: "Beta" }).click();
    await page.getByRole("button", { name: "Start Ingestion" }).click();
    await expect(page).toHaveURL(/\/dd\/ingestion\?slug=beta&run=run-1/);
  });

  test("catalog tiles draw their logos as images, never as an address, and fit the screen", async ({
    page,
  }) => {
    await mockRoutes(page);
    await page.goto("/dd");
    await expect(page.getByText("2 of 2 frameworks shown")).toBeVisible();

    const logos = page.locator("ul img");
    await expect(logos).toHaveCount(3); // alpha's one, beta's two
    await expect
      .poll(() =>
        logos.evaluateAll((images) => images.every((img) => (img as HTMLImageElement).naturalWidth > 0)),
      )
      .toBe(true);
    await expect(page.getByText("logos.test")).toHaveCount(0); // no URL printed as text
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test("a stage tab keeps the framework you are on", async ({ page }) => {
    await mockRoutes(page);
    await page.goto("/dd/ingestion?slug=alpha");
    await page
      .getByRole("navigation", { name: "Docs Distiller stages" })
      .getByRole("link", { name: "Synth" })
      .click();
    await expect(page).toHaveURL(/\/dd\/synth\?slug=alpha$/);
  });

  test("the ingestion preview renders the page, and Source shows the raw text", async ({ page }) => {
    await mockRoutes(page);
    await page.goto("/dd/ingestion?slug=alpha");
    const preview = page.getByRole("region", { name: "Ingested library" });

    await expect(preview.getByRole("button", { name: "Rendered" })).toHaveAttribute("aria-pressed", "true");
    await expect(preview.getByRole("heading", { name: "Quickstart" })).toBeVisible();
    await expect(preview.getByText("alpha", { exact: true })).toBeVisible(); // `**alpha**` drawn bold, not as asterisks
    await expect(preview.getByText("**alpha**")).toHaveCount(0);

    await preview.getByRole("button", { name: "Source" }).click();
    await expect(preview.getByText(/\*\*alpha\*\* quickstart body/)).toBeVisible();
  });

  test("ingestion stage shows the run progress box and cancels", async ({ page }) => {
    await mockRoutes(page);
    await page.goto("/dd/ingestion?slug=alpha&run=run-1");

    await expect(page.getByRole("region", { name: "Ingestion progress" })).toBeVisible();
    await expect(page.getByText(/Splitting & deduplicating pages/i)).toBeVisible();
    await expect(page.getByText("2 / 3 (67%)")).toBeVisible();
    await expect(page.getByText("starting…")).toBeVisible();

    const cancelRequest = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("/api/v1/dd/runs/run-1/cancel"),
    );
    await page.getByRole("button", { name: "Cancel run" }).click();
    await expect(cancelRequest).resolves.toHaveProperty("url");
  });

  test("ingestion stage without a run falls back to the library state", async ({ page }) => {
    await mockRoutes(page);
    await page.goto("/dd/ingestion?slug=alpha");

    await expect(page.getByText("Alpha · 2 pages · 640 B sitemap · 2m ago")).toBeVisible();
    await expect(page.getByText("2 of 2 pages")).toBeVisible();
  });

  test("planner stage recovers a committed plan and offers Resume", async ({ page }) => {
    await mockPlannerRoutes(page);
    await page.goto("/dd/planner?slug=alpha");
    await expect(page.getByTestId("planner-start")).toHaveText("Resume");
    await expect(page.getByRole("button", { name: /Corpus load/ })).toContainText("12 files");
  });

  test("planner stage runs to the plan terminal event", async ({ page }) => {
    await mockPlannerRoutes(page, { recent: false });
    await page.goto("/dd/planner?slug=alpha");
    await page.getByTestId("planner-start").click();
    await expect(page.getByRole("button", { name: /Corpus load/ })).toContainText("12 files", {
      timeout: 15_000,
    });
    await expect(page.locator("[role=status][data-status=done]")).toBeVisible();
    await expect(page.getByTestId("planner-start")).toBeEnabled();
  });

  test("a node opens its drawer with overview, activity and raw state, and Escape closes it", async ({
    page,
  }) => {
    await mockPlannerRoutes(page);
    await page.goto("/dd/planner?slug=alpha");
    await page.getByRole("button", { name: /Off-topic filter/ }).click();

    const drawer = page.getByRole("dialog", { name: "Off-topic filter" });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("LLM calls", { exact: true }).first()).toBeVisible();
    await expect(drawer.getByRole("region", { name: /LLM verdict/ })).toBeVisible();

    await drawer.getByRole("tab", { name: "Raw I/O" }).click();
    await expect(drawer.getByText(/"off_topic_stats"/)).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  });

  test("a book with every chapter written reads Done, with a check on each chapter", async ({ page }) => {
    await mockSynthRoutes(page);
    await page.route("**/api/v1/dd/synth/alpha/study/chapters", (route) =>
      json(route, {
        ...SYNTH_CHAPTERS,
        chapters: SYNTH_CHAPTERS.chapters.map((chapter) => ({
          ...chapter,
          rendered: true,
          wall_ms: 1000,
          thread_id: `synth/alpha/${chapter.id}`,
        })),
      }),
    );
    await page.goto("/dd/synth?slug=alpha");

    await expect(page.locator("[role=status][data-status=done]")).toBeVisible();
    await expect(page.getByTestId("chstrip-counter")).toHaveText("2 / 2");
    for (const id of ["01_hall", "02_eval"]) {
      await expect(page.locator(`[data-chapter-id="${id}"] .lucide-check`)).toBeVisible();
    }
    // The node board belongs to one chapter's run: it says so instead of showing every node as pending.
    await expect(
      page.getByText("Pick a finished chapter above to see how each of its nodes ran."),
    ).toBeVisible();
  });

  test("synth stage asks before re-running a synthesized chapter, then runs it to done", async ({ page }) => {
    await mockSynthRoutes(page);
    let wiped = false;
    await page.route("**/api/v1/dd/synth/alpha/wipe?chapter_id=01_hall", (route) => {
      wiped = true;
      return json(route, { ok: true });
    });
    await page.goto("/dd/synth?slug=alpha");
    await expect(page.getByTestId("chstrip-counter")).toHaveText("1 / 2");

    // Cancel leaves everything as it was.
    await page.getByTestId("chapter-run-01_hall").click();
    const dialog = page.getByRole("dialog", { name: "Re-synthesize this chapter?" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(wiped).toBe(false);

    // Confirming wipes the old output, starts the chapter, and the terminal event settles it as done.
    const started = page.waitForRequest(
      (request) => request.method() === "POST" && request.url().includes("chapter_id=01_hall"),
    );
    await page.getByTestId("chapter-run-01_hall").click();
    await page.getByRole("dialog").getByRole("button", { name: "Re-synthesize" }).click();
    await started;
    expect(wiped).toBe(true);
    await expect(page.locator('[data-chapter-id="01_hall"]')).toHaveAttribute("data-status", "done", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("chapter-run-01_hall")).toBeEnabled();
  });

  test("synth stage opens a synthesized chapter in the study reader", async ({ page }) => {
    await mockSynthRoutes(page);
    await page.goto("/dd/synth?slug=alpha");
    await page.getByRole("link", { name: /Open Hallucinations in the study reader/ }).click();
    await expect(page).toHaveURL(/\/dd\/study\?slug=alpha&chapter=01_hall/);
    await expect(page.getByTestId("study-title")).toHaveText("Hallucinations");
  });

  test("study stage renders a chapter with its TOC", async ({ page }) => {
    await mockSynthRoutes(page);
    await page.goto("/dd/study?slug=alpha");
    await expect(page.getByTestId("study-title")).toHaveText("Hallucinations");
    await expect(page.getByText("Verifiers cut hallucination rates before collapse.")).toBeVisible();
  });

  test("the study reader shows math, a table, highlighted code and a callout, without the inline Contents", async ({
    page,
  }) => {
    await mockSynthRoutes(page);
    await page.goto("/dd/study?slug=alpha");
    const chapter = page.getByRole("region", { name: "Chapter" });
    await expect(chapter.getByRole("heading", { name: "Verifiers" })).toBeVisible();
    await expect(chapter.getByRole("heading", { name: "Contents" })).toHaveCount(0);
    await expect(chapter.locator(".katex").first()).toBeVisible();
    await expect(chapter.getByRole("cell", { name: "0.1" })).toBeVisible();
    await expect(chapter.locator(".hljs-keyword", { hasText: "const" })).toBeVisible();
    // A fence with no language is still coloured, and says which language it guessed.
    await expect(chapter.locator(".hljs-keyword", { hasText: "def" })).toBeVisible();
    await expect(chapter.locator("figcaption", { hasText: "python" })).toBeVisible();
    // The title appears once (above the text), with the chapter's facts under it.
    await expect(chapter.getByRole("heading", { name: "Hallucinations" })).toHaveCount(1);
    await expect(chapter.getByText("Audit passed")).toBeVisible();
    await expect(chapter.getByText("24.6k chars")).toBeVisible();
    await expect(chapter.getByText("25 min read")).toBeVisible();
    await expect(chapter.locator("aside.md-alert-note")).toContainText("Ground every answer.");
    const outline = page.getByRole("navigation", { name: "On this page" });
    if (await outline.isVisible()) {
      await expect(outline.getByRole("link")).toHaveText(["Verifiers", "Retrieval"]);
    }
  });

  test("a cited source opens the ingested page in a sheet, and Escape closes it", async ({ page }) => {
    await mockSynthRoutes(page);
    await page.goto("/dd/study?slug=alpha");
    await page.getByText("Sources for this section (1)").click();
    await page.getByRole("button", { name: "0000-quickstart.md" }).click();

    const sheet = page.getByRole("dialog", { name: "Quickstart" });
    await expect(sheet).toBeVisible();
    await expect(sheet.getByText("alpha quickstart body")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });

  test("Ctrl+K searches the chapters and jumps to the heading found", async ({ page }) => {
    await mockSynthRoutes(page);
    await page.goto("/dd/study?slug=alpha");
    await expect(page.getByTestId("study-title")).toHaveText("Hallucinations");
    await page.getByRole("button", { name: /Search chapters/ }).click();

    const search = page.getByRole("dialog", { name: "Search all chapters" });
    await search.getByRole("combobox").fill("retrieval");
    await expect(search.getByRole("option", { name: /Retrieval/ })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/chapter=01_hall#retrieval/);
    await expect(page.getByRole("heading", { name: "Retrieval" })).toBeInViewport();
  });

  test("the planner's LLM usage opens in a sheet with its calls, tokens and plan health", async ({
    page,
  }) => {
    await mockPlannerRoutes(page);
    await page.goto("/dd/planner?slug=alpha");
    await expect(page.getByText("Plan health:")).toBeVisible();
    await expect(page.getByText("Chapter proposals fell back to deterministic titles.")).toBeVisible();

    await page.getByTestId("planner-usage").click();
    const sheet = page.getByRole("dialog", { name: "Planner LLM usage" });
    await expect(sheet.getByText("9,000").first()).toBeVisible();
    await expect(sheet.getByRole("row", { name: /Off-topic filter/ })).toContainText("12");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
  });

  test("pipeline stage gates starts while the synth stage is running", async ({ page }) => {
    await mockPipelineRoutes(page, { synthBusy: true });
    await page.goto("/dd/pipeline?slug=alpha");
    await expect(page.getByTestId("planner-start")).toBeDisabled();
    await expect(page.getByTestId("synth-start")).toBeDisabled();
  });

  for (const scheme of ["dark", "light"] as const) {
    test(`the node drawer has no WCAG 2.2 AA violations on any tab (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await mockPlannerRoutes(page);
      await page.goto("/dd/planner?slug=alpha");
      await page.getByRole("button", { name: /Off-topic filter/ }).click();
      const drawer = page.getByRole("dialog", { name: "Off-topic filter" });
      for (const tab of ["Overview", "Activity", "Raw I/O"]) {
        await drawer.getByRole("tab", { name: new RegExp(tab) }).click();
        const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
        expect(
          results.violations,
          `${tab}: ${JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`,
        ).toEqual([]);
      }
    });
  }

  for (const scheme of ["dark", "light"] as const) {
    test(`the usage, source and search sheets have no WCAG 2.2 AA violations (${scheme})`, async ({
      page,
    }) => {
      await page.emulateMedia({ colorScheme: scheme });
      const check = async (label: string) => {
        const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
        expect(
          results.violations,
          `${label}: ${JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`,
        ).toEqual([]);
      };

      await mockPlannerRoutes(page);
      await page.goto("/dd/planner?slug=alpha");
      await page.getByTestId("planner-usage").click();
      await expect(
        page.getByRole("dialog", { name: "Planner LLM usage" }).getByText("9,000").first(),
      ).toBeVisible();
      await check("usage sheet");

      await mockSynthRoutes(page);
      await page.goto("/dd/study?slug=alpha");
      await page.getByText("Sources for this section (1)").click();
      await page.getByRole("button", { name: "0000-quickstart.md" }).click();
      await expect(page.getByRole("dialog", { name: "Quickstart" })).toBeVisible();
      await check("source sheet");
      await page.keyboard.press("Escape");

      await page.getByRole("button", { name: /Search chapters/ }).click();
      const search = page.getByRole("dialog", { name: "Search all chapters" });
      await search.getByRole("combobox").fill("verifiers");
      await expect(search.getByRole("option").first()).toBeVisible();
      await check("search dialog");
    });
  }

  test("no stage page scrolls sideways or ships text under 12px", async ({ page }) => {
    await mockPipelineRoutes(page, { synthBusy: false });
    for (const route of [
      "/dd",
      "/dd/ingestion?slug=alpha",
      "/dd/pipeline?slug=alpha",
      "/dd/planner?slug=alpha",
      "/dd/synth?slug=alpha",
      "/dd/study?slug=alpha",
    ]) {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1, name: "Docs Distiller" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const { overflow, small } = await page.evaluate(() => {
        const smallText = Array.from(document.querySelectorAll<HTMLElement>("body *")).filter((element) => {
          const hasText = Array.from(element.childNodes).some(
            (node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "",
          );
          // KaTeX lays math out with its own sizes (scripts, zero-width struts); the floor is for interface text.
          return (
            hasText &&
            !element.closest(".katex") &&
            Number.parseFloat(getComputedStyle(element).fontSize) < 12
          );
        });
        return {
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          small: smallText.map((element) => `${element.tagName}.${element.className}`),
        };
      });
      expect(overflow, `${route} scrolls sideways`).toBeLessThanOrEqual(0);
      expect(small, `${route} has text under 12px`).toEqual([]);
    }
  });

  // One test per page and scheme: five pages in a single test sat at the 30 s limit on a loaded machine.
  const AXE_PAGES: { route: string; ready: (page: Page) => Promise<void> }[] = [
    { route: "/dd", ready: (page) => expect(page.getByText("2 of 2 frameworks shown")).toBeVisible() },
    {
      route: "/dd/ingestion?slug=alpha",
      ready: (page) => expect(page.getByText("2 of 2 pages")).toBeVisible(),
    },
    {
      route: "/dd/pipeline?slug=alpha",
      ready: (page) => expect(page.getByLabel("Auto-chain")).toBeVisible(),
    },
    {
      route: "/dd/synth?slug=alpha",
      ready: (page) => expect(page.getByTestId("chstrip-counter")).toHaveText("1 / 2"),
    },
    {
      route: "/dd/study?slug=alpha",
      ready: (page) =>
        expect(page.getByText("Verifiers cut hallucination rates before collapse.")).toBeVisible(),
    },
  ];

  for (const scheme of ["dark", "light"] as const) {
    for (const { route, ready } of AXE_PAGES) {
      test(`${route.split("?")[0]} has no WCAG 2.2 AA violations (${scheme})`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: scheme });
        await mockPipelineRoutes(page, { synthBusy: false });
        await page.goto(route);
        await ready(page);
        const results = await new AxeBuilder({ page }).withTags(WCAG).analyze();
        expect(
          results.violations,
          `${route}: ${JSON.stringify(results.violations.map((v) => [v.id, v.nodes.map((n) => n.target)]))}`,
        ).toEqual([]);
      });
    }
  }
});
