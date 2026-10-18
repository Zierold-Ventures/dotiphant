import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/search-telegram-history/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Dotiphant copies the history of every Telegram chat you are in into one file on your computer and gives you
            full-text search across all of it. Search in the local viewer with <code>dotiphant open</code>, in the
            terminal with <code>dotiphant search</code>, or ask your AI assistant in plain words.
          </p>
          <p>Searches run on your computer, against your local archive, not against Telegram.</p>
        </>
      }
    >
      <h2>Why Telegram&apos;s own search is not always enough</h2>
      <p>
        Telegram&apos;s search works, but it is built for finding a recent message in one place. It gets harder when
        you remember the words but not the chat, when you want only one person&apos;s messages from last spring, or
        when you want the result as text you can reuse. A local archive with a full-text index makes those searches
        fast and repeatable.
      </p>

      <h2>Get your history into the archive</h2>
      <ol className="steps">
        <li>
          Install Dotiphant and finish the setup in your browser. It takes about five minutes.
          <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
        </li>
        <li>
          The first sync loads the last 365 days of your chats with people and groups by default. For everything, run
          one full sync. Large accounts can take a while; stop with Ctrl-C and the next sync continues where it
          stopped.
          <Cmd lines={["dotiphant sync --all-history"]} />
        </li>
        <li>
          Keep it current. Background refresh fetches new messages every 15 minutes if you turn it on.
          <Cmd lines={["dotiphant schedule install"]} />
        </li>
      </ol>
      <p>
        Channels are off by default; turn them on with <code>dotiphant setup --reconfigure</code>. Secret chats are not
        included, because they never reach Telegram&apos;s servers in readable form.
      </p>

      <h2>Search in the viewer</h2>
      <p>
        <code>dotiphant open</code> starts the viewer at http://127.0.0.1:8765 and opens it in your browser. It shows
        your chat list, each chat with its media, and a search box across all chats. From a result you jump into the
        chat at that message. The viewer listens on your own computer only and needs a private key, so other programs
        and other user accounts cannot read it.
      </p>

      <h2>Search in the terminal</h2>
      <Cmd
        lines={[
          'dotiphant search "launch date"',
          'dotiphant search invoice --chat "Acme" --since 30d',
          "dotiphant search contract --sender @clara --since 2026-03-01 --until 2026-06-30",
          "dotiphant search wallet --limit 50 --json",
        ]}
      />
      <ul className="ticks">
        <li>
          <code>--chat</code> limits to one chat, by id, @username or title.
        </li>
        <li>
          <code>--sender</code> limits to a sender name or @username.
        </li>
        <li>
          <code>--since</code> and <code>--until</code> take a span like <code>24h</code> or <code>7d</code>, or a date
          like <code>2026-09-01</code>.
        </li>
        <li>
          <code>--limit</code> sets the number of results (20 by default). <code>--json</code> prints JSON for scripts.
        </li>
      </ul>
      <p>
        Results come newest first, with the chat and a snippet. Matched words are marked in [brackets].
      </p>

      <h2>How queries work</h2>
      <ul className="ticks">
        <li>
          Every word must match. <code>launch date</code> finds messages that contain both words, anywhere in the
          message.
        </li>
        <li>
          The last word also matches as a prefix, so <code>invoic</code> finds &quot;invoice&quot; and
          &quot;invoices&quot;.
        </li>
        <li>
          Quotes keep a phrase together: <code>&quot;launch date&quot;</code> only finds the two words side by side.
        </li>
        <li>
          OR gives alternatives: <code>contract OR agreement</code>.
        </li>
      </ul>
      <p>
        Not sure which chat? List them first with <code>dotiphant chats</code>, filtered by name, kind or recent
        activity:
      </p>
      <Cmd lines={["dotiphant chats acme", "dotiphant chats --kind group --active 7d"]} />

      <h2>Ask your AI instead</h2>
      <p>
        Sometimes you do not know the words at all, only the topic. Connect your AI app with{" "}
        <code>dotiphant connect</code> and ask in plain language: &quot;What did Clara and I agree on about the launch
        date?&quot; or &quot;Find the wallet address someone sent me in the Acme group.&quot; The assistant runs the
        searches, reads the matching parts of the chats and answers with the context. See{" "}
        <Link href="/guides/telegram-mcp-server/">A local Telegram MCP server</Link>. The AI provider receives the
        messages its assistant reads.
      </p>
      <p>
        For a quick look at what happened lately, <code>dotiphant recent</code> prints a digest of the last 24 hours, or
        any span with <code>--since</code>.
      </p>

      <h2>Limits</h2>
      <ul className="ticks">
        <li>Search covers what is in the archive. Older history needs one <code>--all-history</code> sync.</li>
        <li>
          Photos and files are downloaded on demand. Use the button in the viewer or <code>dotiphant media CHAT</code>.
        </li>
        <li>The archive keeps messages even after they are edited or deleted in Telegram, until you delete them.</li>
      </ul>
      <p>
        Want the result as a transcript? See{" "}
        <Link href="/guides/export-telegram-chat-to-markdown/">Export a Telegram chat to Markdown</Link>.
      </p>
    </GuidePage>
  );
}
