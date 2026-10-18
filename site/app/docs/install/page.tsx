import Link from "next/link";
import { CopyButton } from "@/components/CopyButton";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";
import { GITHUB, RELEASES } from "@/lib";

const info = page("/docs/install/");
export const metadata = metadataFor(info);

const commands: [string, string][] = [
  ["dotiphant open", "Open the viewer at http://127.0.0.1:8765, with setup on first run. dotiphant serve does the same"],
  ["dotiphant setup", "The same guided setup in the terminal. Safe to re-run; finished steps are skipped"],
  ["dotiphant sync [CHAT ...]", "Fetch new messages now, for all chats or the ones named"],
  ["dotiphant status", "Login, archive size, last sync, background refresh"],
  ["dotiphant doctor", "Check the installation and print fixes"],
  ["dotiphant connect [APP]", "Connect an AI app to your archive, or list the apps it can connect"],
  ["dotiphant chats", "List archived chats"],
  ["dotiphant search QUERY", "Full-text search, with --chat, --since, --json"],
  ["dotiphant export CHAT", "Print a chat as Markdown, --since 7d, --out FILE"],
  ["dotiphant recent", "What happened in the last 24 hours"],
  ["dotiphant media CHAT", "Download photos, files and voice notes of a chat"],
  ["dotiphant schedule install", "Refresh in the background every N minutes"],
  ["dotiphant mcp", "The MCP server (AI apps start this themselves)"],
  ["dotiphant mcp --http --public-url URL", "Remote access for ChatGPT, claude.ai and other remote MCP apps"],
  ["dotiphant import PATH", "Add a Telegram Desktop JSON export (result.json), or a chat from WhatsApp's own \"Export chat\" (.zip or .txt), to the archive. Experimental. Nothing connects to WhatsApp"],
  ["dotiphant forget CHAT", "Delete a chat and its media from the archive, and stop syncing it"],
  ["dotiphant demo", "Try the viewer with made-up chats"],
  ["dotiphant login / dotiphant logout", "Log in again, or end the session"],
];

