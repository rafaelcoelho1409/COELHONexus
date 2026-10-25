/** What the drawer's Overview tab shows for one node, as plain data (the component only lays it out). */

export type OverviewCard = { label: string; value: string; sub?: string };

/** A table cell: the text shown, and an optional value to sort the column by. */
export type OverviewCell = { text: string; sort?: string | number; tone?: "good" | "bad" };

export type OverviewSection =
  | {
      kind: "table";
      title: string;
      columns: string[];
      rows: OverviewCell[][];
      /** Columns the reader may sort (by index). */
      sortable?: number[];
    }
  | {
      kind: "bars";
      title: string;
      rows: Array<{ id: string; label: string; value: number; valueLabel: string }>;
    }
  | { kind: "list"; title: string; items: string[] }
  | { kind: "figures"; title: string; cells: Array<{ key: string; value: string }> }
  | { kind: "note"; title: string; text: string };

export type NodeOverview = {
  cards: OverviewCard[];
  sections: OverviewSection[];
  footer: string[];
  /** Set instead of cards when the node has not reported anything yet. */
  empty?: string;
};

export const emptyOverview = (message: string): NodeOverview => ({
  cards: [],
  sections: [],
  footer: [],
  empty: message,
});
