/**
 * The Nexus API as seen from the web app: the typed SDK (`getDdIngestionRunsActive`, `postDdPipelinePlannerSlug`, …), its
 * request/response types and the TanStack Query option factories (`getDdIngestionRunsActiveOptions`, …). All of it
 * is generated from `openapi/openapi.json` — never edit `generated/`; run `npm run api:generate`.
 *
 * Request paths are same-origin (`/api/...`); `next.config.ts` proxies them to the server.
 */
// Re-export the generated source modules directly so Next can resolve the SDK's named exports.

export * from "./generated/@tanstack/react-query.gen";
/** The shared fetch client — tests and server-side callers point it at an absolute `baseUrl`. */
export { client } from "./generated/client.gen";
export * from "./generated/sdk.gen";
export * from "./generated/types.gen";
