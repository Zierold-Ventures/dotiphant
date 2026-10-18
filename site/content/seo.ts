import type { Metadata } from "next";
import { GITHUB, RELEASES, SITE_URL } from "@/lib";
import type { PageInfo } from "./pages";

export const HOME_TITLE = "Dotiphant: your Telegram, searchable by your AI";
export const DESCRIPTION =
  "Dotiphant keeps a searchable archive of your Telegram chats on your own computer and lets your AI assistant read it. Read-only, local, open source (MIT).";

// Absolute URL for a site path ("/guides/x/").
export const abs = (path: string) => `${SITE_URL}${path}`;

const OG_IMAGE = {
  url: "/img/og.png",
  width: 1200,
  height: 630,
  alt: "Dotiphant: now your AI assistant can search your Telegram. A glass lens over blurred chat messages shows the word launch in focus.",
};

// Title, description, canonical, Open Graph and Twitter for one page. Next replaces the layout's
// openGraph and twitter objects as a whole, so every page sets all of it.
export function pageMetadata({
  path,
  title,
  description,
  type = "article",
  absoluteTitle = false,
}: {
  path: string;
  title: string;
  description: string;
  type?: "website" | "article";
  absoluteTitle?: boolean;
}): Metadata {
  const full = absoluteTitle ? title : `${title} | Dotiphant`;
  return {
    title: absoluteTitle ? { absolute: title } : title,
    description,
    alternates: { canonical: path },
    openGraph: { type, url: path, siteName: "Dotiphant", locale: "en_US", title: full, description, images: [OG_IMAGE] },
    twitter: { card: "summary_large_image", title: full, description, images: [OG_IMAGE.url] },
  };
}

export const metadataFor = (p: PageInfo) => pageMetadata({ path: p.path, title: p.title, description: p.description });

// JSON-LD (schema.org). Node ids let the graph refer to the organization and the app by reference.
const ORG_ID = `${SITE_URL}/#organization`;
const APP_ID = `${SITE_URL}/#software`;
const SITE_ID = `${SITE_URL}/#website`;

export const organization = {
  "@type": "Organization",
  "@id": ORG_ID,
  name: "Zierold Ventures SLU",
  url: `${SITE_URL}/`,
  logo: { "@type": "ImageObject", url: abs("/apple-icon.png"), width: 180, height: 180 },
  sameAs: ["https://github.com/Zierold-Ventures"],
};

export const website = {
  "@type": "WebSite",
  "@id": SITE_ID,
  name: "Dotiphant",
  url: `${SITE_URL}/`,
  inLanguage: "en",
  publisher: { "@id": ORG_ID },
};

export const softwareApplication = {
  "@type": "SoftwareApplication",
  "@id": APP_ID,
  name: "Dotiphant",
  description:
    "An open-source, unofficial app that keeps a local, read-only archive of your Telegram chats, with WhatsApp export import, and serves it to AI assistants over MCP. Not affiliated with Telegram.",
  url: `${SITE_URL}/`,
  applicationCategory: "UtilitiesApplication",
  applicationSubCategory: "Chat archive and MCP server",
  operatingSystem: "macOS, Linux",
  license: "https://opensource.org/licenses/MIT",
  isAccessibleForFree: true,
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
  downloadUrl: RELEASES,
  installUrl: abs("/docs/install/"),
  softwareHelp: { "@type": "CreativeWork", url: abs("/docs/install/") },
  sameAs: [GITHUB],
  image: abs("/img/og.png"),
  publisher: { "@id": ORG_ID },
};

export function graph(...nodes: object[]) {
  return { "@context": "https://schema.org", "@graph": nodes };
}

export function breadcrumbs(items: { name: string; path: string }[]) {
  return {
    "@type": "BreadcrumbList",
    itemListElement: items.map((it, i) => ({ "@type": "ListItem", position: i + 1, name: it.name, item: abs(it.path) })),
  };
}

export function article(p: PageInfo) {
  return {
    "@type": "TechArticle",
    "@id": `${abs(p.path)}#article`,
    headline: p.title,
    description: p.description,
    url: abs(p.path),
    mainEntityOfPage: abs(p.path),
    datePublished: p.updated,
    dateModified: p.updated,
    inLanguage: "en",
    image: abs("/img/og.png"),
    author: { "@id": ORG_ID },
    publisher: { "@id": ORG_ID },
    about: { "@id": APP_ID },
    isPartOf: { "@id": SITE_ID },
  };
}

export function faqPage(items: { q: string; text: string }[]) {
  return {
    "@type": "FAQPage",
    "@id": `${SITE_URL}/#faq`,
    mainEntity: items.map((it) => ({
      "@type": "Question",
      name: it.q,
      acceptedAnswer: { "@type": "Answer", text: it.text },
    })),
  };
}
