import type { MetadataRoute } from "next";
import { INDEXES, PAGES } from "@/content/pages";
import { abs } from "@/content/seo";

export const dynamic = "force-static";

// Dates of the last real content change. Content pages carry theirs in content/pages.ts.
const HOME_UPDATED = "2026-10-07";
const LEGAL_UPDATED = "2026-10-01";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: abs("/"), lastModified: HOME_UPDATED, priority: 1 },
    { url: abs(INDEXES.guides.path), lastModified: INDEXES.guides.updated, priority: 0.8 },
    { url: abs(INDEXES.docs.path), lastModified: INDEXES.docs.updated, priority: 0.8 },
    ...PAGES.map((p) => ({ url: abs(p.path), lastModified: p.updated, priority: 0.7 })),
    { url: abs("/privacy/"), lastModified: LEGAL_UPDATED, priority: 0.2 },
    { url: abs("/imprint/"), lastModified: LEGAL_UPDATED, priority: 0.2 },
  ];
}
