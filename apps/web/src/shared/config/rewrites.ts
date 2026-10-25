import type { Env } from "./env";

type Rewrite = { source: string; destination: string };
type Rewrites = { afterFiles: Rewrite[]; fallback: Rewrite[] };

/**
 * The routing rules `next.config.ts` hands to Next — pure, so they are testable.
 *
 * - `afterFiles`: after this app's own routes, before dynamic ones. The API proxy lives here.
 * - `fallback`: only when nothing above matched. While the FastHTML UI is being migrated this is the
 *   strangler seam: every path the Next app does not serve yet falls through to the old UI, so pages move
 *   over one route at a time and there is never a big-bang cut-over.
 */
export function buildRewrites({ SERVER_URL, FASTHTML_URL, EXPOSE_API_DOCS }: Env): Rewrites {
  const afterFiles: Rewrite[] = [
    { source: "/api/health", destination: `${SERVER_URL}/health` },
    { source: "/api/:path*", destination: `${SERVER_URL}/api/:path*` },
  ];
  if (EXPOSE_API_DOCS) {
    afterFiles.push(
      { source: "/docs", destination: `${SERVER_URL}/docs` },
      { source: "/docs/:path*", destination: `${SERVER_URL}/docs/:path*` },
      { source: "/openapi.json", destination: `${SERVER_URL}/openapi.json` },
    );
  }
  const fallback: Rewrite[] = FASTHTML_URL
    ? [{ source: "/:path*", destination: `${FASTHTML_URL}/:path*` }]
    : [];
  return { afterFiles, fallback };
}
