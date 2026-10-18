import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";
import { AiAccessNote } from "@/components/Trust";
import { GITHUB } from "@/lib";

const info = page("/docs/security/");
export const metadata = metadataFor(info);

const stored: [string, string][] = [
  ["Messages, chat names, sender names", "dotiphant.db, a SQLite file in the data folder"],
  ["Downloaded photos and files", "media/ in the data folder, only for chats where you asked for media"],
  ["Your choices", "settings.json in the data folder"],
  [
    "Approved remote apps, hashed tokens, open approval requests, pairing code",
    "oauth.json in the data folder, readable only by you",
  ],
  [
    "Sync progress",
    "status.json and logs/ in the data folder. These never contain message text, phone numbers or secrets",
  ],
  [
    "Telegram API id and hash, login session, HTTP MCP token, viewer key",
    'The system keychain, under "Dotiphant (unofficial Telegram archive)"',
  ],
];

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Dotiphant stores your archive in a private folder on your computer and your Telegram session and keys in
            the system keychain. It only reads from Telegram. The viewer listens on 127.0.0.1 only, and the
            MCP server listens on 127.0.0.1 unless you start it with <code>--allow-remote</code>.
          </p>
          <p>
            When you ask an AI app a question, the messages it reads go to that app&apos;s provider. Two commands and
            one deleted folder remove everything.
          </p>
        </>
      }
    >
      <h2>What is stored where</h2>
      <div className="table glass">
        <table>
          <thead>
            <tr>
              <th scope="col">What</th>
              <th scope="col">Where</th>
            </tr>
          </thead>
          <tbody>
            {stored.map(([what, where]) => (
              <tr key={what}>
                <th scope="row">{what}</th>
                <td>{where}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        The data folder is <code>~/Library/Application Support/Dotiphant</code> on macOS,{" "}
        <code>~/.local/share/dotiphant</code> on Linux and <code>%APPDATA%\Dotiphant</code> on Windows, or whatever{" "}
        <code>DOTIPHANT_HOME</code> points to. It is created readable only by your user account.{" "}
        <code>dotiphant status</code> prints the exact path.
      </p>
      <p>
        On a computer without a keychain (for example a headless Linux server), secrets go to <code>secrets/</code> in
        the data folder as files only your user can read. Set <code>DOTIPHANT_SECRETS=file</code> to always use these
        files, for example in a container. <code>dotiphant status</code> shows which one is in use.
      </p>
      <p>
        The archive itself is not encrypted beyond what your disk encryption provides. Turn on FileVault (macOS),
        BitLocker (Windows) or full-disk encryption (Linux).
      </p>

      <h2>The session is your account</h2>
      <p>
        The Telegram session that Dotiphant stores gives full access to your Telegram account, the same as a
        logged-in Telegram Desktop. Anyone who can read your keychain or your user account can use it. Protect your
        computer like you protect your phone: a login password, screen lock and disk encryption.
      </p>
      <p>
        You can see and end the session at any time in the Telegram app under Settings &gt; Devices. It shows up as
        &quot;Dotiphant&quot;.
      </p>
      <p>
        A login belongs to the API id and hash that made it. When you replace them (
        <code>dotiphant setup --new-credentials</code>, other values in <code>dotiphant setup</code>, or new values on{" "}
        <code>/setup</code>), Dotiphant first tries to end the old session on Telegram, so old entries do not pile up
        under Settings &gt; Devices. If Telegram cannot be reached, it says so; end the old entry there by hand.
      </p>

      <h2>Read-only toward Telegram</h2>
      <p>
        Dotiphant only reads. It never sends, edits, deletes, forwards or reacts to messages, never joins or leaves
        chats and never marks messages as read. Requests are paced and Dotiphant waits whenever Telegram asks it to
        slow down.
      </p>
      <p>
        Syncing never deletes messages from the archive. When a sync sees that a message was edited, Dotiphant keeps
        the earlier text too. A sync looks for edits in a chat&apos;s latest 100 messages, when the chat has new
        messages or when you sync it on its own (<code>dotiphant sync CHAT</code>, or Refresh chat in the
        viewer). Edits to older messages are not fetched. To remove a chat on purpose, for example when someone asks you to delete their messages, run{" "}
        <code>dotiphant forget CHAT</code>. It deletes the chat, its messages and its media from the archive and stops
        syncing it.
      </p>

      <h2 id="verify">Verify a download</h2>
      <p>
        Dotiphant holds your Telegram session, so check that what you install is what the project released. Each{" "}
        <a href={`${GITHUB}/releases`}>GitHub release</a> has three files:
      </p>
      <ul className="ticks">
        <li>
          <code>dotiphant-X.Y.Z-py3-none-any.whl</code>, the wheel that gets installed.
        </li>
        <li>
          <code>dotiphant-X.Y.Z.tar.gz</code>, the source archive.
        </li>
        <li>
          <code>SHA256SUMS</code>, the SHA-256 checksums of the other two.
        </li>
      </ul>
      <p>
        The installer (<a href={`${GITHUB}/blob/main/install.sh`}>install.sh</a>, worth reading first) checks the wheel
        for you. It downloads the wheel and <code>SHA256SUMS</code> from the same release, compares them and prints the
        hash. On a mismatch or a missing file it stops and installs nothing. If the GitHub CLI (<code>gh</code>) is
        installed and logged in, it also runs the attestation check below and reports the result.
      </p>

      <h3>Check the checksum by hand</h3>
      <p>
        Download the wheel and <code>SHA256SUMS</code> from the release page into one folder. In that folder, run:
      </p>
      <Cmd
        lines={[
          "shasum -a 256 -c --ignore-missing SHA256SUMS  # macOS",
          "sha256sum -c --ignore-missing SHA256SUMS  # Linux",
        ]}
      />
      <p>
        Each file you downloaded should show <code>OK</code>. Then install the file with{" "}
        <code>uv tool install ./dotiphant-X.Y.Z-py3-none-any.whl</code>. A checksum from the same release page catches
        a broken or swapped download. It cannot prove who built the file: anyone who can change the release can change
        both.
      </p>

      <h3>Check the build attestation</h3>
      <p>
        Each release also has a signed build attestation (SLSA build provenance) for the wheel, the source archive and{" "}
        <code>SHA256SUMS</code>. GitHub Actions creates it while it builds the files. It records which repository,
        workflow and commit produced each file, and it is signed with <a href="https://www.sigstore.dev/">Sigstore</a>.
        Check it with the <a href="https://cli.github.com">GitHub CLI</a>:
      </p>
      <Cmd
        lines={[
          "gh auth login  # once",
          "gh attestation verify dotiphant-X.Y.Z-py3-none-any.whl --repo Zierold-Ventures/dotiphant",
        ]}
      />
      <p>
        A passing check means the file was built by the release workflow of <code>Zierold-Ventures/dotiphant</code> and
        has not changed since.
      </p>

      <h2>Who can reach the archive</h2>
      <h3>The viewer</h3>
      <ul className="ticks">
        <li>
          <code>dotiphant open</code> listens on 127.0.0.1, so only programs on your computer can reach it. It also
          needs a private key, so other programs and other user accounts on the same computer cannot read the archive.
        </li>
        <li>
          <code>dotiphant open</code> opens a link with the key once; the viewer swaps it for a browser cookie and
          removes it from the address bar. The cookie holds a random session value, not the key. It works only as a
          cookie and stops working when the viewer stops; the next <code>dotiphant open</code> starts a new session.
          <code>dotiphant open --no-browser</code> prints the link instead. Scripts
          can send the key as <code>Authorization: Bearer KEY</code>. The key is created on first use and kept in the
          keychain as <code>viewer_token</code>.
        </li>
        <li>
          The viewer accepts changes (refresh, favorites) from a browser only from its own pages: every{" "}
          <code>/api/</code> request and every POST must carry the page token that each viewer page holds, in addition
          to the cookie. The page token protects against other web pages open in your browser. It does not protect
          against programs: a program that has the cookie can load a viewer page and read the page token from it.
        </li>
        <li>
          The browser setup at <code>/setup</code> follows the same rules. It needs the key, accepts changes only from
          its own pages, never sends the API hash back to the page, keeps the two-step password and login code in
          memory only until Telegram has checked them, limits login attempts, and is off in the demo.
        </li>
        <li>
          Anyone who captures the viewer cookie can read the archive and make changes until the viewer stops.
          Browsers send cookies for 127.0.0.1 to every port on 127.0.0.1, so on a computer shared with other people,
          another user&apos;s program listening on another port could receive your viewer cookie when your browser
          visits it. The cookie never reveals the viewer key, and it cannot stop the viewer. Use the viewer on a
          computer that only you use, and stop it (Ctrl+C or <code>dotiphant stop</code>) when you are done.
        </li>
      </ul>
      <h3>The MCP server</h3>
      <ul className="ticks">
        <li>
          The MCP server normally runs over stdio: your AI app starts it as a child process and nothing listens on the
          network.
        </li>
        <li>
          <code>dotiphant mcp --http</code> listens on 127.0.0.1 and requires a token on every request. Apps that
          support OAuth (ChatGPT, claude.ai) get their own token after you approve them. Approved apps and hashed
          tokens are kept in <code>oauth.json</code>; <code>dotiphant mcp --clients</code> lists them and{" "}
          <code>dotiphant mcp --revoke</code> removes them.
        </li>
        <li>
          Apps that can set headers can use the static bearer token instead, created on first use and kept in the
          keychain; <code>dotiphant mcp --show-token</code> prints it. Anyone with a valid token can read your whole
          archive.
        </li>
        <li>
          <code>--public-url</code> is for an HTTPS tunnel you run; see{" "}
          <Link href="/docs/remote-access/">Remote access</Link>. <code>--allow-remote</code> lets other machines
          connect directly; use it only on a network you control.
        </li>
      </ul>

      <h2>Approving remote apps</h2>
      <p>
        Anyone who finds your tunnel URL can register an app and open an approval page. Only you can approve it, in one
        of two ways:
      </p>
      <ul className="ticks">
        <li>
          In a terminal (safer): the page shows a command like <code>dotiphant mcp --approve K7QM-2XPA</code>. It
          prints the app name and the exact address your browser returns to, and asks y/N. It needs an interactive
          terminal. Then choose Continue on the page.
        </li>
        <li>
          With the pairing code that only your terminal shows (<code>dotiphant mcp --pair</code>). It has 8 digits,
          works once and expires after 30 minutes.
        </li>
      </ul>
      <p>
        The page shows the full return address of this request, with its host highlighted. The authorization goes to
        that address, so approve only if the host belongs to the app you are connecting right now. The app name is
        chosen by the app and proves nothing.
      </p>
      <h3>Limits that keep strangers out without locking you out</h3>
      <ul className="ticks">
        <li>
          Return addresses must use <code>https://</code>, or <code>http://</code> on 127.0.0.1, localhost or [::1].
          Addresses with a user name (<code>user@host</code>) or a fragment (<code>#...</code>) and other schemes such
          as <code>javascript:</code> or <code>data:</code> are refused. An app may register at most 5 addresses of up
          to 500 characters, and a name of up to 100 characters. Other metadata such as logos is not stored or shown.
          Oversized requests are refused.
        </li>
        <li>
          Five wrong pairing codes close that one approval request. After 20 wrong codes within 10 minutes, code entry
          pauses for 10 minutes for everyone. Terminal approval keeps working during the pause. At most about 40
          guesses fit into one code&apos;s lifetime, out of 100 million possible codes.
        </li>
        <li>
          At most 50 unapproved apps and 50 open approval requests are kept. A flood of new ones pushes out the oldest,
          so it cannot block a real app for long. It can push out a request you just started; start connecting again
          if the page says the link expired.
        </li>
        <li>
          Every refresh replaces the app&apos;s tokens. If an old refresh token is ever used again, Dotiphant assumes
          it was copied and revokes every token of that approval. The app then has to be approved again.
        </li>
        <li>
          <code>oauth.json</code> is changed under a file lock, so <code>dotiphant mcp --revoke</code> and a running
          server never overwrite each other.
        </li>
      </ul>
      <p>
        Remaining risks: if you approve a request without checking the return address, the app at that address gets
        read access. A stranger can also keep pausing code entry; use terminal approval then.
      </p>

      <h2>What an AI app receives</h2>
      <p>
        When you ask an AI app a question, it calls Dotiphant&apos;s tools and receives the matching messages: text,
        sender names, chat names and dates. The app then sends that content to its provider (Anthropic, OpenAI, Cursor
        and so on) to answer you, under that provider&apos;s privacy terms. Dotiphant cannot control what happens after
        that. Only connect apps whose provider you trust with your chats.
      </p>
      <p>
        All tools are read-only. The one exception is <code>refresh</code>, which runs a sync to fetch new messages;
        it still only reads from Telegram. <code>refresh</code> does not start a new sync within 60 seconds of the
        last one; it reports the last result and when the next refresh is allowed. This keeps a looping or tricked AI
        app from hammering Telegram.
      </p>

      <h2>Prompt injection</h2>
      <p>
        Anyone who can message you can put text like &quot;ignore previous instructions&quot; into your archive.
        Dotiphant marks every message it hands to an AI as untrusted content and tells the AI not to follow
        instructions inside it. The marker lines use runs of three <code>&lt;</code> or <code>&gt;</code>; any such
        run inside a message is spaced out, so a message cannot fake the end of the untrusted block.
      </p>
      <p>
        This lowers the risk but does not remove it, because the AI model makes the final decision. Since
        Dotiphant&apos;s tools cannot send messages or change anything, a tricked AI still cannot act in Telegram
        through Dotiphant. Be careful when the same AI app also has tools that can send email, run commands or move
        money.
      </p>

      <h2 id="ai-access">Choose which chats AI apps can read</h2>
      <p>
        <AiAccessNote />
      </p>

      <h2>Your responsibilities toward the people you chat with</h2>
      <p>
        Your archive holds other people&apos;s messages. They did not choose to use Dotiphant. Before you connect an AI
        app, hide the chats that should stay out of it (see <a href="#ai-access">above</a>).
      </p>
      <ul className="ticks">
        <li>
          Keep the archive for your own use. Do not publish it, sell it or share it with people who were not in the
          conversation.
        </li>
        <li>
          Before you connect an AI app, check how its provider handles your data. Some consumer plans may use
          conversations to improve their models unless you opt out.
        </li>
        <li>
          Be careful with sensitive topics, such as health, politics, religion or money. Only send them to an AI app if
          you would be comfortable forwarding the messages yourself.
        </li>
        <li>
          If someone asks you to delete their messages, honor it with <code>dotiphant forget CHAT</code>.
        </li>
        <li>
          If you use Dotiphant for work, data protection law (such as the GDPR in the EU) likely applies in full. Ask
          your data protection officer before you start.
        </li>
      </ul>
      <p>
        The full app privacy notice is on <a href={`${GITHUB}/blob/main/docs/privacy.md`}>GitHub</a>.
      </p>

      <h2 id="wipe">Wiping everything</h2>
      <Cmd
        lines={[
          "dotiphant schedule remove  # stop background refresh",
          "dotiphant logout --forget-credentials  # end the session on Telegram, remove every stored secret",
        ]}
      />
      <p>
        <code>--forget-credentials</code> removes the session, the API id and hash, the viewer key, the MCP token and
        the approved remote apps (<code>oauth.json</code>). If Telegram cannot be reached, the command says so: end the
        session in the Telegram app under Settings &gt; Devices. Running viewers and a running{" "}
        <code>dotiphant mcp --http</code> check the stored keys every few seconds. Within a few seconds of the command,
        they stop accepting the old keys and tokens, on any port, and a viewer shuts down at its next request. The
        one exception is a token passed with <code>--token</code>: it works until you stop that server.
      </p>
      <p>
        Then delete the data folder (the path is shown by <code>dotiphant status</code>). If you tried{" "}
        <code>dotiphant demo</code>, also delete the demo folder next to it (&quot;Dotiphant Demo&quot;, or
        &quot;dotiphant-demo&quot; on Linux). To remove Dotiphant from your AI apps, delete the <code>dotiphant</code>{" "}
        entry from their MCP settings, or for Claude Code run <code>claude mcp remove dotiphant</code>. Finally, run{" "}
        <code>uv tool uninstall dotiphant</code> (or <code>pipx uninstall dotiphant</code>).
      </p>
      <p>
        Messages that AI apps already read stay with their providers. Delete those conversations in each app if you
        want them gone.
      </p>

      <h2>Telegram terms</h2>
      <p>
        Dotiphant uses the public Telegram API with API credentials that you create yourself, so you are the developer
        of record under the <a href="https://core.telegram.org/api/terms">Telegram API Terms of Service</a>. In
        practice:
      </p>
      <ul className="ticks">
        <li>Use it for your own account and your own chats.</li>
        <li>
          Dotiphant does not train AI models. Using Telegram data with AI apps is subject to the{" "}
          <a href="https://core.telegram.org/api/terms">Telegram API Terms of Service</a> and the{" "}
          <a href="https://telegram.org/tos/content-licensing">Terms for Content Licensing and AI Scraping</a>. Check them before you connect an AI
          app; you are responsible for how you use your archive.
        </li>
        <li>Do not present Dotiphant or anything built on it as an official Telegram app.</li>
      </ul>
      <p>Dotiphant is an independent, unofficial project. It is not affiliated with Telegram.</p>
    </GuidePage>
  );
}
