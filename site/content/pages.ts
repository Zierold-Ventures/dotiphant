// Every content page on the site, in one list. The pages, the /guides and /docs indexes, the sitemap,
// llms.txt, breadcrumbs, related links and JSON-LD all read from here, so they cannot drift apart.

export type Section = "Integrations" | "Use cases" | "Docs";

export type PageInfo = {
  path: string; // with a trailing slash, as the static export serves it
  title: string; // the <title> before " | Dotiphant", and the h1
  short: string; // link text in lists and breadcrumbs
  description: string; // meta description, about 150 characters
  summary: string; // one line for indexes and llms.txt
  section: Section;
  updated: string; // ISO date of the last real content change
  related: string[]; // paths of related pages
};

export const PAGES: PageInfo[] = [
  {
    path: "/guides/telegram-mcp-server/",
    title: "A local Telegram MCP server for your AI assistant",
    short: "Telegram MCP server",
    description:
      "Dotiphant is an open-source MCP server for your own Telegram chats. It runs on your computer, reads a local archive and never writes to Telegram.",
    summary: "What the Dotiphant MCP server is, which tools it gives your AI app, and how to connect it.",
    section: "Integrations",
    updated: "2026-10-07",
    related: ["/guides/connect-telegram-to-claude/", "/guides/telegram-in-cursor-and-codex/", "/docs/security/"],
  },
  {
    path: "/guides/connect-telegram-to-claude/",
    title: "Connect Telegram to Claude",
    short: "Connect Telegram to Claude",
    description:
      "Let Claude Desktop, Claude Code or claude.ai search your Telegram chats. One command for the local apps, remote access with a pairing code for the web.",
    summary: "Claude Desktop and Claude Code with one command; claude.ai on the web and phone through remote access.",
    section: "Integrations",
    updated: "2026-10-07",
    related: ["/guides/telegram-mcp-server/", "/guides/connect-telegram-to-chatgpt/", "/docs/remote-access/"],
  },
  {
    path: "/guides/connect-telegram-to-chatgpt/",
    title: "Connect Telegram to ChatGPT",
    short: "Connect Telegram to ChatGPT",
    description:
      "ChatGPT can read your Telegram archive through a custom connector, an HTTPS tunnel you run and a one-time pairing code. Steps, trade-offs and limits.",
    summary: "Remote access for ChatGPT: a tunnel you run, OAuth, a pairing code, and the honest trade-offs.",
    section: "Integrations",
    updated: "2026-10-07",
    related: ["/docs/remote-access/", "/docs/security/", "/guides/connect-telegram-to-claude/"],
  },
  {
    path: "/guides/telegram-in-cursor-and-codex/",
    title: "Use your Telegram chats in Cursor and Codex",
    short: "Telegram in Cursor and Codex",
    description:
      "Give Cursor or Codex read-only access to your Telegram chats, so the context from your team chats is there while you code. One command each.",
    summary: "Connect Cursor or Codex with one command and ask about decisions made in your chats while you code.",
    section: "Integrations",
    updated: "2026-10-07",
    related: ["/guides/telegram-mcp-server/", "/guides/export-telegram-chat-to-markdown/", "/guides/search-telegram-history/"],
  },
  {
    path: "/guides/search-telegram-history/",
    title: "Search your whole Telegram history",
    short: "Search Telegram history",
    description:
      "Search every Telegram chat you are in, from the first message, on your own computer. Full-text search in a local viewer, the terminal, or through your AI.",
    summary: "Full-text search across all your chats in the viewer, the terminal or your AI assistant.",
    section: "Use cases",
    updated: "2026-10-07",
    related: ["/guides/back-up-telegram-chats-locally/", "/guides/export-telegram-chat-to-markdown/", "/guides/telegram-mcp-server/"],
  },
  {
    path: "/guides/export-telegram-chat-to-markdown/",
    title: "Export a Telegram chat to Markdown",
    short: "Export a chat to Markdown",
    description:
      "Turn a Telegram chat into a clean Markdown transcript for any time range, then save it or pipe it into an AI tool. From the viewer or one command.",
    summary: "Copy a chat or a time range as Markdown in the viewer, or print it with `dotiphant export`.",
    section: "Use cases",
    updated: "2026-10-07",
    related: ["/guides/search-telegram-history/", "/guides/telegram-in-cursor-and-codex/", "/guides/back-up-telegram-chats-locally/"],
  },
  {
    path: "/guides/back-up-telegram-chats-locally/",
    title: "Back up your Telegram chats locally",
    short: "Back up Telegram chats locally",
    description:
      "Keep a local, read-only copy of your Telegram chats in one SQLite file on your computer, refreshed in the background. No cloud. Open source.",
    summary: "A local SQLite archive of your chats, refreshed every 15 minutes, with no cloud in between.",
    section: "Use cases",
    updated: "2026-10-07",
    related: ["/docs/security/", "/guides/search-telegram-history/", "/docs/install/"],
  },
  {
    path: "/guides/ask-ai-about-whatsapp-chat-export/",
    title: "Ask AI about a WhatsApp chat export",
    short: "Ask AI about a WhatsApp export",
    description:
      "Import a WhatsApp \"Export chat\" file into a local archive and let your AI assistant search and summarize it. Nothing connects to WhatsApp.",
    summary: "Import WhatsApp's own chat export, then search it and ask your AI about it. Nothing connects to WhatsApp.",
    section: "Use cases",
    updated: "2026-10-07",
    related: ["/guides/export-telegram-chat-to-markdown/", "/guides/search-telegram-history/", "/guides/telegram-mcp-server/"],
  },
  {
    path: "/docs/install/",
    title: "Install Dotiphant",
    short: "Install",
    description:
      "Install Dotiphant on macOS or Linux with one command, set it up in your browser in about five minutes, and connect your AI app. All commands.",
    summary: "Install, the five setup steps, the command list, and how to update or remove Dotiphant.",
    section: "Docs",
    updated: "2026-10-07",
    related: ["/docs/security/", "/docs/remote-access/", "/guides/telegram-mcp-server/"],
  },
  {
    path: "/docs/remote-access/",
    title: "Remote access: ChatGPT, claude.ai and other remote apps",
    short: "Remote access",
    description:
      "Reach your local Dotiphant archive from ChatGPT, claude.ai and other apps that support remote MCP servers through an HTTPS tunnel you run, with OAuth and a one-time pairing code.",
    summary: "Tunnel setup (ngrok, Tailscale Funnel, Cloudflare Tunnel), approval with a pairing code, and managing access.",
    section: "Docs",
    updated: "2026-10-07",
    related: ["/guides/connect-telegram-to-chatgpt/", "/guides/connect-telegram-to-claude/", "/docs/security/"],
  },
  {
    path: "/docs/security/",
    title: "Security and privacy",
    short: "Security",
    description:
      "What Dotiphant stores and where, who can reach the archive, what an AI app receives, how remote approval works, and how to wipe everything.",
    summary: "What is stored where, who can reach it, prompt injection, remote approval limits, and how to wipe everything.",
    section: "Docs",
    updated: "2026-10-07",
    related: ["/docs/remote-access/", "/guides/back-up-telegram-chats-locally/", "/docs/install/"],
  },
];

export const SECTIONS: { name: Section; blurb: string }[] = [
  { name: "Integrations", blurb: "Connect your chats to the AI app you already use." },
  { name: "Use cases", blurb: "Search, export, back up and ask." },
  { name: "Docs", blurb: "Install, remote access and security, in full." },
];

export const INDEXES = {
  guides: {
    path: "/guides/",
    title: "Guides",
    description:
      "Short guides for Dotiphant: connect your Telegram chats to Claude, ChatGPT, Cursor or Codex, search and export them, back them up, and read WhatsApp exports.",
    updated: "2026-10-07",
  },
  docs: {
    path: "/docs/",
    title: "Docs",
    description: "Dotiphant documentation: install and setup, remote access for ChatGPT and claude.ai, and security and privacy.",
    updated: "2026-10-07",
  },
};

export function page(path: string): PageInfo {
  const p = PAGES.find((x) => x.path === path);
  if (!p) throw new Error(`Unknown page ${path}`);
  return p;
}

export const guides = () => PAGES.filter((p) => p.section !== "Docs");
export const docs = () => PAGES.filter((p) => p.section === "Docs");
