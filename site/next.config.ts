import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
  // Set NEXT_PUBLIC_BASE_PATH=/dotiphant when hosting under a sub-path (GitHub Pages project site).
  basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
};

export default nextConfig;
