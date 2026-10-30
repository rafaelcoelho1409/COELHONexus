import type { Env } from "./env";

type Rewrite = { source: string; destination: string };

/**
 * The routing rules `next.config.ts` hands to Next — pure, so they are testable. They run after this app's
 * own routes, before dynamic ones: the API proxy, and (only when asked) the API's docs.
 */
export function buildRewrites({ SERVER_URL, EXPOSE_API_DOCS }: Env): Rewrite[] {
  const rules: Rewrite[] = [
    { source: "/api/health", destination: `${SERVER_URL}/health` },
    { source: "/api/:path*", destination: `${SERVER_URL}/api/:path*` },
  ];
  if (EXPOSE_API_DOCS) {
    rules.push(
      { source: "/docs", destination: `${SERVER_URL}/docs` },
      { source: "/docs/:path*", destination: `${SERVER_URL}/docs/:path*` },
      { source: "/openapi.json", destination: `${SERVER_URL}/openapi.json` },
    );
  }
  return rules;
}
