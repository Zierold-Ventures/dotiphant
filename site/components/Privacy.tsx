import Link from "next/link";
import type { ReactNode } from "react";
import { AiAccessNote } from "./Trust";

type Entry = { term: string; detail: ReactNode };

const columns: { title: string; warn?: boolean; entries: Entry[] }[] = [
  {
    title: "Stays on your computer",
    entries: [
      { term: "Your messages", detail: "One SQLite file in a private folder that only your user account can read." },
      {
        term: "Your Telegram login",
        detail:
          "The session, API id and hash sit in the system keychain. You can end the session any time in Telegram under Settings > Devices.",
      },
      {
        term: "The viewer",
        detail: "Listens on 127.0.0.1 only, and every request needs a key that is unique to your install.",
      },
      {
        term: "No training",
        detail: "Dotiphant never uses your messages to train AI models, and its makers never receive them.",
      },
    ],
  },
  {
    title: "Toward Telegram: read only",
    entries: [
      {
        term: "Never writes",
        detail:
          "Dotiphant never sends, edits, deletes, forwards or reacts, never joins or leaves chats and never marks anything as read.",
      },
      {
        term: "Paced",
        detail: "Requests are paced, and Dotiphant waits whenever Telegram asks it to slow down.",
      },
    ],
  },
  {
    title: "What your AI provider sees",
    warn: true,
    entries: [
      {
        term: "Only what it reads",
        detail:
          "When you ask a question, the messages your assistant reads go to its provider (Anthropic, OpenAI, Cursor), the same as anything you paste in. Connect only apps you trust with your chats.",
      },
      {
        term: "Your choice of chats",
        detail: <AiAccessNote />,
      },
      {
        term: "Remote access",
        detail: (
          <>
            ChatGPT, claude.ai and other remote apps need a tunnel you run, which makes your archive reachable from the
            internet. It is the riskiest path: every app needs your approval, and <code>dotiphant ai</code> limits what
            they can see. Claude Desktop, Claude Code, Codex and Cursor need no tunnel.{" "}
            <Link href="/docs/remote-access/">Remote access</Link>
          </>
        ),
      },
      {
        term: "Untrusted by default",
        detail:
          "Every message is fenced as untrusted content, and the AI is told not to follow instructions inside it. That lowers the risk of prompt injection; no tool can rule it out. Dotiphant's tools can't send anything, so a tricked AI can't act in Telegram through Dotiphant.",
      },
    ],
  },
];

export function Privacy() {
  return (
    <section id="privacy" className="sec privacy" aria-labelledby="privacy-h">
      <div className="wrap">
        <header className="head">
          <h2 id="privacy-h">
            Read-only toward Telegram.
            <br />
            No cloud in between.
          </h2>
          <p className="head__sub">
            Your chats stay on this computer. No cloud, no telemetry. Here is exactly what lives where.
          </p>
        </header>

        <div className="ledger">
          {columns.map((col, i) => (
            <div key={col.title} className={`ledger__col glass${col.warn ? " ledger__col--warn" : ""}`}>
              <p className="ledger__n">{String(i + 1).padStart(2, "0")}</p>
              <h3>{col.title}</h3>
              <dl>
                {col.entries.map((e) => (
                  <div key={e.term}>
                    <dt>{e.term}</dt>
                    <dd>{e.detail}</dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}
        </div>
        <p className="fine privacy__foot">
          The stored session gives full access to your account, like a logged-in Telegram Desktop. Protect your
          computer like you protect your phone: screen lock and disk encryption. To wipe everything, stop the refresh,
          log out with <code>--forget-credentials</code> and delete the data folder. Steps are in the{" "}
          <a href="https://github.com/Zierold-Ventures/dotiphant/blob/main/docs/security.md">security guide</a>. Your
          archive also holds other people&apos;s messages. If you use Dotiphant for work, data protection law (such as
          the GDPR) likely applies, so ask your data protection officer first.
        </p>
      </div>
    </section>
  );
}
