/** Book Distiller — zod schemas for what the generated client does not type: the SSE events of an extraction and the
 * upload's answer (read outside the generated client, which has no upload progress). Every field the UI relies on has
 * a fallback, so one malformed value never breaks a stream. */

import { z } from "zod";

const count = z.number().catch(0);

const statsSchema = z.looseObject({
  chars: count,
  headings: count,
  tables: count,
  pictures: count,
  formulas: count,
});
export type PageStats = z.infer<typeof statsSchema>;

export const pageBodySchema = z.object({
  book_id: z.string(),
  page: z.number(),
  markdown: z.string(),
});

const ZERO_STATS: PageStats = { chars: 0, headings: 0, tables: 0, pictures: 0, formulas: 0 };

const outlineEntrySchema = z.looseObject({
  level: z.number().catch(1),
  title: z.string().catch(""),
  page: z.number(),
});
export type OutlineEntry = z.infer<typeof outlineEntrySchema>;

const chapterSchema = z.looseObject({
  title: z.string().catch(""),
  first_page: z.number(),
  last_page: z.number(),
});
export type ChapterRange = z.infer<typeof chapterSchema>;

const startedSchema = z.looseObject({
  kind: z.literal("started"),
  title: z.string().catch(""),
  n_pages: z.number().catch(0),
  outline: z.array(outlineEntrySchema).catch([]),
  chapters: z.array(chapterSchema).catch([]),
  resumed: z.boolean().catch(false),
  completed: z.number().catch(0),
});

const pageSchema = z.looseObject({
  kind: z.literal("page"),
  page: z.number(),
  markdown: z.string().catch(""),
  /** The server cut the text for the stream; the full content unit is `GET …/pages/{n}`. */
  truncated: z.boolean().catch(false),
  stats: statsSchema.catch(ZERO_STATS),
  figures: z.array(z.string()).catch([]),
  section_path: z.array(z.string()).catch([]),
  completed: z.number().catch(0),
  total: z.number().catch(0),
});

const sliceDoneSchema = z.looseObject({
  kind: z.literal("slice_done"),
  first: z.number(),
  last: z.number(),
  seconds: z.number().catch(0),
});

function terminalSchema<K extends "done" | "failed" | "cancelled">(kind: K) {
  return z.looseObject({
    kind: z.literal(kind),
    pages: z.number().catch(0),
    total: z.number().catch(0),
    error: z.string().nullable().catch(null),
  });
}

export const bdEventSchema = z.discriminatedUnion("kind", [
  startedSchema,
  pageSchema,
  sliceDoneSchema,
  terminalSchema("done"),
  terminalSchema("failed"),
  terminalSchema("cancelled"),
]);
export type BdEvent = z.infer<typeof bdEventSchema>;
export type TerminalEvent = Extract<BdEvent, { kind: "done" | "failed" | "cancelled" }>;

/** After a terminal event the stream is over (the server keeps the connection open until the client closes it). */
export function isTerminalEvent(event: BdEvent): event is TerminalEvent {
  return event.kind === "done" || event.kind === "failed" || event.kind === "cancelled";
}

/** POST /api/v1/bd/ingestion — read by hand (XHR, for the upload progress), so parsed here. */
export const startResponseSchema = z.object({
  status: z.enum(["queued", "cached", "locked"]),
  book_id: z.string(),
  run_id: z.string().nullable().catch(null),
  resumed: z.boolean().catch(false),
});
export type StartResponse = z.infer<typeof startResponseSchema>;
