import type { ReportChart, ReportDiagram } from "../entities";

type PlotPoint = {
  x: number;
  label: string;
  rowIndex: number;
  value: number;
};

type PlotSeries = {
  name: string;
  points: PlotPoint[];
};

export type ChartPlotModel = {
  xTicks: { x: number; label: string }[];
  yTicks: number[];
  yMin: number;
  yMax: number;
  series: PlotSeries[];
};

export function diagramMermaidSource(diagram: ReportDiagram): string {
  const nodeIds = new Map<string, string>();
  const lines = ["flowchart TD"];

  diagram.nodes.forEach((node, index) => {
    const mermaidId = `node${index}`;
    if (!nodeIds.has(node.node_id)) nodeIds.set(node.node_id, mermaidId);
    lines.push(`  ${mermaidId}["${escapeMermaidLabel(node.label)}"]`);
  });

  for (const edge of diagram.edges) {
    const source = nodeIds.get(edge.source_id);
    const target = nodeIds.get(edge.target_id);
    if (!source || !target) continue;
    lines.push(`  ${source} -->|${edge.relation.replaceAll("_", " ")}| ${target}`);
  }

  return lines.join("\n");
}

export function buildChartPlotModel(chart: ReportChart): ChartPlotModel | null {
  if (chart.chart_type === "table" || chart.dataset.rows.length === 0 || chart.y_fields.length === 0)
    return null;

  const rows = chart.dataset.rows.map((row, index) => {
    const xValue = row.values[chart.x_field];
    const yValues = chart.y_fields.map((field) => numericValue(row.values[field]));
    return {
      index,
      label: String(xValue ?? `Row ${index + 1}`),
      xValue: numericValue(xValue),
      yValues,
    };
  });
  const numericX = chart.chart_type !== "bar" && rows.every((row) => row.xValue !== null);
  const numericXValues = rows.flatMap((row) => (row.xValue === null ? [] : [row.xValue]));
  const minimumX = numericX ? Math.min(...numericXValues) : 0;
  const maximumX = numericX ? Math.max(...numericXValues) : 0;
  const xPosition = (value: number | null, index: number, count: number) => {
    if (!numericX || value === null || minimumX === maximumX) return categoryX(index, count);
    return (value - minimumX) / (maximumX - minimumX);
  };
  const orderedRows =
    numericX && chart.chart_type === "line"
      ? [...rows].sort((left, right) => (left.xValue ?? 0) - (right.xValue ?? 0))
      : rows;
  const series = chart.y_fields
    .map((name, seriesIndex) => ({
      name,
      points: orderedRows.flatMap((row, rowIndex) => {
        const value = row.yValues[seriesIndex];
        if (value === null || value === undefined) return [];
        return [
          {
            x: xPosition(row.xValue, rowIndex, orderedRows.length),
            label: row.label,
            rowIndex: row.index,
            value,
          },
        ];
      }),
    }))
    .filter((item) => item.points.length > 0);
  const values = series.flatMap((item) => item.points.map((point) => point.value));
  if (series.length === 0 || values.length === 0) return null;

  let yMin = values.reduce((minimum, value) => Math.min(minimum, value), Number.POSITIVE_INFINITY);
  let yMax = values.reduce((maximum, value) => Math.max(maximum, value), Number.NEGATIVE_INFINITY);
  if (chart.chart_type === "bar") {
    yMin = Math.min(0, yMin);
    yMax = Math.max(0, yMax);
  } else {
    const padding = yMax === yMin ? Math.max(Math.abs(yMin) * 0.1, 1) : (yMax - yMin) * 0.08;
    yMin -= padding;
    yMax += padding;
  }
  if (yMin === yMax) yMax = yMin + 1;

  const labelRows = numericX && chart.chart_type === "line" ? orderedRows : rows;
  const tickStride = Math.max(1, Math.ceil(labelRows.length / 6));
  const xTicks = labelRows.flatMap((row, index) => {
    if (index % tickStride !== 0 && index !== labelRows.length - 1) return [];
    const x = xPosition(row.xValue, index, labelRows.length);
    return [{ x, label: shortenLabel(row.label) }];
  });

  return {
    xTicks,
    yTicks: Array.from({ length: 5 }, (_, index) => yMin + ((yMax - yMin) * index) / 4),
    yMin,
    yMax,
    series,
  };
}

function numericValue(value: string | number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function categoryX(index: number, count: number): number {
  return count <= 1 ? 0.5 : index / (count - 1);
}

function shortenLabel(label: string): string {
  return label.length <= 16 ? label : `${label.slice(0, 13)}…`;
}

function escapeMermaidLabel(label: string): string {
  return label
    .replaceAll("&", "#38;")
    .replaceAll('"', "#34;")
    .replaceAll("<", "#60;")
    .replaceAll(">", "#62;")
    .replaceAll("`", "#96;")
    .replace(/[\r\n]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
