import { z } from "zod";

/**
 * The web app's whole environment, validated once at startup.
 *
 * `SERVER_URL` is read when `next.config.ts` compiles the rewrites, so for a production image it is a
 * build-time value (a Dockerfile ARG), not a runtime one.
 */
const schema = z.object({
  /** The Nexus API (FastAPI). In-cluster: http://coelhonexus-server:8000. */
  SERVER_URL: z.url().default("http://localhost:23030"),
  /** Proxy the API's /docs and /openapi.json through this app. Off by default: the schema is not public. */
  EXPOSE_API_DOCS: z.stringbool().default(false),
});

const withoutTrailingSlash = (url: string) => url.replace(/\/+$/, "");

export type Env = {
  SERVER_URL: string;
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
  const { SERVER_URL, EXPOSE_API_DOCS } = result.data;
  return { SERVER_URL: withoutTrailingSlash(SERVER_URL), EXPOSE_API_DOCS };
}

export const env: Env = parseEnv(process.env);
