type Redirect = { source: string; destination: string; permanent: boolean };

/**
 * Pages whose address changed, as `from → to` path prefixes (the old UI's URLs). The query string
 * (`?slug=…&run=…&chapter=…`) is carried over by Next, and the new pages read the same names.
 */
const MOVED_PAGES = [{ from: "/docs-distiller", to: "/dd" }] as const;

/**
 * Old URLs → new URLs, so bookmarks and shared study links keep working.
 *
 * `permanent: false` (307) on purpose: browsers cache a 308 for good, so a later change of address would not
 * reach anyone who already followed the link. Flip it once the new address is final.
 */
export function buildRedirects(): Redirect[] {
  return MOVED_PAGES.flatMap(({ from, to }) => [
    { source: from, destination: to, permanent: false },
    { source: `${from}/:path*`, destination: `${to}/:path*`, permanent: false },
  ]);
}
