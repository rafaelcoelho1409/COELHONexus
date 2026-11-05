/** What the Agentic AI step changed on a page: the Docling Markdown against the AI-processed Markdown, as a line diff in
 * which a replaced line also says which words changed. Only the changed lines and a little context around them are
 * kept, so a long page with one repaired heading shows that heading, not the page. */

/** `id` is the position in its list, fixed once the diff is built: a stable React key for a list that never reorders. */
export type DiffToken = { id: number; text: string; changed: boolean };

type RowBody =
  | { kind: "same"; text: string }
  | { kind: "removed"; text: string; tokens: DiffToken[] }
  | { kind: "added"; text: string; tokens: DiffToken[] }
  | { kind: "skip"; count: number };
export type DiffRow = RowBody & { id: number };

export type PageDiff = {
  rows: DiffRow[];
  /** Lines taken out plus lines put in. */
  changedLines: number;
  identical: boolean;
};

type Op = { type: "same" | "del" | "ins"; a: number; b: number };

/** The longest-common-subsequence table is quadratic; past this many cells the diff is coarse instead of exact. */
const MAX_TABLE_CELLS = 4_000_000;
const DEFAULT_CONTEXT_LINES = 2;

function lcsOps<T>(a: readonly T[], b: readonly T[]): Op[] {
  const n = a.length;
  const m = b.length;
  if ((n + 1) * (m + 1) > MAX_TABLE_CELLS) {
    return [
      ...a.map((_, index): Op => ({ type: "del", a: index, b: -1 })),
      ...b.map((_, index): Op => ({ type: "ins", a: -1, b: index })),
    ];
  }
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  const cell = (i: number, j: number) => table[i * width + j] ?? 0;
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      table[i * width + j] =
        a[i] === b[j] ? cell(i + 1, j + 1) + 1 : Math.max(cell(i + 1, j), cell(i, j + 1));
    }
  }
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ type: "same", a: i, b: j });
      i++;
      j++;
    } else if (cell(i + 1, j) >= cell(i, j + 1)) {
      ops.push({ type: "del", a: i, b: -1 });
      i++;
    } else {
      ops.push({ type: "ins", a: -1, b: j });
      j++;
    }
  }
  for (; i < n; i++) ops.push({ type: "del", a: i, b: -1 });
  for (; j < m; j++) ops.push({ type: "ins", a: -1, b: j });
  return ops;
}

/** The words of two versions of one line, each marked changed or not (whitespace is kept so the line reads as it was). */
function inlineTokens(before: string, after: string): { before: DiffToken[]; after: DiffToken[] } {
  const left = before.split(/(\s+)/).filter((part) => part !== "");
  const right = after.split(/(\s+)/).filter((part) => part !== "");
  const keptLeft = new Set<number>();
  const keptRight = new Set<number>();
  for (const op of lcsOps(left, right)) {
    if (op.type === "same") {
      keptLeft.add(op.a);
      keptRight.add(op.b);
    }
  }
  return {
    before: left.map((text, index) => ({ id: index, text, changed: !keptLeft.has(index) })),
    after: right.map((text, index) => ({ id: index, text, changed: !keptRight.has(index) })),
  };
}

const whole = (text: string): DiffToken[] => [{ id: 0, text, changed: true }];

export function diffPages(before: string, after: string, contextLines = DEFAULT_CONTEXT_LINES): PageDiff {
  if (before === after) return { rows: [], changedLines: 0, identical: true };
  const left = before.split("\n");
  const right = after.split("\n");

  // Lines both versions share at the start and the end are never the change, so the table only covers the middle.
  let head = 0;
  while (head < left.length && head < right.length && left[head] === right[head]) head++;
  let tail = 0;
  while (
    tail < left.length - head &&
    tail < right.length - head &&
    left[left.length - 1 - tail] === right[right.length - 1 - tail]
  ) {
    tail++;
  }
  const middleLeft = left.slice(head, left.length - tail);
  const middleRight = right.slice(head, right.length - tail);

  const rows: RowBody[] = left.slice(0, head).map((text): RowBody => ({ kind: "same", text }));
  const ops = lcsOps(middleLeft, middleRight);
  let changedLines = 0;
  for (let index = 0; index < ops.length; ) {
    const op = ops[index];
    if (op?.type === "same") {
      rows.push({ kind: "same", text: middleLeft[op.a] ?? "" });
      index++;
      continue;
    }
    const removed: string[] = [];
    const added: string[] = [];
    while (index < ops.length && ops[index]?.type !== "same") {
      const current = ops[index];
      if (current?.type === "del") removed.push(middleLeft[current.a] ?? "");
      else if (current?.type === "ins") added.push(middleRight[current.b] ?? "");
      index++;
    }
    changedLines += removed.length + added.length;
    const paired = Math.min(removed.length, added.length);
    const pairTokens = Array.from({ length: paired }, (_, pair) =>
      inlineTokens(removed[pair] ?? "", added[pair] ?? ""),
    );
    removed.forEach((text, pair) => {
      rows.push({ kind: "removed", text, tokens: pairTokens[pair]?.before ?? whole(text) });
    });
    added.forEach((text, pair) => {
      rows.push({ kind: "added", text, tokens: pairTokens[pair]?.after ?? whole(text) });
    });
  }
  for (const text of left.slice(left.length - tail)) rows.push({ kind: "same", text });

  const shown = collapseUnchanged(rows, contextLines).map((row, id): DiffRow => ({ ...row, id }));
  return { rows: shown, changedLines, identical: false };
}

/** Long stretches of unchanged lines become one `skip` row, leaving `context` lines on each side of a change. */
function collapseUnchanged(rows: RowBody[], context: number): RowBody[] {
  const keep = rows.map(() => false);
  rows.forEach((row, index) => {
    if (row.kind === "same") return;
    for (
      let near = Math.max(0, index - context);
      near <= Math.min(rows.length - 1, index + context);
      near++
    ) {
      keep[near] = true;
    }
  });
  const collapsed: RowBody[] = [];
  let skipped = 0;
  rows.forEach((row, index) => {
    if (keep[index]) {
      if (skipped > 0) collapsed.push({ kind: "skip", count: skipped });
      skipped = 0;
      collapsed.push(row);
    } else {
      skipped++;
    }
  });
  if (skipped > 0) collapsed.push({ kind: "skip", count: skipped });
  return collapsed;
}
