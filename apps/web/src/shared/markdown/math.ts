/** LaTeX as authors really write it, made digestible for `remark-math`. Documentation uses `\[…\]`, `\(…\)` and bare
 * `\begin{align}…\end{align}` blocks without `$$` fences, and KaTeX has no `align` (its name for it is `aligned`).
 * Fenced code and inline code are left exactly as they are. */

const FENCE = /^\s*(```+|~~~+)/;

/** KaTeX's name for the LaTeX `align` environments. */
const fixAlign = (math: string): string =>
  math.replace(/\\begin\{align\*?\}/g, "\\begin{aligned}").replace(/\\end\{align\*?\}/g, "\\end{aligned}");

function rewrite(text: string): string {
  const held: string[] = [];
  const hold = (value: string) => `@@HELD${held.push(value) - 1}@@`;

  let out = text.replace(/`[^`\n]*`/g, hold); // inline code is never math
  out = out.replace(/\$\$[\s\S]+?\$\$/g, (match) => hold(fixAlign(match))); // already fenced: only fix align
  // `\[ … \]` alone on its line(s) is display math, which `remark-math` wants on lines of its own; inside a
  // sentence it stays an inline span.
  out = out.replace(/\\\[([\s\S]+?)\\\]/g, (match, body: string, offset: number, whole: string) => {
    const alone =
      /(?:^|\n)[ \t]*$/.test(whole.slice(0, offset)) &&
      /^[ \t]*(?:\n|$)/.test(whole.slice(offset + match.length));
    const math = fixAlign(body).trim();
    return hold(alone ? `$$\n${math}\n$$` : `$$${math}$$`);
  });
  out = out.replace(/\\\(([\s\S]+?)\\\)/g, (_match, body: string) => hold(`$${body.trim()}$`));
  out = out.replace(/\\begin\{([a-zA-Z*]+)\}[\s\S]+?\\end\{\1\}/g, (match) =>
    hold(`$$\n${fixAlign(match)}\n$$`),
  );

  // A held value can contain another placeholder (display math around inline code): unwrap until none are left.
  for (let pass = 0; pass < 4 && /@@HELD\d+@@/.test(out); pass += 1) {
    out = out.replace(/@@HELD(\d+)@@/g, (_match, index: string) => held[Number(index)] ?? "");
  }
  return out;
}

export function normalizeMath(markdown: string): string {
  const out: string[] = [];
  let block: string[] = [];
  let fence: string | null = null;

  const flush = () => {
    if (block.length > 0) out.push(rewrite(block.join("\n")));
    block = [];
  };

  for (const line of markdown.split("\n")) {
    const marker = FENCE.exec(line)?.[1] ?? null;
    if (fence === null && marker) {
      flush();
      fence = marker;
      out.push(line);
    } else if (fence !== null) {
      out.push(line);
      if (marker?.startsWith(fence)) fence = null; // the closing fence uses the same character, at least as long
    } else {
      block.push(line);
    }
  }
  flush();
  return out.join("\n");
}
