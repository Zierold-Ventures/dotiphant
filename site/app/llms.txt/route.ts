import { SECTIONS, PAGES } from "@/content/pages";
import { abs } from "@/content/seo";
import { GITHUB, INSTALL, RELEASES } from "@/lib";

export const dynamic = "force-static";

// llms.txt (https://llmstxt.org): a short Markdown map of the site for AI tools and answer engines.
// Built from the same page list as the sitemap. scripts/llms-full.mjs appends every page's text into llms-full.txt.
function llmsText(): string {
  const sections = SECTIONS.map(
    (s) =>
      `## ${s.name === "Docs" ? "Docs" : `Guides: ${s.name}`}\n\n` +
      PAGES.filter((p) => p.section === s.name)
        .map((p) => `- [${p.title}](${abs(p.path)}): ${p.summary.replace(/`/g, "")}`)
        .join("\n"),
  ).join("\n\n");

  return `# Dotiphant

> Dotiphant is a free, open-source (MIT) app that keeps a local, read-only archive of your Telegram chats on your own computer and lets your AI assistant search and read it over MCP. It also imports WhatsApp chat exports. It is an independent, unofficial project, not affiliated with Telegram.

Key facts:

- Logs in as your own Telegram account (QR code or phone, like Telegram Desktop) with an API id and hash you create yourself at my.telegram.org. Sees the full history of every chat you are in. Secret chats are not included; channels are optional.
- Read-only toward Telegram: never sends, edits, deletes, forwards, reacts or marks anything as read.
- The archive is one SQLite file in a private folder on your computer. The Telegram session and API keys are in the system keychain. No cloud service, no telemetry.
- Includes a local web viewer with full-text search, media and "copy as Markdown", a CLI (search, export to Markdown, import), and an MCP server.
- Claude Desktop, Claude Code, Codex and Cursor connect locally with \`dotiphant connect\`. ChatGPT, claude.ai and other apps that support remote MCP servers connect through remote access: \`dotiphant mcp --http\` behind an HTTPS tunnel you run, with OAuth and approval in the terminal or with a one-time pairing code.
- When an AI app reads messages to answer you, those messages go to that app's provider under its terms.
- Platforms: macOS and Linux; Windows experimental.
- Releases ship on GitHub (${RELEASES}), not on PyPI.

Install on macOS or Linux, then run the setup in your browser (about five minutes):

\`\`\`sh
${INSTALL}
dotiphant open
\`\`\`

${sections}

## Optional

- [Home page](${abs("/")}): what Dotiphant does, how it works, privacy, and the FAQ.
- [Source code on GitHub](${GITHUB}): code, releases and issues.
- [Telegram API Terms of Service](https://core.telegram.org/api/terms): the terms every Telegram API client must follow.
- [Telegram Terms for Content Licensing and AI Scraping](https://telegram.org/tos/content-licensing): Telegram's terms on content licensing and AI scraping.
- [Full text of all guides and docs](${abs("/llms-full.txt")}): every page above in one file.
`;
}

export function GET() {
  return new Response(llmsText(), { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
