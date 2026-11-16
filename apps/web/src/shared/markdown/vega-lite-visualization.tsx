"use client";

import type { VisualizationSpec } from "vega-embed";
import { useEffect, useMemo, useRef, useState } from "react";

import { CodeBlock } from "./code-block";
import { VisualizationFrame } from "./visualization-frame";

const SPEC_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json";
const MAX_SPEC_CHARS = 30_000;
const MAX_DATA_ROWS = 120;
const ROOT_KEYS = new Set([
  "$schema",
  "title",
  "description",
  "data",
  "mark",
  "encoding",
  "projection",
  "width",
  "height",
]);
const BLOCKED_KEYS = new Set([
  "url",
  "href",
  "signal",
  "expr",
  "calculate",
  "transform",
  "params",
  "lookup",
  "datasets",
  "usermeta",
  "formatType",
  "condition",
  "events",
  "on",
  "init",
  "update",
]);
const MARKS = new Set(["bar", "line", "area", "point", "arc", "rect", "geoshape"]);
const CHANNELS = new Set(["x", "xOffset", "y", "color", "theta", "order", "shape", "tooltip"]);
const ENCODING_KEYS = new Set(["field", "type", "title", "legend", "sort", "stack"]);
const DATA_ROW_KEYS = new Set(["x", "y", "value", "series", "order", "type", "properties", "geometry"]);

type RenderState = "loading" | "ready" | "failed";

export function VegaLiteVisualization({
  source,
  onExpand,
}: {
  source: string;
  onExpand?: (label: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const spec = useMemo(() => parseSafeVegaLite(source), [source]);
  const label = spec && isMapSpec(spec) ? "Map" : "Chart";
  const [state, setState] = useState<RenderState>(spec ? "loading" : "failed");
  const [width, setWidth] = useState(640);

  useEffect(() => {
    const element = container.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const nextWidth = Math.max(1, Math.min(1200, Math.floor(entry.contentRect.width)));
      setWidth((current) => (current === nextWidth ? current : nextWidth));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!spec || !container.current) return;
    let cancelled = false;
    let finalize: (() => void) | undefined;
    setState("loading");
    void (async () => {
      try {
        const { default: embed } = await import("vega-embed");
        const result = await embed(container.current as HTMLDivElement, spec, {
          mode: "vega-lite",
          actions: false,
          renderer: "svg",
          tooltip: false,
          width,
        });
        if (cancelled) result.finalize();
        else {
          finalize = result.finalize;
          setState("ready");
        }
      } catch {
        if (!cancelled) setState("failed");
      }
    })();
    return () => {
      cancelled = true;
      finalize?.();
      container.current?.replaceChildren();
    };
  }, [spec, width]);

  if (!spec || state === "failed") {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground" role="status">
          {label} specification is unsupported or could not be rendered; showing its Markdown source.
        </p>
        <CodeBlock language="json" raw={source}>
          <code>{source}</code>
        </CodeBlock>
      </div>
    );
  }

  return (
    <VisualizationFrame label={label} onExpand={onExpand ? () => onExpand(label) : undefined}>
      <figure className="md-visualization" role="img" aria-label={label}>
        <div className="md-visualization-canvas" ref={container} />
        {state === "loading" ? (
          <figcaption className="text-sm text-muted-foreground">Rendering {label.toLowerCase()}…</figcaption>
        ) : null}
      </figure>
    </VisualizationFrame>
  );
}

function parseSafeVegaLite(source: string): VisualizationSpec | null {
  if (source.length > MAX_SPEC_CHARS) return null;
  let candidate: unknown;
  try {
    candidate = JSON.parse(source);
  } catch {
    return null;
  }
  if (
    !isRecord(candidate) ||
    hasBlockedKey(candidate) ||
    Object.keys(candidate).some((key) => !ROOT_KEYS.has(key))
  )
    return null;
  if (candidate.$schema !== SPEC_SCHEMA || !isRecord(candidate.data) || !Array.isArray(candidate.data.values))
    return null;
  if (
    Object.keys(candidate.data).some((key) => key !== "values") ||
    candidate.data.values.length > MAX_DATA_ROWS
  )
    return null;
  if (
    !isSafeData(candidate.data.values) ||
    !isSafeMark(candidate.mark) ||
    !isSafeEncoding(candidate.encoding)
  )
    return null;
  if (candidate.projection !== undefined && !isSafeProjection(candidate.projection)) return null;
  if (!isBoundedDimension(candidate.width) || !isBoundedDimension(candidate.height)) return null;
  if (candidate.title !== undefined && candidate.title !== null && typeof candidate.title !== "string")
    return null;
  if (candidate.description !== undefined && typeof candidate.description !== "string") return null;
  return candidate as VisualizationSpec;
}

