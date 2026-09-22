import type { NextConfig } from "next";
const config: NextConfig = {
  devIndicators: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.CAREERAI_API_URL || "http://127.0.0.1:8000"}/api/:path*` }];
  },
};
export default config;
