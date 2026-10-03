const API = process.env.MIA_API_ORIGIN || "http://127.0.0.1:8080";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/v1/:path*", destination: `${API}/v1/:path*` },
    ];
  },
};

export default nextConfig;