function isSafeData(values: unknown[]): boolean {
  if (!values.length || !values.every(isRecord)) return false;
  for (const row of values) {
    if (Object.keys(row).some((key) => !DATA_ROW_KEYS.has(key))) return false;
    if (row.type === "Feature") {
      if (!isGeoJsonFeature(row)) return false;
    } else if (!Object.values(row).every(isPrimitive)) return false;
  }
  return true;
}

function isGeoJsonFeature(value: Record<string, unknown>): boolean {
  if (Object.keys(value).some((key) => !["type", "properties", "geometry"].includes(key))) return false;
  if (!isRecord(value.properties) || !isRecord(value.geometry)) return false;
  if (Object.keys(value.properties).some((key) => !["label", "category"].includes(key))) return false;
  if (!Object.values(value.properties).every((item) => typeof item === "string")) return false;
  if (Object.keys(value.geometry).some((key) => !["type", "coordinates"].includes(key))) return false;
  const type = value.geometry.type;
  const coordinates = value.geometry.coordinates;
  if (!(["Point", "LineString", "Polygon"] as unknown[]).includes(type)) return false;
  return validateCoordinates(type as string, coordinates);
}

function validateCoordinates(type: string, value: unknown): boolean {
  if (type === "Polygon" && (!Array.isArray(value) || value.length !== 1)) return false;
  const pairs: unknown[] =
    type === "Point"
      ? [value]
      : type === "Polygon" && Array.isArray(value)
        ? (value[0] as unknown[])
        : (value as unknown[]);
  if (!Array.isArray(pairs) || pairs.length === 0 || pairs.length > 500) return false;
  if (type === "Point" && pairs.length !== 1) return false;
  if ((type === "LineString" && pairs.length < 2) || (type === "Polygon" && pairs.length < 4)) return false;
  if (
    !pairs.every(
      (pair) =>
        Array.isArray(pair) &&
        pair.length === 2 &&
        typeof pair[0] === "number" &&
        Number.isFinite(pair[0]) &&
        Math.abs(pair[0]) <= 180 &&
        typeof pair[1] === "number" &&
        Number.isFinite(pair[1]) &&
        Math.abs(pair[1]) <= 90,
    )
  )
    return false;
  if (type === "Polygon") {
    const first = pairs[0] as number[];
    const last = pairs[pairs.length - 1] as number[];
    if (first[0] !== last[0] || first[1] !== last[1]) return false;
  }
  return true;
}

function isSafeMark(value: unknown): boolean {
  if (typeof value === "string") return MARKS.has(value);
  return (
    isRecord(value) &&
    value.type === "geoshape" &&
    Object.keys(value).every((key) => ["type", "stroke", "strokeWidth"].includes(key)) &&
    (value.stroke === undefined ||
      (typeof value.stroke === "string" && /^#[0-9a-f]{3,8}$/i.test(value.stroke))) &&
    (value.strokeWidth === undefined ||
      (typeof value.strokeWidth === "number" && value.strokeWidth >= 0 && value.strokeWidth <= 10))
  );
}

function isSafeEncoding(value: unknown): boolean {
  if (!isRecord(value) || Object.keys(value).some((key) => !CHANNELS.has(key))) return false;
  return Object.entries(value).every(([channelName, channel]) => {
    const fields = Array.isArray(channel) ? channel : [channel];
    if (Array.isArray(channel) && channelName !== "tooltip") return false;
    return fields.every((field) => {
      if (!isRecord(field) || Object.keys(field).some((key) => !ENCODING_KEYS.has(key))) return false;
      if (typeof field.field !== "string" || field.field.length > 80) return false;
      if (
        field.type !== undefined &&
        !["nominal", "ordinal", "quantitative", "temporal", "geojson"].includes(field.type as string)
      )
        return false;
      if (field.title !== undefined && field.title !== null && typeof field.title !== "string") return false;
      if (field.sort !== undefined && (!Array.isArray(field.sort) || !field.sort.every(isPrimitive)))
        return false;
      if (field.stack !== undefined && field.stack !== null) return false;
      if (
        field.legend !== undefined &&
        field.legend !== null &&
        (!isRecord(field.legend) ||
          Object.keys(field.legend).some((key) => key !== "title") ||
          (field.legend.title !== undefined && typeof field.legend.title !== "string"))
      )
        return false;
      return true;
    });
  });
}

function isSafeProjection(value: unknown): boolean {
  return isRecord(value) && value.type === "equalEarth" && Object.keys(value).every((key) => key === "type");
}

function isBoundedDimension(value: unknown): boolean {
  return typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 1200;
}

function isPrimitive(value: unknown): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    !Object.keys(value).some((key) => BLOCKED_KEYS.has(key))
  );
}

function hasBlockedKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasBlockedKey);
  if (typeof value !== "object" || value === null) return false;
  return Object.entries(value).some(([key, child]) => BLOCKED_KEYS.has(key) || hasBlockedKey(child));
}

function isMapSpec(spec: VisualizationSpec): boolean {
  const mark = "mark" in spec ? spec.mark : undefined;
  return mark === "geoshape" || (isRecord(mark) && mark.type === "geoshape");
}