const AI_PROMPT = `Please install Dotiphant for me, following the instructions at
https://github.com/Zierold-Ventures/dotiphant/blob/main/docs/install-with-ai.md
Explain each step in plain words before you run it, and tell me exactly
what to do whenever you need something from me.`;

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            On macOS or Linux, paste <code>curl -fsSL https://dotiphant.com/install.sh | sh</code> into a terminal.
            It installs the latest Dotiphant release and opens the setup in your browser, which takes about five
            minutes. Windows support is experimental.
          </p>
        </>
      }
    >
      <h2>Quick start</h2>
      <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
      <p>
        The script is about 150 lines; <a href={`${GITHUB}/blob/main/install.sh`}>read it first</a>. It installs{" "}
        <a href="https://docs.astral.sh/uv/">uv</a> if needed (uv brings its own Python), installs the latest
        Dotiphant release from GitHub with uv, and runs <code>dotiphant open</code>. Before installing, it checks the
        download against the release's SHA-256 checksums and stops on a mismatch. To update, run the same command again.
      </p>
      <p>
        You can also check signed build provenance with <code>gh attestation verify</code>; see{" "}
        <Link href="/docs/security/#verify">Verify a download</Link>.
      </p>

      <h2>Other ways to install</h2>
      <p>
        Dotiphant ships as <a href={RELEASES}>GitHub releases</a>, not on PyPI. If you already have uv:
      </p>
      <div className="table glass">
        <table>
          <thead>
            <tr>
              <th scope="col">Goal</th>
              <th scope="col">Command</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Install a release</th>
              <td>
                <code>uv tool install &lt;URL of the .whl file on the release page&gt;</code>, then{" "}
                <code>dotiphant open</code>
              </td>
            </tr>
            <tr>
              <th scope="row">From a local clone</th>
              <td>
                <code>uv tool install .</code>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p>
        To install by hand and check the file yourself: download the <code>.whl</code> file and{" "}
        <code>SHA256SUMS</code> from the <a href={RELEASES}>release page</a> into one folder, check it, then install
        the file:
      </p>
      <Cmd
        lines={[
          "shasum -a 256 -c --ignore-missing SHA256SUMS  # Linux: sha256sum",
          "uv tool install ./dotiphant-X.Y.Z-py3-none-any.whl",
        ]}
      />
      <p>
        The command is <code>dotiphant</code>; <code>phant</code> is a short alias. If the command is not found after
        installing, run <code>uv tool update-shell</code> and open a new shell.
      </p>

      <h2>Setup</h2>
      <p>
        <code>dotiphant open</code> walks you through setup in your browser. It takes about 5 minutes. Prefer the
        terminal? <code>dotiphant setup</code> asks the same questions there. Both are safe to run again.
      </p>
      <ol className="steps">
        <li>
          Create your own Telegram API id and hash at <a href="https://my.telegram.org">my.telegram.org</a> (free,
          about a minute). Telegram requires every app to have one. Click &quot;API development tools&quot;, fill in
          the form (app title and short name can be anything, platform Desktop) and copy &quot;App api_id&quot; and
          &quot;App api_hash&quot; into the setup page.
        </li>
        <li>
          Log in by scanning a QR code with the Telegram app (Settings &gt; Devices &gt; Link Desktop Device). Phone
          login works too. If your account has a two-step verification password, the page asks for it.
        </li>
        <li>Choose what to archive: people and groups by default, the last 365 days or everything.</li>
        <li>Run the first sync. It takes from a minute to an hour for large accounts, and you can continue while it runs.</li>
        <li>Connect the AI apps it finds on your computer, and turn on background refresh.</li>
      </ol>
      <p>Afterwards:</p>
      <Cmd
        lines={[
          "dotiphant open  # browse and search in your browser",
          "dotiphant connect claude-desktop  # or claude-code, codex, cursor",
        ]}
      />
      <p>
        Fully quit and restart the AI app after connecting it. Run <code>dotiphant status</code> to check the login,
        archive size and last sync, and <code>dotiphant doctor</code> if anything looks wrong.
      </p>

      <h2>Try it without Telegram</h2>
      <Cmd lines={["dotiphant demo"]} />
      <p>
        This opens the viewer on a made-up archive, stored separately from your real data. No login needed. It uses
        port 8764, so it can run next to <code>dotiphant open</code>.
      </p>

      <h2>Install with an AI assistant</h2>
      <p>
        Not comfortable with a terminal? An AI assistant that can run commands on your computer (Claude Code, Codex,
        Cursor, or Claude Desktop with a terminal tool) can install Dotiphant with you. Copy this prompt into it:
      </p>
      <div className="term" role="group" aria-label="Prompt for your AI assistant">
        <div className="term__bar" aria-hidden="true">
          <span />
          <span />
          <span />
          <b>Prompt</b>
        </div>
        <pre className="term__body">
          <code>{AI_PROMPT}</code>
        </pre>
        <CopyButton text={AI_PROMPT} />
      </div>
      <p>
        You will need your phone with Telegram on it. You type your API id, hash and any codes into the setup page in
        your browser yourself, never into the AI chat.
      </p>

      <h2>Commands</h2>
      <div className="table glass">
        <table>
          <thead>
            <tr>
              <th scope="col">Command</th>
              <th scope="col">What it does</th>
            </tr>
          </thead>
          <tbody>
            {commands.map(([cmd, what]) => (
              <tr key={cmd}>
                <th scope="row">
                  <code>{cmd}</code>
                </th>
                <td>{what}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p>
        CHAT can be a chat id, an <code>@username</code> or part of the title. Run{" "}
        <code>dotiphant COMMAND --help</code> for all options. Remote access is explained in{" "}
        <Link href="/docs/remote-access/">Remote access</Link>.
      </p>
      <Cmd lines={['dotiphant export "Acme" --since 7d | claude -p "What are the open action items?"']} />

      <h2>Update and uninstall</h2>
      <p>
        To update, run the install command again. To remove Dotiphant and everything it stored, follow{" "}
        <Link href="/docs/security/#wipe">Wiping everything</Link>: stop the background refresh, log out with{" "}
        <code>--forget-credentials</code>, delete the data folder, and run <code>uv tool uninstall dotiphant</code>.
      </p>
    </GuidePage>
  );
}
