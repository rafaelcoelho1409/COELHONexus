import Link from "next/link";

import { buildChartPlotModel } from "../domain/visualization";
import type { ReportChart as ReportChartData } from "../entities";

const WIDTH = 720;
const HEIGHT = 360;
const LEFT = 64;
const RIGHT = 20;
const TOP = 24;
const BOTTOM = 62;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;
const SERIES_COLORS: readonly [string, ...string[]] = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

export function ReportChart({
  chart,
  pageHref,
}: {
  chart: ReportChartData;
  pageHref: (page: number) => string;
}) {
  const model = buildChartPlotModel(chart);

  return (
    <section aria-label={`Chart: ${chart.title}`} className="border-t border-border pt-4">
      <h4 className="font-medium">{chart.title}</h4>
      <p className="mt-1 text-sm text-muted-foreground">{chart.accessible_description}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {chart.caption} · {chart.chart_type} data
      </p>

      {model ? (
        <figure className="mt-3 min-w-0 rounded-sm border border-border bg-background p-3">
          <svg
            aria-label={`${chart.title}. ${chart.accessible_description}`}
            className="h-auto w-full text-muted-foreground"
            role="img"
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          >
            <title>{chart.title}</title>
            <desc>{chart.accessible_description}</desc>
            {model.yTicks.map((tick) => {
              const y = yPosition(tick, model.yMin, model.yMax);
              return (
                <g key={tick}>
                  <line
                    x1={LEFT}
                    x2={WIDTH - RIGHT}
                    y1={y}
                    y2={y}
                    stroke="currentColor"
                    strokeDasharray="3 5"
                    strokeOpacity="0.24"
                  />
                  <text x={LEFT - 10} y={y + 4} fill="currentColor" fontSize="12" textAnchor="end">
                    {formatNumber(tick)}
                  </text>
                </g>
              );
            })}
            <line
              x1={LEFT}
              x2={LEFT}
              y1={TOP}
              y2={HEIGHT - BOTTOM}
              stroke="currentColor"
              strokeOpacity="0.6"
            />
            <line
              x1={LEFT}
              x2={WIDTH - RIGHT}
              y1={HEIGHT - BOTTOM}
              y2={HEIGHT - BOTTOM}
              stroke="currentColor"
              strokeOpacity="0.6"
            />
            {model.xTicks.map((tick) => {
              const x = xPosition(tick.x);
              return (
                <text
                  key={`${tick.x}-${tick.label}`}
                  x={x}
                  y={HEIGHT - BOTTOM + 22}
                  fill="currentColor"
                  fontSize="12"
                  textAnchor="middle"
                >
                  {tick.label}
                </text>
              );
            })}
            <text
              x={LEFT + PLOT_WIDTH / 2}
              y={HEIGHT - 8}
              fill="currentColor"
              fontSize="12"
              textAnchor="middle"
            >
              {chart.x_field}
            </text>
            <text
              transform={`translate(16 ${TOP + PLOT_HEIGHT / 2}) rotate(-90)`}
              fill="currentColor"
              fontSize="12"
              textAnchor="middle"
            >
              {model.series.map((series) => series.name).join(", ")}
            </text>
            {chart.chart_type === "bar"
              ? model.series.flatMap((series, seriesIndex) =>
                  series.points.map((point) => {
                    const groupWidth = PLOT_WIDTH / Math.max(chart.dataset.rows.length, 1);
                    const barWidth = Math.min(28, (groupWidth * 0.76) / model.series.length);
                    const x =
                      xPosition(point.x) - (barWidth * model.series.length) / 2 + seriesIndex * barWidth;
                    const zeroY = yPosition(0, model.yMin, model.yMax);
                    const valueY = yPosition(point.value, model.yMin, model.yMax);
                    return (
                      <rect
                        key={`${series.name}-${point.rowIndex}`}
                        x={x}
                        y={Math.min(zeroY, valueY)}
                        width={barWidth}
                        height={Math.max(Math.abs(valueY - zeroY), 1)}
                        fill={seriesColor(seriesIndex)}
                        rx="2"
                      />
                    );
                  }),
                )
              : model.series.map((series, seriesIndex) => {
                  const points = series.points.map((point) => ({
                    x: xPosition(point.x),
                    y: yPosition(point.value, model.yMin, model.yMax),
                    rowIndex: point.rowIndex,
                  }));
                  const line = points
                    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x},${point.y}`)
                    .join(" ");
                  return (
                    <g key={series.name}>
                      {chart.chart_type === "line" ? (
                        <path
                          d={line}
                          fill="none"
                          stroke={seriesColor(seriesIndex)}
                          strokeWidth="3"
                          strokeLinejoin="round"
                          strokeLinecap="round"
                        />
                      ) : null}
                      {points.map((point) => (
                        <circle
                          key={`${series.name}-${point.rowIndex}`}
                          cx={point.x}
                          cy={point.y}
                          r="5"
                          fill={seriesColor(seriesIndex)}
                        />
                      ))}
                    </g>
                  );
                })}
          </svg>
          <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-2 text-xs text-muted-foreground">
            {model.series.map((series, index) => (
              <span key={series.name} className="inline-flex items-center gap-2">
                <span
                  aria-hidden
                  className="size-3 rounded-sm"
                  style={{ backgroundColor: seriesColor(index) }}
                />
                {series.name}
              </span>
            ))}
          </figcaption>
        </figure>
      ) : null}

      <section
        className="mt-3 overflow-x-auto rounded-sm border border-border"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a wide data table scrolls horizontally, so keyboard users need to focus it to scroll (WCAG 2.1.1)
        tabIndex={0}
        aria-label={`${chart.title} data table`}
      >
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">{chart.caption}</caption>
          <thead className="bg-muted text-left">
            <tr>
              {chart.dataset.columns.map((column) => (
                <th key={column.name} scope="col" className="px-3 py-2 font-medium">
                  {column.name}
                  {column.unit ? ` · ${column.unit}` : ""}
                </th>
              ))}
              <th scope="col" className="px-3 py-2 font-medium">
                Evidence
              </th>
            </tr>
          </thead>
          <tbody>
            {chart.dataset.rows.map((row) => (
              <tr key={JSON.stringify(row.values)} className="border-t border-border align-top">
                {chart.dataset.columns.map((column) => (
                  <td key={column.name} className="max-w-80 px-3 py-2">
                    {String(row.values[column.name] ?? "—")}
                  </td>
                ))}
                <td className="min-w-40 px-3 py-2">
                  {row.evidence.map((item) => (
                    <p key={`${item.page_number}-${item.quote}`} className="mb-2 last:mb-0 text-xs">
                      “{item.quote}” —{" "}
                      <Link
                        href={pageHref(item.page_number)}
                        className="text-primary-text underline underline-offset-2"
                      >
                        Page {item.page_number}
                      </Link>
                    </p>
                  ))}
                  {row.evidence.length === 0 ? (
                    <span className="text-muted-foreground">No quote linked</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {chart.dataset.transformations.length > 0 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Transformations: {chart.dataset.transformations.join(" · ")}
        </p>
      ) : null}
      {chart.dataset.assumptions.length > 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">
          Assumptions: {chart.dataset.assumptions.join(" · ")}
        </p>
      ) : null}
    </section>
  );
}

function xPosition(value: number): number {
  return LEFT + value * PLOT_WIDTH;
}

function yPosition(value: number, minimum: number, maximum: number): number {
  return TOP + ((maximum - value) / (maximum - minimum)) * PLOT_HEIGHT;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 3 }).format(value);
}

function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length] ?? "var(--chart-1)";
}
