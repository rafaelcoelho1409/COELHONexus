/** Terminal output with ANSI colour codes → plain segments the UI can colour. Only the SGR "select graphic
 * rendition" codes (colours, bold, dim, italic, underline) are honoured; every other escape sequence is dropped. */

type AnsiTone = "red" | "green" | "yellow" | "blue" | "magenta" | "cyan" | "muted";

export type AnsiSegment = {
  text: string;
  tone?: AnsiTone;
  bold?: boolean;
  dim?: boolean;
  italic?: boolean;
  underline?: boolean;
};

/** SGR foreground code (30-37 and the bright 90-97) → tone. Black/white/grey read as muted: a fixed black or
 * white would vanish on one of the two themes. */
const FOREGROUND: Record<number, AnsiTone> = {
  30: "muted",
  31: "red",
  32: "green",
  33: "yellow",
  34: "blue",
  35: "magenta",
  36: "cyan",
  37: "muted",
};

const ESCAPE = String.fromCharCode(27);
const SEQUENCE = new RegExp(`${ESCAPE}\\[([0-9;]*)([A-Za-z])`, "g");
// The title-setting OSC sequence first (it ends at BEL), then any other two-character escape.
const OTHER_ESCAPES = new RegExp(`${ESCAPE}\\][^\\u0007]*\\u0007|${ESCAPE}[@-Z\\\\-_]`, "g");

type Style = Omit<AnsiSegment, "text">;

function apply(style: Style, codes: number[]): Style {
  let next = { ...style };
  for (const code of codes.length > 0 ? codes : [0]) {
    if (code === 0) next = {};
    else if (code === 1) next.bold = true;
    else if (code === 2) next.dim = true;
    else if (code === 3) next.italic = true;
    else if (code === 4) next.underline = true;
    else if (code === 22) {
      next.bold = undefined;
      next.dim = undefined;
    } else if (code === 23) next.italic = undefined;
    else if (code === 24) next.underline = undefined;
    else if (code === 39) next.tone = undefined;
    else if (code >= 30 && code <= 37) next.tone = FOREGROUND[code];
    else if (code >= 90 && code <= 97) next.tone = FOREGROUND[code - 60];
  }
  return next;
}

const sameStyle = (a: Style, b: Style): boolean =>
  a.tone === b.tone &&
  !a.bold === !b.bold &&
  !a.dim === !b.dim &&
  !a.italic === !b.italic &&
  !a.underline === !b.underline;

export function parseAnsi(input: string): AnsiSegment[] {
  const text = input.replace(OTHER_ESCAPES, "");
  const segments: AnsiSegment[] = [];
  let style: Style = {};
  let cursor = 0;
  const push = (end: number) => {
    const chunk = text.slice(cursor, end);
    if (!chunk) return;
    const last = segments.at(-1);
    // Dropped sequences (cursor moves) must not split a run that is styled the same.
    if (last && sameStyle(last, style)) last.text += chunk;
    else segments.push({ ...style, text: chunk });
  };
  for (const match of text.matchAll(SEQUENCE)) {
    push(match.index);
    cursor = match.index + match[0].length;
    if (match[2] === "m") style = apply(style, (match[1] ?? "").split(";").filter(Boolean).map(Number));
  }
  push(text.length);
  return segments;
}
