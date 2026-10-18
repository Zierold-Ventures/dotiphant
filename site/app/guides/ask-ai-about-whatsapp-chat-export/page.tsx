import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/ask-ai-about-whatsapp-chat-export/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Export the chat with WhatsApp&apos;s own &quot;Export chat&quot;, then run{" "}
            <code>dotiphant import</code> on the file. The chat joins a local archive on your computer, where you can
            search it, export it as Markdown, and let your AI assistant read it. Nothing connects to WhatsApp, and
            Dotiphant never logs in to it. Import is experimental: it is tested with sample files, not yet with
            many real exports.
          </p>
        </>
      }
    >
      <h2>Why not just paste the file into a chat?</h2>
      <p>
        For a short chat, pasting works. A long group chat quickly gets too big for one prompt, and the export&apos;s
        raw format, with phone-specific date styles and media placeholders, is hard to search. After an import, your
        AI assistant can search the chat and read only the parts it needs, and you can ask follow-up questions without
        pasting anything again.
      </p>

      <h2>1. Export the chat in WhatsApp</h2>
      <ul className="ticks">
        <li>
          <strong>iPhone:</strong> open the chat, tap the contact or group name, then Export Chat.
        </li>
        <li>
          <strong>Android:</strong> open the chat, tap the three dots, then More &gt; Export chat.
        </li>
      </ul>
      <p>
        Choose &quot;Attach media&quot; if you want photos and files too. Save the .zip (or the .txt) on your
        computer.
      </p>

      <h2>2. Import it</h2>
      <ol className="steps">
        <li>
          Install Dotiphant, if you have not yet. Importing a WhatsApp export needs no Telegram login and no API id.
          <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
        </li>
        <li>
          Import the file. <code>--me</code> is your name exactly as the export shows it, so your own messages are
          marked as yours.
          <Cmd lines={['dotiphant import "WhatsApp Chat - Clara.zip" --me "Your Name"']} />
        </li>
      </ol>
      <p>
        The path can be the .zip, the chat .txt, or the unpacked folder. Dotiphant detects a WhatsApp export by
        itself; <code>--source whatsapp</code> forces it. Useful options:
      </p>
      <ul className="ticks">
        <li>
          <code>--copy-media</code> copies attached photos and files from the .zip or folder into the archive.
        </li>
        <li>
          <code>--title &quot;Chat name&quot;</code> sets the chat name. By default it comes from the file name.
        </li>
        <li>
          <code>--tz Europe/Berlin</code> sets the time zone of the export. The export has local times without a zone,
          so Dotiphant reads them in your computer&apos;s time zone. Use this if the export came from a phone in
          another zone, and use the same <code>--tz</code> for every export of that chat.
        </li>
        <li>
          <code>--date-order mdy</code> (or <code>dmy</code>) when dates look wrong. Dotiphant tells day-first from
          month-first by looking at the whole file. If the file cannot tell, it says so and uses day-first.
        </li>
      </ul>
      <p>
        Supported: iPhone and Android exports, 12 and 24 hour clocks, messages over several lines, system lines like
        &quot;X added Y&quot;, media placeholders, deleted messages and the &quot;edited&quot; marker. A chat with more
        than two people is stored as a group.
      </p>

      <h2>3. Ask your AI about it</h2>
      <p>Connect your AI app once. Claude Desktop, Claude Code, Codex and Cursor work locally:</p>
      <Cmd lines={["dotiphant connect claude-desktop  # or claude-code, codex, cursor"]} />
      <p>
        Restart the app, then ask in plain words: &quot;What did Clara and I decide about the flat?&quot; or
        &quot;Summarize the group chat from last month.&quot; The assistant searches the archive and reads the
        messages it needs. ChatGPT and claude.ai on the web work too, through{" "}
        <Link href="/docs/remote-access/">remote access</Link>.
      </p>
      <p>Prefer the terminal? Search the chat, or hand a Markdown transcript to any AI tool:</p>
      <Cmd
        lines={[
          'dotiphant search "deposit" --chat "Clara"',
          'dotiphant export "Clara" --since 2026-09-01 --out clara.md',
          "dotiphant open  # browse it in the viewer",
        ]}
      />

      <h2>Importing a newer export</h2>
      <p>
        WhatsApp chats are never synced: Dotiphant has no live link to WhatsApp, so the archive holds what you
        imported. To add newer messages, export the chat again and import it again. The chat name decides which chat
        the messages go to. Importing the same export twice adds nothing. A newer export adds only messages that are
        not stored yet: a message counts as stored when its time, sender and text match. A message edited between two
        exports is added again in its new form. Nothing is ever deleted.
      </p>

      <h2>Honest limits</h2>
      <ul className="ticks">
        <li>
          A live WhatsApp connection is not supported. WhatsApp has no official API for personal accounts, and an
          unofficial one may put your account at risk.
        </li>
        <li>One export is one chat. Import each chat you want on its own.</li>
        <li>
          When your AI app reads the chat, its provider receives those messages. The chat also holds the other
          person&apos;s messages, so think about what you share.
        </li>
        <li>To remove an imported chat, run <code>dotiphant forget CHAT</code>. It deletes the chat and its media from the archive.</li>
      </ul>
      <p>
        Dotiphant mainly archives Telegram. If you use both, both kinds of chats sit in the same archive, search and AI
        tools. See <Link href="/guides/search-telegram-history/">Search your whole Telegram history</Link>.
      </p>
    </GuidePage>
  );
}
