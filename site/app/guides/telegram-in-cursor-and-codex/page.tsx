import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/telegram-in-cursor-and-codex/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Run <code>dotiphant connect cursor</code> or <code>dotiphant connect codex</code>. It adds Dotiphant&apos;s
            read-only MCP server to the app&apos;s config, so the agent can search and read your Telegram archive while
            you code.
          </p>
          <p>Both run locally: no tunnel, no account, nothing listening on your network.</p>
        </>
      }
    >
      <h2>Why your chats belong next to your code</h2>
      <p>
        A lot of product decisions never make it into a ticket. The API change was agreed in a group chat, the client
        sent the edge case in a private message, and the deadline moved in a thread from Tuesday. When your coding
        agent can read those chats, you can ask it to check the code against what was actually decided.
      </p>
      <p>
        Dotiphant keeps a copy of your Telegram chats in one SQLite file on your computer. Cursor and Codex start its
        MCP server as a child process over stdio and call its tools: list chats, search messages, read a chat, recent
        activity, chat details and refresh. All of them only read.
      </p>

      <h2>Connect Cursor or Codex</h2>
      <ol className="steps">
        <li>
          Install Dotiphant and finish the setup in your browser: your own free API id from my.telegram.org, a QR code
          login, what to archive, and the first sync.
          <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
        </li>
        <li>
          Connect the app. <code>dotiphant connect</code> without a name lists the apps it can connect and whether it
          found them.
          <Cmd lines={["dotiphant connect", "dotiphant connect cursor", "dotiphant connect codex"]} />
        </li>
        <li>Quit the app completely and start it again, so it loads the new server.</li>
        <li>Ask the agent a question that needs your chats.</li>
      </ol>
      <p>
        For Cursor, Dotiphant writes a <code>dotiphant</code> entry to <code>~/.cursor/mcp.json</code>. For Codex, it
        writes one to <code>~/.codex/config.toml</code>. It keeps a backup of the old file. Add <code>--dry-run</code>{" "}
        to see the change first, or <code>--print</code> to get the snippet and add it yourself. To connect every app
        it finds at once, use <code>dotiphant connect all</code>.
      </p>

      <h2>Things to ask while you code</h2>
      <ul className="ticks">
        <li>&quot;What did we agree on in the Acme group about the new API rate limits?&quot;</li>
        <li>&quot;Find the webhook URL Tomas sent me last month.&quot;</li>
        <li>&quot;Check this error message against what the client reported in our chat.&quot;</li>
        <li>&quot;Summarize what happened in the dev group since yesterday.&quot;</li>
      </ul>
      <p>
        The agent will often search first, then read the part of the chat around the best match. If it seems to miss
        recent messages, ask it to refresh, or run <code>dotiphant sync</code>.
      </p>

      <h2>No MCP? Pipe Markdown instead</h2>
      <p>
        Any tool that reads text can use your chats. <code>dotiphant export</code> prints a chat as Markdown, and{" "}
        <code>--out</code> saves it to a file you can open in the editor or add to the agent&apos;s context:
      </p>
      <Cmd
        lines={[
          'dotiphant export "Acme" --since 7d --out acme.md',
          'dotiphant search "rate limit" --chat "Acme" --json',
        ]}
      />
      <p>
        Be careful where that file ends up: a transcript in your project folder can be committed by accident. Details in{" "}
        <Link href="/guides/export-telegram-chat-to-markdown/">Export a Telegram chat to Markdown</Link>.
      </p>

      <h2>Limits</h2>
      <ul className="ticks">
        <li>
          The provider behind the app (Cursor, OpenAI) receives the messages its agent reads to answer you: text, sender
          names, chat names and dates. Only connect apps whose provider you trust with your chats.
        </li>
        <li>
          Coding agents often have tools that run commands or edit files. Messages are marked as untrusted content and
          the AI is told not to follow instructions inside them, but no tool can rule out prompt injection. Review what
          an agent wants to run.
        </li>
        <li>
          Dotiphant&apos;s tools cannot send, edit or delete anything in Telegram, so a tricked agent still cannot act
          in Telegram through Dotiphant.
        </li>
        <li>
          If the app does not see Dotiphant, quit it completely, start it again and run <code>dotiphant doctor</code>.
        </li>
      </ul>
      <p>
        More about the server and its tools: <Link href="/guides/telegram-mcp-server/">A local Telegram MCP server</Link>
        .
      </p>
    </GuidePage>
  );
}
