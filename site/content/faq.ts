// The home page FAQ. The Faq component renders it and the FAQPage JSON-LD is built from it,
// so the two never drift apart. Answers are paragraphs with two inline marks:
// `code` and [link text](https://url).

export type FaqItem = { q: string; a: string[] };

export const FAQ: FaqItem[] = [
  {
    q: "Why log in as me instead of using a bot?",
    a: [
      "A bot is a separate Telegram identity. It only sees messages sent after it was added to a group, never the history before that, and it can't read your private chats unless people message the bot directly.",
      "Your own account already sees everything you see: every one-on-one chat, every group, from the first message. Dotiphant logs in the way Telegram Desktop does and reads that, so your assistant gets the full context from day one.",
    ],
  },
  {
    q: "Can my account get banned?",
    a: [
      "Dotiphant logs in the same way Telegram Desktop does, only reads, and slows down whenever Telegram asks. Read-only, paced use of your own account is what the API is meant for. No third-party client can promise zero risk, though: brand new accounts and accounts with spam reports are watched more closely. Use your own API id and hash, and don't run several archivers on one account at once.",
    ],
  },
  {
    q: "Are secret chats included?",
    a: [
      "No. Secret chats are end-to-end encrypted between two devices and never reach Telegram's servers in readable form, so no other app can read them.",
    ],
  },
  {
    q: "Does it work with ChatGPT?",
    a: [
      "Yes, through a tunnel you run. ChatGPT and claude.ai on the web and phone only talk to servers on the internet. Run `dotiphant mcp --http` behind ngrok, Tailscale Funnel or Cloudflare Tunnel, add the address as a connector, and approve it in your terminal or with a one-time pairing code. Custom connectors in ChatGPT need a paid plan. Remote access is the riskiest path, since it exposes your archive through a tunnel you run; limit what remote apps can see with `dotiphant ai`. Claude Desktop, Claude Code, Codex and Cursor work locally with `dotiphant connect` and need no tunnel. Step by step: [Connect Telegram to ChatGPT](/guides/connect-telegram-to-chatgpt/).",
    ],
  },
  {
    q: "What does the AI provider see?",
    a: [
      "The messages your assistant reads to answer you: text, sender names, chat names and dates. They go to that app's provider under its privacy terms, like anything you paste into it. Dotiphant can't control what happens after that. It never sends your archive anywhere on its own.",
    ],
  },
  {
    q: "My chats include other people's messages. Can I keep some away from AI?",
    a: [
      "Yes. You decide which chats your AI may read. Hide any chat from AI apps with `dotiphant ai hide CHAT`, or the toggle in the viewer, or switch to `dotiphant ai mode selected` to share only the chats you choose.",
      "The people in your chats did not choose Dotiphant, so keep the archive for your own use. If you use it for work, data protection law (such as the GDPR in the EU) likely applies; ask your data protection officer first. More in the [security guide](/docs/security/#ai-access).",
    ],
  },
  {
    q: "Do I need to use the terminal?",
    a: [
      "Yes. Dotiphant is a command-line tool. Setup itself runs in your browser, and an AI assistant that can run commands can [install it with you](/docs/install/).",
    ],
  },
  {
    q: "What about WhatsApp, Signal or Slack?",
    a: [
      "Dotiphant syncs Telegram. For WhatsApp, export a chat in the WhatsApp app (Export chat) and run `dotiphant import` on the file: the chat joins the same archive, search and AI tools. Nothing connects to WhatsApp. A live WhatsApp link is not supported: WhatsApp has no official API for personal accounts, and an unofficial one may put your account at risk. Signal and Slack are not supported.",
    ],
  },
  {
    q: "What does it cost?",
    a: [
      "Dotiphant is free and open source under the MIT license, including remote access through your own tunnel. Your Telegram API id is free too.",
    ],
  },
  {
    q: "Is Dotiphant made by Telegram?",
    a: [
      "No. Dotiphant is an independent, unofficial project, not made, endorsed or supported by Telegram. It uses the public Telegram API with API credentials you create yourself, so you are responsible for using it within the [Telegram API Terms of Service](https://core.telegram.org/api/terms). Dotiphant does not train AI models. Using Telegram data with AI apps is subject to the [Telegram API Terms of Service](https://core.telegram.org/api/terms) and the [Terms for Content Licensing and AI Scraping](https://telegram.org/tos/content-licensing). Check them before you connect an AI app; you are responsible for how you use your archive.",
    ],
  },
];

export type Segment = { text: string; code?: boolean; href?: string };

// Splits one paragraph into plain text, code and link segments.
export function segments(paragraph: string): Segment[] {
  const out: Segment[] = [];
  const re = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  for (const m of paragraph.matchAll(re)) {
    if (m.index > last) out.push({ text: paragraph.slice(last, m.index) });
    if (m[1] !== undefined) out.push({ text: m[1], code: true });
    else out.push({ text: m[2], href: m[3] });
    last = m.index + m[0].length;
  }
  if (last < paragraph.length) out.push({ text: paragraph.slice(last) });
  return out;
}

// The answer as plain text, for structured data.
export const plainAnswer = (item: FaqItem) =>
  item.a.map((p) => segments(p).map((s) => s.text).join("")).join("\n\n");
