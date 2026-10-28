/** "By model": calls and input, output and reasoning tokens for each model that answered, the model named as
 * the LLM endpoint reported it. Shown in a node's drawer and in the usage sheets. */

import { fmtInt } from "../domain/format";
import type { ModelUsageRow } from "../domain/usage";

const NUMBER_HEAD = "px-3 py-2 text-right font-medium";
const NUMBER_CELL = "px-3 py-1.5 text-right tabular-nums";

export function ModelUsageTableView({ rows }: { rows: readonly ModelUsageRow[] }) {
  if (rows.length === 0) return null;
  return (
    <section
      aria-label="Usage by model"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
      tabIndex={0}
      className="overflow-x-auto rounded-sm border border-border"
    >
      <table className="w-full border-collapse text-sm">
        <caption className="border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground">
          By model
        </caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th scope="col" className="px-3 py-2 font-medium">
              Model
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Calls
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Input tokens
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Output tokens
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Reasoning
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.model} className="border-b border-border last:border-b-0">
              <th scope="row" className="px-3 py-1.5 text-left font-mono text-xs font-normal break-all">
                {row.model}
              </th>
              <td className={NUMBER_CELL}>{fmtInt(row.calls)}</td>
              <td className={NUMBER_CELL}>{fmtInt(row.tokens_in)}</td>
              <td className={NUMBER_CELL}>{fmtInt(row.tokens_out)}</td>
              <td className={NUMBER_CELL}>{fmtInt(row.reasoning_tokens)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
