import { describe, expect, it } from "vitest";

import type { ReportChart, ReportDiagram } from "../entities";
import { buildChartPlotModel, diagramMermaidSource } from "./visualization";

const diagram: ReportDiagram = {
  title: "A small model",
  accessible_description: "Two ideas and the link between them.",
  nodes: [
    {
      node_id: "idea-a",
      label: 'First "idea" <one>',
      attribution: "book",
      learning_unit_ids: [],
      evidence: [],
    },
    {
      node_id: "idea-b",
      label: "Second idea",
      attribution: "agent_inference",
      learning_unit_ids: [],
      evidence: [],
    },
  ],
  edges: [
    {
      source_id: "idea-a",
      target_id: "idea-b",
      relation: "supports",
      attribution: "book",
      learning_unit_ids: [],
      evidence: [],
    },
    {
      source_id: "missing",
      target_id: "idea-b",
      relation: "sequence",
      attribution: "book",
      learning_unit_ids: [],
      evidence: [],
    },
  ],
};

function chart(chartType: ReportChart["chart_type"], rows: ReportChart["dataset"]["rows"]): ReportChart {
  return {
    title: "Example values",
    chart_type: chartType,
    x_field: "period",
    y_fields: ["score"],
    caption: "Scores over time.",
    accessible_description: "A simple score chart.",
    learning_unit_ids: [],
    dataset: {
      dataset_id: "scores",
      title: "Scores",
      columns: [
        { name: "period", value_type: "number" },
        { name: "score", value_type: "number" },
      ],
      rows,
      transformations: [],
      assumptions: [],
      learning_unit_ids: [],
    },
  };
}

describe("Learning Guide visual models", () => {
  it("builds safe Mermaid node IDs and omits relationships with missing endpoints", () => {
    const source = diagramMermaidSource(diagram);
    expect(source).toContain('node0["First #34;idea#34; #60;one#62;"]');
    expect(source).toContain("node0 -->|supports| node1");
    expect(source).not.toContain("missing");
  });

  it("builds category bar scales that include zero for positive and negative data", () => {
    const model = buildChartPlotModel(
      chart("bar", [
        { values: { period: "A", score: -3 }, learning_unit_ids: [], evidence: [] },
        { values: { period: "B", score: 5 }, learning_unit_ids: [], evidence: [] },
      ]),
    );
    expect(model?.series[0]?.points.map((point) => point.x)).toEqual([0, 1]);
    expect(model?.yMin).toBe(-3);
    expect(model?.yMax).toBe(5);
    expect(model?.xTicks.map((tick) => tick.label)).toEqual(["A", "B"]);
  });

  it("orders numeric line data by its x values and handles table-only charts without a plot", () => {
    const model = buildChartPlotModel(
      chart("line", [
        { values: { period: 30, score: 3 }, learning_unit_ids: [], evidence: [] },
        { values: { period: 10, score: 1 }, learning_unit_ids: [], evidence: [] },
        { values: { period: 20, score: 2 }, learning_unit_ids: [], evidence: [] },
      ]),
    );
    expect(model?.series[0]?.points.map((point) => point.label)).toEqual(["10", "20", "30"]);
    expect(model?.series[0]?.points.map((point) => point.x)).toEqual([0, 0.5, 1]);
    expect(buildChartPlotModel(chart("table", []))).toBeNull();
  });

  it("keeps a table-only fallback when the visual series has no numeric values", () => {
    expect(
      buildChartPlotModel(
        chart("scatter", [
          { values: { period: 1, score: "not numeric" }, learning_unit_ids: [], evidence: [] },
        ]),
      ),
    ).toBeNull();
  });
});
