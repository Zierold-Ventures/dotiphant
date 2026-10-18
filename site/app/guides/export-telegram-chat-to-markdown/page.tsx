import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/export-telegram-chat-to-markdown/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            With Dotiphant, you can turn any Telegram chat into a clean Markdown transcript. In the local viewer, copy
            selected messages or the last 7 days, or download the whole chat as a file. In the terminal,{" "}
            <code>dotiphant export CHAT</code> prints the chat as Markdown, for any time range, ready to save or pipe
            into an AI tool.
          </p>
        </>
      }
    >
      <h2>Why Markdown</h2>
      <p>
        Telegram Desktop can export chats as HTML or JSON. Both are made for machines or for archiving, not for
        reading or pasting into an AI assistant. Markdown is plain text with a little structure: every AI tool reads it
        well, it fits into notes apps and Git repositories, and you can read it yourself without any viewer.
      </p>
      <p>
        Dotiphant keeps a read-only archive of your chats on your computer, so exporting is a local operation. It sends
        no request to Telegram and works offline.
      </p>

      <h2>Before you start</h2>
      <p>
        Install Dotiphant and run the setup once. It takes about five minutes and loads the last 365 days of your
        people and groups by default. For older messages, run <code>dotiphant sync --all-history</code> once. See{" "}
        <Link href="/docs/install/">Install Dotiphant</Link>.
      </p>

      <h2>In the viewer</h2>
      <ol className="steps">
        <li>
          Open the viewer. It runs on your computer at 127.0.0.1 and opens in your browser.
          <Cmd lines={["dotiphant open"]} />
        </li>
        <li>Open the chat you want.</li>
        <li>
          Pick what to export. &quot;Copy last 7 days&quot; puts the last week on your clipboard. &quot;Export
          chat&quot; downloads the whole chat as a Markdown file. To copy only some messages, select them and choose
          &quot;Copy as Markdown&quot;.
        </li>
        <li>Paste the result into your AI assistant, a note or a document.</li>
      </ol>

      <h2>On the command line</h2>
      <p>
        <code>dotiphant export</code> prints a chat as Markdown. CHAT can be a chat id, an <code>@username</code> or
        part of the title. <code>dotiphant chats</code> lists the chats in your archive if you are not sure of the name.
      </p>
      <Cmd
        lines={[
          'dotiphant export "Acme"  # the whole chat',
          'dotiphant export "Acme" --since 7d  # the last 7 days',
          'dotiphant export "Acme" --since 2026-09-01 --until 2026-09-30',
          'dotiphant export @clara --limit 200  # the newest 200 messages',
          'dotiphant export "Acme" --since 30d --out acme.md  # save to a file',
        ]}
      />
      <p>
        <code>--since</code> takes a duration like <code>24h</code> or <code>7d</code>, or a date like{" "}
        <code>2026-09-01</code>. <code>--until</code> ends the range, <code>--limit</code> keeps only the newest
        messages, and <code>--out</code> (or <code>-o</code>) writes a file instead of printing. The file is created
        readable only by your user account.
      </p>

      <h2>What the transcript looks like</h2>
      <p>The transcript is built to be easy to read, for people and for AI:</p>
      <ul className="ticks">
        <li>
          A heading with the chat title, then one line that says whether it is a Telegram or WhatsApp chat, what kind
          of chat it is, how many messages it holds and which dates it covers. Times are in UTC.
        </li>
        <li>
          One heading per message, with the date and time, the sender and the message id, like{" "}
          <code>## 2026-09-14 10:02, Clara (#4812)</code>.
        </li>
        <li>Forum topics, forwards, replies and edits are noted with the message, so the context survives.</li>
        <li>Attachments show as a short label in brackets, like the file type and name, not as the file itself.</li>
      </ul>

      <h2>Pipe it into an AI tool</h2>
      <p>
        Because the export goes to standard output, you can send it straight into any command line AI tool. For
        example, with Claude Code:
      </p>
      <Cmd lines={['dotiphant export "Acme" --since 7d | claude -p "What are the open action items?"']} />
      <p>
        If your AI app supports MCP, you may not need an export at all: connect it with{" "}
        <code>dotiphant connect</code> and it can search and read your chats on its own. See{" "}
        <Link href="/guides/telegram-mcp-server/">A local Telegram MCP server</Link>.
      </p>

      <h2>Already have a Telegram Desktop export?</h2>
      <p>
        You can import it. Import is experimental: it is tested with sample files, not yet with many real exports.
        In Telegram Desktop, choose &quot;Export chat history&quot; for one chat, or Settings &gt;
        Advanced &gt; &quot;Export Telegram data&quot; for the account, with the format &quot;Machine-readable
        JSON&quot;. Then add it to the archive and export it as Markdown like any other chat:
      </p>
      <Cmd lines={["dotiphant import ~/Downloads/Telegram\\ Desktop/ChatExport_2024-01-31"]} />
      <p>
        The path can be the export folder or its <code>result.json</code>. Importing again is safe: messages already
        in the archive are kept as they are.
      </p>

      <h2>Limits</h2>
      <ul className="ticks">
        <li>The export holds what your archive holds. Secret chats are never included, and channels are off by default.</li>
        <li>Media files are not embedded in the Markdown. Download them in the viewer or with <code>dotiphant media CHAT</code>.</li>
        <li>
          When you paste a transcript into an AI app, its provider receives those messages, under its privacy terms.
          The transcript also contains other people&apos;s messages, so share it with care.
        </li>
      </ul>
    </GuidePage>
  );
}
