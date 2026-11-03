import type { BdEvent, ChapterRange, OutlineEntry, PageStats, TerminalEvent } from "../entities";
import { type BookStatus, isLive, toBookStatus } from "./books";

export type LivePage = {
  page: number;
  markdown: string;
  truncated: boolean;
  stats: PageStats;
  figures: string[];
  sectionPath: string[];
};

/** What the stream has told this page so far. The server replays the whole history to every new subscriber (a reload,
 * a reconnect), so every field is keyed or maxed — applying the same event twice changes nothing. */
export type RunState = {
  title: string | null;
  nPages: number | null;
  outline: OutlineEntry[];
  chapters: ChapterRange[];
  pages: Record<number, LivePage>;
  newest: number | null;
  completed: number;
  total: number;
  /** `"first-last"` → pages and seconds of that slice; summed for the throughput. */
  slices: Record<string, { pages: number; seconds: number }>;
  terminal: Pick<TerminalEvent, "kind" | "pages" | "total" | "error"> | null;
  canceling: boolean;
};

export const EMPTY_RUN: RunState = {
  title: null,
  nPages: null,
  outline: [],
  chapters: [],
  pages: {},
  newest: null,
  completed: 0,
  total: 0,
  slices: {},
  terminal: null,
  canceling: false,
};

export type RunAction =
  | { type: "event"; event: BdEvent }
  /** A new run was requested: forget the last run's ending (the pages stay; the replay re-sends them anyway). */
  | { type: "restart" }
  | { type: "canceling" }
  | { type: "cancel-timeout" };

export function runReducer(state: RunState, action: RunAction): RunState {
  if (action.type === "restart") return { ...state, terminal: null, canceling: false };
  if (action.type === "canceling") return { ...state, canceling: true };
  if (action.type === "cancel-timeout") return { ...state, canceling: false };

  const { event } = action;
  switch (event.kind) {
    case "started":
      return {
        ...state,
        title: event.title || state.title,
        nPages: event.n_pages > 0 ? event.n_pages : state.nPages,
        outline: event.outline,
        chapters: event.chapters,
        completed: Math.max(state.completed, event.completed),
        terminal: null,
      };
    case "page":
      return {
        ...state,
        pages: {
          ...state.pages,
          [event.page]: {
            page: event.page,
            markdown: event.markdown,
            truncated: event.truncated,
            stats: event.stats,
            figures: event.figures,
            sectionPath: event.section_path,
          },
        },
        newest: Math.max(state.newest ?? 0, event.page),
        completed: Math.max(state.completed, event.completed),
        total: Math.max(state.total, event.total),
      };
    case "slice_done":
      return {
        ...state,
        slices: {
          ...state.slices,
          [`${event.first}-${event.last}`]: {
            pages: Math.max(event.last - event.first + 1, 0),
            seconds: event.seconds,
          },
        },
      };
    default:
      return {
        ...state,
        terminal: { kind: event.kind, pages: event.pages, total: event.total, error: event.error },
        completed: Math.max(state.completed, event.pages),
        total: Math.max(state.total, event.total),
        canceling: false,
      };
  }
}

/** Pages per minute over the slices seen so far, or null before the first one. */
export function pagesPerMinute(slices: RunState["slices"]): number | null {
  let pages = 0;
  let seconds = 0;
  for (const slice of Object.values(slices)) {
    pages += slice.pages;
    seconds += slice.seconds;
  }
  return seconds > 0 && pages > 0 ? (pages / seconds) * 60 : null;
}

/** The status to show: the stream's ending wins, then a live run's progress, then what the manifest says. */
export function effectiveStatus(
  manifestStatus: string | null | undefined,
  progressStatus: string | null | undefined,
  terminal: RunState["terminal"],
): BookStatus {
  if (terminal) return terminal.kind;
  const progress = toBookStatus(progressStatus);
  if (isLive(progress)) return progress;
  return toBookStatus(manifestStatus);
}
