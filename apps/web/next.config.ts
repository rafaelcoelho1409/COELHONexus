import type { NextConfig } from "next";

import { env } from "./src/shared/config/env";
import { buildRewrites } from "./src/shared/config/rewrites";

const nextConfig: NextConfig = {
  output: "standalone",
  // Server-sent events pass through the rewrite proxy. With compression on, Next gzips a
  // `text/event-stream` that lacks `Cache-Control: no-transform` and delivers it in one lump when
  // the stream ends (measured: events arrived together after 6 s). Let the ingress compress.
  compress: false,
  turbopack: { root: import.meta.dirname },
  // Nexus pipelines can stream for minutes; Next's rewrite proxy defaults to 30s.
  experimental: { proxyTimeout: 30 * 60 * 1000 },
  async rewrites() {
    return buildRewrites(env);
  },
};

export default nextConfig;
