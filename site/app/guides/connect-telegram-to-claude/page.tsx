import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/connect-telegram-to-claude/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            Install Dotiphant, let it archive your Telegram chats on your computer, then run{" "}
            <code>dotiphant connect claude-desktop</code> or <code>dotiphant connect claude-code</code>. Claude can then
            search and read your chats through Dotiphant&apos;s read-only MCP server.
          </p>
          <p>
            claude.ai on the web and phone works too, through an HTTPS tunnel you run and an approval with a one-time
            pairing code.
          </p>
        </>
      }
    >
      <h2>Which Claude do you use?</h2>
      <p>
        Claude comes in a few forms, and they connect in two different ways. Claude Desktop and Claude Code run on your
        computer, so they can start Dotiphant themselves. claude.ai in the browser and the Claude mobile apps run on
        Anthropic&apos;s side, so they can only reach a server on the internet.
      </p>
      <div className="table glass">
        <table>
          <thead>
            <tr>
              <th scope="col">App</th>
              <th scope="col">How it connects</th>
              <th scope="col">Command</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">Claude Desktop</th>
              <td>Local, starts Dotiphant over stdio</td>
              <td>
                <code>dotiphant connect claude-desktop</code>
              </td>
            </tr>
            <tr>
              <th scope="row">Claude Code</th>
              <td>Local, added through the <code>claude</code> command</td>
              <td>
                <code>dotiphant connect claude-code</code>
              </td>
            </tr>
            <tr>
              <th scope="row">claude.ai, web and phone</th>
              <td>Remote, through a tunnel you run</td>
              <td>
                <code>dotiphant mcp --http --public-url URL</code>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <h2>Claude Desktop and Claude Code</h2>
      <ol className="steps">
        <li>
          Install Dotiphant and finish the setup in your browser. You create your own free API id at my.telegram.org,
          scan a QR code with the Telegram app, choose what to archive and run the first sync. The last setup step
          already offers to connect the AI apps it finds.
          <Cmd lines={["curl -fsSL https://dotiphant.com/install.sh | sh"]} label="Install" />
        </li>
        <li>
          If you skipped that step, connect Claude now. Without a name, <code>dotiphant connect</code> lists the apps
          and whether it found them.
          <Cmd
            lines={["dotiphant connect", "dotiphant connect claude-desktop", "dotiphant connect claude-code"]}
          />
        </li>
        <li>
          Quit Claude completely and start it again. On macOS that means Cmd+Q, not just closing the window. For Claude Code,
          start a new session.
        </li>
        <li>
          Ask Claude something only your chats know, like &quot;Summarize what happened in my groups since
          yesterday.&quot;
        </li>
      </ol>
      <p>
        <code>dotiphant connect claude-desktop</code> writes a <code>dotiphant</code> entry into Claude Desktop&apos;s
        config file and keeps a backup of the old file. Add <code>--dry-run</code> to see the change first, or{" "}
        <code>--print</code> to get the snippet and paste it yourself. Claude Code is connected through its{" "}
        <code>claude</code> command; if that command is missing, Dotiphant prints the command to run later.
      </p>

      <h2>Claude Code and the terminal</h2>
      <p>
        In Claude Code you can also skip MCP and pipe a transcript straight in. <code>dotiphant export</code> prints a
        chat as Markdown:
      </p>
      <Cmd lines={['dotiphant export "Acme" --since 7d | claude -p "What are the open action items?"']} />
      <p>
        More on that in <Link href="/guides/export-telegram-chat-to-markdown/">Export a Telegram chat to Markdown</Link>.
      </p>

      <h2>claude.ai on the web and phone</h2>
      <p>
        claude.ai only reaches MCP servers on the internet over HTTPS. Dotiphant still listens only on 127.0.0.1, so you
        put an HTTPS tunnel in front of it: ngrok, Tailscale Funnel or Cloudflare Tunnel.
      </p>
      <ol className="steps">
        <li>
          Start a tunnel to port 8766 and copy its <code>https://</code> address.
          <Cmd lines={["ngrok http 8766"]} label="Tunnel" />
        </li>
        <li>
          In another terminal, start Dotiphant with that address. It prints the pairing code. Leave it running.
          <Cmd lines={["dotiphant mcp --http --public-url https://abc.ngrok.app"]} />
        </li>
        <li>
          In claude.ai, open Settings &gt; Connectors and choose Add custom connector. On Team and Enterprise plans an
          owner adds it under Organization settings &gt; Connectors.
        </li>
        <li>
          Enter a name and your tunnel address with <code>/mcp</code> at the end, like{" "}
          <code>https://abc.ngrok.app/mcp</code>. Choose Add, then Connect.
        </li>
        <li>
          The approval page opens. Check the return address: its host should be <code>claude.ai</code>. Then enter the
          pairing code and choose Approve, or run the <code>dotiphant mcp --approve</code> command the page shows.
        </li>
      </ol>
      <p>
        Once added, the connector is also available in the Claude mobile apps. Menu names may differ between versions
        and plans. Every step, and how to manage or revoke access, is in the{" "}
        <Link href="/docs/remote-access/">remote access docs</Link>.
      </p>

      <h2>What to know before you connect</h2>
      <ul className="ticks">
        <li>
          Anthropic sees the messages Claude reads to answer you: text, sender names, chat names and dates. That
          is how any connector works, under Anthropic&apos;s privacy terms.
        </li>
        <li>
          Dotiphant&apos;s tools only read. Claude cannot send, edit or delete anything in Telegram through Dotiphant.
          It can draft a reply for you; you send it yourself.
        </li>
        <li>
          For claude.ai, your computer must be on and awake, with Dotiphant and the tunnel running. Stop both when you
          no longer need them.
        </li>
        <li>
          Messages are marked as untrusted content for the AI. This lowers the risk of prompt injection but cannot rule
          it out.
        </li>
      </ul>

      <h2>When Claude does not see Dotiphant</h2>
      <p>
        Quit the app completely and start it again. Then run <code>dotiphant doctor</code>; it checks the installation
        and prints fixes. <code>dotiphant status</code> shows whether you are logged in and when the last sync ran.
      </p>
      <Cmd lines={["dotiphant doctor", "dotiphant status"]} />
    </GuidePage>
  );
}
