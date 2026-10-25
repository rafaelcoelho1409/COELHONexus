type Redirect = { source: string; destination: string; permanent: boolean };

/**
 * Pages that moved from the FastHTML UI to this app, as `from → to` path prefixes. The query string
 * (`?slug=…&run=…&chapter=…`) is carried over by Next, and the new pages read the same names.
 */
const MOVED_PAGES = [{ from: "/docs-distiller", to: "/dd" }] as const;

/**
 * Old URLs → new URLs, so bookmarks, the FastHTML UI's own links and shared study links keep working.
 *
 * `permanent: false` (307) on purpose: browsers cache a 308 for good, which would make a rollback to the
 * FastHTML page impossible for anyone who already followed the link. Flip it once the new pages have been
 * running live for a while.
 */
export function buildRedirects(): Redirect[] {
  return MOVED_PAGES.flatMap(({ from, to }) => [
    { source: from, destination: to, permanent: false },
    { source: `${from}/:path*`, destination: `${to}/:path*`, permanent: false },
  ]);
}
