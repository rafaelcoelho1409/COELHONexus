/** The "LLM calls and tokens" table of a node's drawer: what this node used next to what the whole run used.
 * Counts only; the models behind the calls are deliberately not shown. */

import type { NodeUsageTable } from "../domain/usage";

export function NodeUsageTableView({ table }: { table: NodeUsageTable }) {
  return (
    <section
      aria-label="LLM calls and tokens"
      // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide table scrolls, so keyboard users must be able to focus it to scroll it (WCAG 2.1.1)
      tabIndex={0}
      className="overflow-x-auto rounded-sm border border-border"
    >
      <table className="w-full border-collapse text-sm">
        <caption className="border-b border-border px-3 py-2 text-left text-xs font-medium text-muted-foreground">
          LLM calls and tokens
        </caption>
        <thead>
          <tr className="border-b border-border text-left text-xs text-muted-foreground">
            <th scope="col" className="px-3 py-2">
              <span className="sr-only">Figure</span>
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              This node
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              {table.runLabel}
            </th>
            <th scope="col" className="px-3 py-2 text-right font-medium">
              Share
            </th>
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row) => (
            <tr key={row.label} className="border-b border-border last:border-b-0">
              <th scope="row" className="px-3 py-1.5 text-left font-normal">
                {row.label}
              </th>
              <td className="px-3 py-1.5 text-right tabular-nums">{row.node}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{row.run}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted-foreground">{row.share}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
