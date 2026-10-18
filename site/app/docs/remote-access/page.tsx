import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { RemoteRisk } from "@/components/Trust";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/docs/remote-access/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            ChatGPT and claude.ai on the web and phone can only reach MCP servers on the internet over HTTPS. For them, run <code>dotiphant mcp --http</code> and put an HTTPS tunnel you run in
            front of it, such as ngrok, Tailscale Funnel or Cloudflare Tunnel.
          </p>
          <p>
            Every app you connect needs your approval, in your terminal or with a one-time pairing code. Claude
            Desktop, Claude Code, Codex and Cursor do not need this: <code>dotiphant connect</code> is all they need.
          </p>
        </>
      }
    >
      <p>
        A shorter walkthrough for one app: <Link href="/guides/connect-telegram-to-chatgpt/">Connect Telegram to ChatGPT</Link>.
      </p>

      <h2>Is remote access right for you?</h2>
      <RemoteRisk />

      <h2>The trade-off</h2>
      <ul className="ticks">
        <li>
          Anyone who finds the tunnel URL still needs your approval. Each app you connect shows an approval page, and
          approving needs either a command in your terminal or a one-time pairing code that only appears on your
          computer.
        </li>
        <li>
          The AI provider (OpenAI, Anthropic and so on) sees the messages its assistant reads, as with any connector.
        </li>
        <li>
          Your computer must be on, awake and running Dotiphant and the tunnel. Stop both when you no longer need them.
        </li>
      </ul>

      <h2>How it works</h2>
      <ol className="steps">
        <li>
          A tunnel gives your computer a public HTTPS address, like <code>https://abc.ngrok.app</code>, and forwards
          requests to Dotiphant on 127.0.0.1. Dotiphant itself never listens on your network.
        </li>
        <li>
          You add <code>https://abc.ngrok.app/mcp</code> as a connector in the AI app.
        </li>
        <li>
          The app opens an approval page. It shows the app&apos;s name, what it gets (read-only access to your
          archive) and the full address your browser returns to, with the host highlighted. Approve in one of two
          ways:
          <ul className="ticks">
            <li>
              Safer: run the command the page shows, like <code>dotiphant mcp --approve K7QM-2XPA</code>, in a
              terminal on your computer. It prints the app name and the exact return address and asks y/N. Answer y,
              then choose Continue on the page.
            </li>
            <li>Or enter the pairing code from your terminal and choose Approve.</li>
          </ul>
        </li>
        <li>
          The app gets its own token. It can search and read your archive, and it can start a refresh (at most one
          sync per minute). It can never send anything to Telegram.
        </li>
      </ol>
      <p>
        Check the return address before you approve. The access goes to whoever runs that address. The app name is
        chosen by the app and proves nothing; the host is what counts (for example <code>chatgpt.com</code> or{" "}
        <code>claude.ai</code>).
      </p>
      <p>
        The pairing code works once and expires after 30 minutes. Five wrong codes close that approval request; start
        connecting again from the app. After 20 wrong codes within 10 minutes, code entry pauses for 10 minutes.
        Terminal approval still works then. Run <code>dotiphant mcp --pair</code> on your computer to see the current
        code, or get a new one.
      </p>

      <h2>Step 1: start a tunnel</h2>
      <p>
        Pick one. Each prints or shows an <code>https://</code> address. Keep the tunnel running in its own terminal.
      </p>

      <h3>ngrok</h3>
      <ol className="steps">
        <li>
          Create a free account at <a href="https://ngrok.com">ngrok.com</a>, install ngrok and run the{" "}
          <code>ngrok config add-authtoken ...</code> command from your dashboard.
        </li>
        <li>
          Run:
          <Cmd lines={["ngrok http 8766"]} />
        </li>
        <li>
          Copy the <code>https://...ngrok...</code> address from the &quot;Forwarding&quot; line. Free accounts get one
          fixed domain, so the address stays the same next time.
        </li>
      </ol>

      <h3>Tailscale Funnel</h3>
      <ol className="steps">
        <li>
          Install <a href="https://tailscale.com">Tailscale</a> and log in. Funnel must be allowed for your tailnet;
          the first run tells you how if it is not.
        </li>
        <li>
          Run:
          <Cmd lines={["tailscale funnel 8766"]} />
        </li>
        <li>
          Copy the address it prints, like <code>https://my-laptop.tail1234.ts.net</code>. It stays the same.
        </li>
      </ol>

      <h3>Cloudflare Tunnel (quick tunnel)</h3>
      <p>No account needed.</p>
      <ol className="steps">
        <li>
          Install <code>cloudflared</code> (on macOS: <code>brew install cloudflared</code>).
        </li>
        <li>
          Run:
          <Cmd lines={["cloudflared tunnel --url http://127.0.0.1:8766"]} />
        </li>
        <li>
          Copy the <code>https://...trycloudflare.com</code> address it prints. It changes every time you start the
          tunnel, so you have to update the connector each time. For a fixed address, set up a named Cloudflare
          Tunnel.
        </li>
      </ol>

      <h2>Step 2: start Dotiphant</h2>
      <p>In another terminal, with your tunnel address:</p>
      <Cmd lines={["dotiphant mcp --http --public-url https://abc.ngrok.app"]} />
      <p>
        It prints the pairing code. Leave it running. Dotiphant still listens only on 127.0.0.1;{" "}
        <code>--public-url</code> tells it its public name, so its approval pages and the host check use that address.
      </p>

      <h2>Step 3: add the connector</h2>
      <p>
        Use your tunnel address with <code>/mcp</code> at the end, for example <code>https://abc.ngrok.app/mcp</code>.
        Choose OAuth if the app asks for an authentication type. Leave the client id and secret empty: the app
        registers itself. Menu names may differ between versions and plans.
      </p>

      <h3>ChatGPT</h3>
      <p>Custom connectors need a paid plan and work in ChatGPT on the web.</p>
      <ol className="steps">
        <li>Open Settings &gt; Apps &amp; Connectors &gt; Advanced settings and turn on Developer mode.</li>
        <li>Back in Apps &amp; Connectors, choose Create.</li>
        <li>
          Enter a name (for example &quot;Dotiphant&quot;), the URL ending in <code>/mcp</code>, and OAuth as
          authentication. Confirm that you trust the connector.
        </li>
        <li>The approval page opens. Enter the pairing code and choose Approve.</li>
        <li>In a new chat, turn on the connector from the tools menu (Developer mode).</li>
      </ol>

      <h3>claude.ai</h3>
      <p>Works on the web; once added, the connector is also available in the Claude mobile apps.</p>
      <ol className="steps">
        <li>
          Open Settings &gt; Connectors and choose Add custom connector. On Team and Enterprise plans an owner adds it
          under Organization settings &gt; Connectors.
        </li>
        <li>
          Enter a name and the URL ending in <code>/mcp</code>, then choose Add.
        </li>
        <li>Choose Connect. The approval page opens. Enter the pairing code and choose Approve.</li>
      </ol>

      <h3>Other apps</h3>
      <p>
        Any app that supports remote MCP servers with OAuth works the same way. Apps that let you set a header can
        skip OAuth and send <code>Authorization: Bearer &lt;token&gt;</code> instead;{" "}
        <code>dotiphant mcp --show-token</code> prints that token. Anyone who has this token can read your archive, so
        keep it private.
      </p>

      <h2>Managing access</h2>
      <Cmd
        lines={[
          "dotiphant mcp --clients  # apps you approved",
          "dotiphant mcp --revoke CLIENT_ID  # remove one app; its tokens stop working at once",
          "dotiphant mcp --revoke all  # remove every approved app",
          "dotiphant mcp --pair  # show the pairing code to approve another app",
          "dotiphant mcp --approve REQUEST_ID  # approve a waiting app after checking its return address",
        ]}
      />
      <p>
        If an app ever uses an old refresh token a second time, Dotiphant takes it as a sign that the token was copied
        and revokes all of that app&apos;s tokens. Approve the app again if that was a mistake.
      </p>
      <p>
        Approved apps, hashed tokens and the pairing code are stored in <code>oauth.json</code> in the Dotiphant data
        folder, readable only by your user. More in <Link href="/docs/security/">Security and privacy</Link>.
      </p>

      <h2>Troubleshooting</h2>
      <ul className="ticks">
        <li>
          &quot;Invalid Host header&quot;: the address in <code>--public-url</code> does not match the address the app
          uses. Restart Dotiphant with the exact tunnel address.
        </li>
        <li>
          The approval page says the code expired: run <code>dotiphant mcp --pair</code> and use the new code, or use{" "}
          <code>dotiphant mcp --approve</code>.
        </li>
        <li>
          The approval page says code entry is paused: someone entered many wrong codes. Use{" "}
          <code>dotiphant mcp --approve</code> with the id the page shows.
        </li>
        <li>
          The approval page says the link expired: approval requests last 15 minutes. Start connecting again from the
          app.
        </li>
        <li>
          The app cannot register and mentions <code>redirect_uri</code>: Dotiphant only accepts <code>https://</code>{" "}
          return addresses (or <code>http://</code> on 127.0.0.1, localhost or [::1]), at most 5 of them.
        </li>
        <li>
          The app says it cannot connect: check that both the tunnel and <code>dotiphant mcp --http</code> are
          running, and that the URL ends in <code>/mcp</code>.
        </li>
      </ul>
    </GuidePage>
  );
}
