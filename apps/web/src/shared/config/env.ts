import { z } from "zod";

/**
 * The web app's whole environment, validated once at startup.
 *
 * `SERVER_URL` and `FASTHTML_URL` are read when `next.config.ts` compiles the rewrites, so for a
 * production image they are build-time values (Dockerfile ARGs), not runtime ones.
 */
const schema = z.object({
  /** The Nexus API (FastAPI). In-cluster: http://coelhonexus-server:8000. */
  SERVER_URL: z.url().default("http://localhost:23030"),
  /** The legacy FastHTML UI. When set, every path this app does not serve falls back to it. */
  FASTHTML_URL: z.url().optional(),
  /** Proxy the API's /docs and /openapi.json through this app. Off by default: the schema is not public. */
  EXPOSE_API_DOCS: z.stringbool().default(false),
});

const withoutTrailingSlash = (url: string) => url.replace(/\/+$/, "");

export type Env = {
  SERVER_URL: string;
  FASTHTML_URL: string | undefined;
  EXPOSE_API_DOCS: boolean;
};

/** Parses an environment source; empty strings count as unset. Throws one readable error listing every problem. */
export function parseEnv(source: Record<string, string | undefined>): Env {
  const present = Object.fromEntries(
    Object.entries(source).filter(([, value]) => value !== undefined && value !== ""),
  );
  const result = schema.safeParse(present);
  if (!result.success) {
    const problems = result.error.issues
      .map((issue) => `${issue.path.join(".")}: ${issue.message}`)
      .join("; ");
    throw new Error(`Invalid environment — ${problems}`);
  }
  const { SERVER_URL, FASTHTML_URL, EXPOSE_API_DOCS } = result.data;
  return {
    SERVER_URL: withoutTrailingSlash(SERVER_URL),
    FASTHTML_URL: FASTHTML_URL === undefined ? undefined : withoutTrailingSlash(FASTHTML_URL),
    EXPOSE_API_DOCS,
  };
}

export const env: Env = parseEnv(process.env);
