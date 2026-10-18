import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/back-up-telegram-chats-locally/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Dotiphant copies your Telegram chats into one SQLite file in a private folder on your computer and keeps it
            up to date, in the background every 15 minutes if you want. It only reads from Telegram, has no cloud, and
            never deletes a message from the archive when it syncs.
          </p>
          <p>
            It is an archive to search and read, not a tool that restores messages back into Telegram.
          </p>
        </>
      }
    >
      <h2>Why keep a local copy</h2>
      <p>
        Your chat history lives on Telegram&apos;s servers. Messages get deleted or edited, people leave groups, and
        searching years of history in the app is slow. A local copy gives you the full history of your chats with
        people and groups in a file you control, with fast full-text search, and readable by your AI assistant if you
        connect one.
      </p>

      <h2>Set it up</h2>
      <ol className="steps">
        <li>
          Install Dotiphant. The setup opens in your browser and takes about five minutes: create your own free API id
          at my.telegram.org, scan a QR code with the Telegram app, choose what to archive, and run the first sync.
          <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
        </li>
        <li>
          The first sync loads the last 365 days by default. For everything, run a full sync once. Large accounts take
          a while; you can stop with Ctrl-C, and the next sync continues where it stopped.
          <Cmd lines={["dotiphant sync --all-history"]} />
        </li>
        <li>
          Turn on background refresh, if you did not in the setup. It runs a sync every 15 minutes by default;{" "}
          <code>--every</code> sets another interval in minutes.
          <Cmd lines={["dotiphant schedule install", "dotiphant schedule install --every 60  # once an hour"]} />
        </li>
        <li>
          Check that it works. <code>dotiphant status</code> shows the login, the archive size, the last sync, whether
          background refresh is on, and the path of the data folder.
          <Cmd lines={["dotiphant status"]} />
        </li>
      </ol>

      <h2>Where the archive lives</h2>
      <p>
        Everything is in one data folder, created readable only by your user account. The messages are in{" "}
        <code>dotiphant.db</code>, a SQLite file.
      </p>
      <div className="table glass">
        <table>
          <thead>
            <tr>
              <th scope="col">System</th>
              <th scope="col">Data folder</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">macOS</th>
              <td><code>~/Library/Application Support/Dotiphant</code></td>
            </tr>
            <tr>
              <th scope="row">Linux</th>
              <td><code>~/.local/share/dotiphant</code></td>
            </tr>
            <tr>
              <th scope="row">Windows (experimental)</th>
              <td><code>%APPDATA%\Dotiphant</code></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        Set <code>DOTIPHANT_HOME</code> to use another folder. Your Telegram login session and API id are not in this
        folder: they are in the system keychain.
      </p>

      <h2>What the archive keeps</h2>
      <ul className="ticks">
        <li>The full history of your chats with people and groups, including forum topics and groups that were upgraded to supergroups.</li>
        <li>
          Messages that were deleted in Telegram stay in the archive, because a sync never deletes stored messages.
          Edits to a chat&apos;s recent messages are picked up when the chat gets new messages or is synced on its
          own, and Dotiphant keeps the earlier text too.
        </li>
        <li>
          Photos, files and voice notes are downloaded on demand, to save space. Use the button in the viewer, or{" "}
          <code>dotiphant media CHAT</code>.
        </li>
        <li>
          Channels are off by default, because they are often large and easy to reread in Telegram. Turn them on with{" "}
          <code>dotiphant setup --reconfigure</code>.
        </li>
        <li>
          Secret chats are not included. They are end-to-end encrypted between two devices and never reach
          Telegram&apos;s servers in readable form.
        </li>
      </ul>

      <h2>Protect the archive</h2>
      <p>
        Dotiphant does not encrypt the archive beyond what your disk encryption provides. Turn on FileVault (macOS),
        BitLocker (Windows) or full-disk encryption (Linux). The stored Telegram session gives full access to your
        account, the same as a logged-in Telegram Desktop, so protect this computer like your phone: a login password,
        screen lock and disk encryption.
      </p>
      <p>
        If you copy the data folder to another backup, the same care applies there. It holds other people&apos;s
        messages, not only yours.
      </p>

      <h2>Remove a chat</h2>
      <p>
        If someone asks you to delete their messages, or you no longer want a chat kept, remove it. This deletes the
        chat, its messages and its media from the archive and stops syncing it.
      </p>
      <Cmd lines={['dotiphant forget "Chat name"']} />
      <p>
        To keep a chat out of the archive from the start, add its id to <code>excluded_chats</code> in{" "}
        <code>settings.json</code> in the data folder. Messages already archived stay until you run{" "}
        <code>dotiphant forget</code>.
      </p>

      <h2>Honest limits</h2>
      <ul className="ticks">
        <li>
          It is a read-only archive. Dotiphant cannot restore messages into Telegram, and it never sends, edits or
          deletes anything there.
        </li>
        <li>Your computer has to be on for background refresh to run. Messages deleted before the next sync are not caught.</li>
        <li>Windows support is experimental.</li>
      </ul>
      <p>
        Once the archive is there, you can <Link href="/guides/search-telegram-history/">search it</Link>,{" "}
        <Link href="/guides/export-telegram-chat-to-markdown/">export chats as Markdown</Link>, or read the full{" "}
        <Link href="/docs/security/">security notes</Link>.
      </p>
    </GuidePage>
  );
}
