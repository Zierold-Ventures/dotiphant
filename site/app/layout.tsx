import type { Metadata, Viewport } from "next";
import { Host_Grotesk, Fragment_Mono } from "next/font/google";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";
import { SITE_URL } from "@/lib";
import { DESCRIPTION } from "@/content/seo";

// One grotesk at regular weight for everything, a quiet mono for code and labels.
const sans = Host_Grotesk({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
});

const mono = Fragment_Mono({
  subsets: ["latin"],
  weight: "400",
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  // Absolute site URL, used for canonical and Open Graph links.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || `${SITE_URL}/`),
  title: { default: "Dotiphant", template: "%s | Dotiphant" },
  description: DESCRIPTION,
  applicationName: "Dotiphant",
  authors: [{ name: "Zierold Ventures SLU" }],
  // Pages set canonical, Open Graph and Twitter themselves; these are the fallbacks.
  openGraph: { type: "website", siteName: "Dotiphant", locale: "en_US", images: ["/img/og.png"] },
  twitter: { card: "summary_large_image" },
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: "#0c0d0e",
};

// Runs before first paint: applies a saved theme choice so there is no flash.
// No saved choice means "system", which the CSS handles with prefers-color-scheme.
const themeScript = `try{var t=localStorage.getItem("dotiphant-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        {children}
        <Analytics />
      </body>
    </html>
  );
}
