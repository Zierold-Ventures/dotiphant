import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/telegram-mcp-server/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Dotiphant is an open-source MCP server for your own Telegram chats. It keeps a read-only archive of your
            chats in one file on your computer and gives your AI assistant tools to search and read it. It never sends,
            edits or deletes anything in Telegram.
          </p>
          <p>
            Claude Desktop, Claude Code, Codex and Cursor start it on your computer. ChatGPT and claude.ai reach it
            through a tunnel you run.
          </p>
        </>
      }
    >
      <h2>Why a local archive, and not a bot</h2>
      <p>
        MCP (Model Context Protocol) is how AI apps call outside tools. A Telegram MCP server could talk to Telegram
        live on every question, or it could use a bot. Both have limits. A bot is a separate Telegram identity: it
        only sees messages sent after it joined a group, never the history, and it cannot read your private chats.
      </p>
      <p>
        Dotiphant logs in as your own account, the way Telegram Desktop does, and copies your chats into a SQLite file
        on your computer. The MCP server reads that file. So your assistant sees the full history of every chat you
        are in, searches run fast, and answering a question sends no request to Telegram at all.
      </p>

      <h2>What your AI app can do with it</h2>
      <p>The server gives your AI app these tools:</p>
      <ul className="ticks">
        <li>
          <code>list_chats</code>: the chats in your archive, most recently active first, with message counts.
        </li>
        <li>
          <code>search_messages</code>: full-text search across all chats, or one chat, a sender or a time range.
        </li>
        <li>
          <code>get_messages</code>: read a chat, or the part of it around a message.
        </li>
        <li>
          <code>recent_activity</code>: what happened in your chats recently.
        </li>
        <li>
          <code>chat_info</code>: the title, username, forum topics and participants of one chat.
        </li>
        <li>
          <code>refresh</code>: fetch new messages from Telegram. It still only reads, and runs at most once a minute.
        </li>
      </ul>
      <p>
        All tools are read-only. There is no tool to send, edit, delete, forward or react, so even a confused or
        tricked assistant cannot act in Telegram through Dotiphant.
      </p>

      <h2>Set it up</h2>
      <ol className="steps">
        <li>
          Install Dotiphant and run the setup. It opens in your browser and takes about five minutes: create your own
          free API id at my.telegram.org, scan a QR code with the Telegram app, choose what to archive, and run the
          first sync.
          <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
        </li>
        <li>
          Connect your AI app. Without a name, <code>dotiphant connect</code> lists the apps it can connect and
          whether it found them.
          <Cmd lines={["dotiphant connect", "dotiphant connect claude-desktop  # or claude-code, codex, cursor, all"]} />
        </li>
        <li>
          Quit the AI app completely and start it again. On macOS that means Cmd+Q, not just closing the window.
        </li>
        <li>Ask a question, like "What did Clara and I agree on about the launch date?"</li>
      </ol>
      <p>
        If an app is not found, <code>dotiphant connect NAME --print</code> prints the configuration to add by hand.
        The server itself is <code>dotiphant mcp</code>. Your AI app starts it as a child process over stdio, so
        nothing listens on your network.
      </p>

      <h2>Apps that only reach the internet</h2>
      <p>
        ChatGPT and claude.ai on the web and phone only talk to MCP servers on the internet over HTTPS. For them, run <code>dotiphant mcp --http</code> behind an HTTPS tunnel you run, such as ngrok, Tailscale
        Funnel or Cloudflare Tunnel. Each app has to be approved by you, in your terminal or with a one-time pairing
        code. See <Link href="/guides/connect-telegram-to-chatgpt/">Connect Telegram to ChatGPT</Link> and the full{" "}
        <Link href="/docs/remote-access/">remote access docs</Link>.
      </p>

      <h2>Limits worth knowing</h2>
      <ul className="ticks">
        <li>
          The AI provider sees the messages its assistant reads to answer you: text, sender names, chat names and
          dates. That is how any connector works. Only connect apps whose provider you trust with your chats.
        </li>
        <li>
          Messages are handed to the AI marked as untrusted content, and the AI is told not to follow instructions
          inside them. This lowers the risk of prompt injection. No tool can rule it out.
        </li>
        <li>Secret chats are not included. They never reach Telegram&apos;s servers in readable form.</li>
        <li>Channels are off by default. You can turn them on in the setup.</li>
        <li>
          Dotiphant is an independent, unofficial project that uses the public Telegram API with your own API id. It
          is not made, endorsed or supported by Telegram.
        </li>
      </ul>
      <p>
        More about what is stored and who can reach it: <Link href="/docs/security/">Security and privacy</Link>.
      </p>
    </GuidePage>
  );
}
