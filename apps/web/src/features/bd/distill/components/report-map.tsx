import Link from "next/link";
import type { ReportMap } from "../entities";

type Feature = ReportMap["geojson"]["features"][number];
type Geometry = Feature["geometry"];
type Position = readonly [number, number];

const WIDTH = 720;
const HEIGHT = 400;
const PADDING = 24;
const MAP_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

export function ReportMapView({ map, pageHref }: { map: ReportMap; pageHref: (page: number) => string }) {
  const positions = map.geojson.features.flatMap((feature) => geometryPositions(feature.geometry));
  if (positions.length === 0) return null;

  const longitudeValues = positions.map(([longitude]) => longitude);
  const latitudeValues = positions.map(([, latitude]) => latitude);
  const minLongitude = Math.min(...longitudeValues);
  const maxLongitude = Math.max(...longitudeValues);
  const minLatitude = Math.min(...latitudeValues);
  const maxLatitude = Math.max(...latitudeValues);
  const longitudeSpan = Math.max(maxLongitude - minLongitude, 0.000001);
  const latitudeSpan = Math.max(maxLatitude - minLatitude, 0.000001);
  const scale = Math.min((WIDTH - PADDING * 2) / longitudeSpan, (HEIGHT - PADDING * 2) / latitudeSpan);
  const offsetX = (WIDTH - longitudeSpan * scale) / 2;
  const offsetY = (HEIGHT - latitudeSpan * scale) / 2;
  const project = ([longitude, latitude]: Position): Position => [
    offsetX + (longitude - minLongitude) * scale,
    offsetY + (maxLatitude - latitude) * scale,
  ];

  return (
    <section aria-label={`Map: ${map.title}`} className="border-t border-border pt-4">
      <h4 className="font-medium">{map.title}</h4>
      <p className="mt-1 text-sm text-muted-foreground">{map.accessible_description}</p>
      <figure className="mt-3 min-w-0 rounded-sm border border-border bg-background p-3">
        <svg
          aria-label={`${map.title}. ${map.accessible_description}`}
          className="h-auto w-full"
          role="img"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        >
          <title>{map.title}</title>
          <desc>{map.accessible_description}</desc>
          <rect
            x={PADDING}
            y={PADDING}
            width={WIDTH - PADDING * 2}
            height={HEIGHT - PADDING * 2}
            fill="none"
            stroke="currentColor"
            strokeOpacity="0.25"
          />
          {map.geojson.features.map((feature, index) => (
            <FeatureGeometry
              key={feature.id}
              feature={feature}
              color={MAP_COLORS[index % MAP_COLORS.length] ?? MAP_COLORS[0]}
              project={project}
            />
          ))}
          <text x={WIDTH / 2} y={HEIGHT - 5} fill="currentColor" fontSize="12" textAnchor="middle">
            Longitude · schematic from source coordinates
          </text>
        </svg>
        <figcaption className="mt-2 text-xs text-muted-foreground">
          Coordinate schematic from the book; no basemap or inferred locations are used.
        </figcaption>
      </figure>
      <ol className="mt-3 space-y-3 text-sm">
        {map.geojson.features.map((feature) => (
          <li key={feature.id} className="border-l border-border pl-3">
            <h5 className="font-medium">{feature.properties.label}</h5>
            {feature.properties.description ? (
              <p className="mt-1 text-muted-foreground">{feature.properties.description}</p>
            ) : null}
            <p className="mt-1 text-xs text-muted-foreground">
              {feature.properties.attribution === "book" ? "From the book" : "AI inference"}
            </p>
            <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
              {feature.properties.evidence.map((item) => (
                <li key={`${item.page_number}-${item.quote}`}>
                  “{item.quote}” —{" "}
                  <Link href={pageHref(item.page_number)} className="underline underline-offset-2">
                    Page {item.page_number}
                  </Link>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}

function FeatureGeometry({
  feature,
  color,
  project,
}: {
  feature: Feature;
  color: string;
  project: (position: Position) => Position;
}) {
  const geometry = feature.geometry;
  if (geometry.type === "Point") {
    const [x, y] = project(geometry.coordinates);
    return (
      <circle cx={x} cy={y} r="7" fill={color}>
        <title>{feature.properties.label}</title>
      </circle>
    );
  }
  if (geometry.type === "MultiPoint") {
    return geometry.coordinates.map((coordinate, index) => {
      const [x, y] = project(coordinate);
      return (
        <circle key={`${feature.id}-${index}`} cx={x} cy={y} r="6" fill={color}>
          <title>{feature.properties.label}</title>
        </circle>
      );
    });
  }
  if (geometry.type === "LineString") {
    return (
      <path d={linePath(geometry.coordinates, project)} fill="none" stroke={color} strokeWidth="3">
        <title>{feature.properties.label}</title>
      </path>
    );
  }
  if (geometry.type === "MultiLineString") {
    return geometry.coordinates.map((line, index) => (
      <path
        key={`${feature.id}-${index}`}
        d={linePath(line, project)}
        fill="none"
        stroke={color}
        strokeWidth="3"
      >
        <title>{feature.properties.label}</title>
      </path>
    ));
  }
  const rings = geometry.type === "Polygon" ? geometry.coordinates : geometry.coordinates.flat();
  return (
    <path
      d={rings.map((ring) => linePath(ring, project, true)).join(" ")}
      fill={color}
      fillOpacity="0.2"
      fillRule="evenodd"
      stroke={color}
      strokeWidth="2"
    >
      <title>{feature.properties.label}</title>
    </path>
  );
}

function geometryPositions(geometry: Geometry): Position[] {
  switch (geometry.type) {
    case "Point":
      return [geometry.coordinates];
    case "MultiPoint":
    case "LineString":
      return geometry.coordinates;
    case "MultiLineString":
    case "Polygon":
      return geometry.coordinates.flat();
    case "MultiPolygon":
      return geometry.coordinates.flat(2);
  }
  return [];
}

function linePath(
  coordinates: readonly Position[],
  project: (position: Position) => Position,
  close = false,
): string {
  const projected = coordinates.map(project);
  const [first, ...rest] = projected;
  if (!first) return "";
  const commands = [
    `M ${formatCoordinate(first[0])} ${formatCoordinate(first[1])}`,
    ...rest.map(([x, y]) => `L ${formatCoordinate(x)} ${formatCoordinate(y)}`),
  ];
  if (close) commands.push("Z");
  return commands.join(" ");
}

function formatCoordinate(value: number): string {
  return Number(value.toFixed(2)).toString();
}
