import Link from "next/link";
import { Cmd, GuidePage } from "@/components/GuidePage";
import { RemoteRisk } from "@/components/Trust";
import { page } from "@/content/pages";
import { metadataFor } from "@/content/seo";

const info = page("/guides/connect-telegram-to-chatgpt/");
export const metadata = metadataFor(info);

export default function Page() {
  return (
    <GuidePage
      info={info}
      answer={
        <>
          <p>
            ChatGPT can read your Telegram archive through a custom connector. You run{" "}
            <code>dotiphant mcp --http</code> on your computer behind an HTTPS tunnel, add the tunnel address in
            ChatGPT, and approve the connection with a one-time pairing code.
          </p>
          <p>
            Custom connectors need a paid ChatGPT plan, and your computer has to be on while you use it.
          </p>
        </>
      }
    >
      <h2>Why ChatGPT needs a tunnel</h2>
      <p>
        Claude Desktop, Claude Code, Codex and Cursor run on your computer and start Dotiphant themselves. ChatGPT runs on
        OpenAI&apos;s servers. It can only reach MCP servers on the internet over HTTPS, and your archive lives on your
        own computer, with no cloud service in between.
      </p>
      <p>
        So you give your computer a public HTTPS address with a tunnel (ngrok, Tailscale Funnel or Cloudflare Tunnel).
        The tunnel forwards requests to Dotiphant on 127.0.0.1. Dotiphant itself never listens on your network.
      </p>

      <h2>The risk, plainly</h2>
      <RemoteRisk />

      <h2>The trade-offs</h2>
      <ul className="ticks">
        <li>
          Anyone who finds the tunnel address still needs your approval. Approving takes a command in your terminal or
          a one-time pairing code that only appears on your computer.
        </li>
        <li>
          OpenAI sees the messages ChatGPT reads to answer you: text, sender names, chat names and dates. That is how
          every connector works.
        </li>
        <li>
          Your computer must be on, awake and running both Dotiphant and the tunnel. Stop both when you no longer need
          them.
        </li>
        <li>Custom connectors need a paid ChatGPT plan and work in ChatGPT on the web.</li>
        <li>
          A Cloudflare quick tunnel gets a new address every time it starts, so you have to update the connector each
          time. ngrok&apos;s free fixed domain and Tailscale Funnel keep the same address.
        </li>
      </ul>

      <h2>Step 1: start a tunnel</h2>
      <p>
        Install Dotiphant and finish the setup first (see <Link href="/docs/install/">Install</Link>). Then pick one
        tunnel and keep it running in its own terminal. Each prints an <code>https://</code> address.
      </p>
      <Cmd
        label="Tunnel, pick one"
        lines={[
          "ngrok http 8766",
          "tailscale funnel 8766",
          "cloudflared tunnel --url http://127.0.0.1:8766",
        ]}
      />
      <p>
        ngrok needs a free account and its <code>ngrok config add-authtoken</code> step. Tailscale Funnel must be allowed
        for your tailnet. Cloudflare&apos;s quick tunnel needs no account.
      </p>

      <h2>Step 2: start Dotiphant</h2>
      <p>In another terminal, with your tunnel address:</p>
      <Cmd lines={["dotiphant mcp --http --public-url https://abc.ngrok.app"]} />
      <p>
        It prints the pairing code. Leave it running. <code>--public-url</code> tells Dotiphant its public name, so its
        approval pages and host check use that address; it still listens only on 127.0.0.1.
      </p>

      <h2>Step 3: add the connector in ChatGPT</h2>
      <ol className="steps">
        <li>Open Settings &gt; Apps &amp; Connectors &gt; Advanced settings and turn on Developer mode.</li>
        <li>Back in Apps &amp; Connectors, choose Create.</li>
        <li>
          Enter a name, like &quot;Dotiphant&quot;, and your tunnel address with <code>/mcp</code> at the end, like{" "}
          <code>https://abc.ngrok.app/mcp</code>. Choose OAuth as authentication and leave the client id and secret
          empty: ChatGPT registers itself. Confirm that you trust the connector.
        </li>
        <li>
          The approval page opens. Check the return address: its host should be <code>chatgpt.com</code>. Then enter
          the pairing code and choose Approve.
        </li>
        <li>In a new chat, turn on the connector from the tools menu.</li>
      </ol>
      <p>Menu names may differ between versions and plans.</p>

      <h2>Approving safely</h2>
      <p>
        The approval page shows the app&apos;s name and the full address your browser returns to, with the host
        highlighted. The app name is chosen by the app and proves nothing. The host is what counts.
      </p>
      <p>
        The safer way is the terminal: the page shows a command like <code>dotiphant mcp --approve K7QM-2XPA</code>. Run
        it on your computer. It prints the app name and the exact return address and asks y/N. Then choose Continue on
        the page.
      </p>
      <p>
        The pairing code works once and expires after 30 minutes. Five wrong codes close that approval request. Run{" "}
        <code>dotiphant mcp --pair</code> to see the current code or get a new one.
      </p>

      <h2>Managing access</h2>
      <Cmd
        lines={[
          "dotiphant mcp --clients  # apps you approved",
          "dotiphant mcp --revoke CLIENT_ID  # remove one app",
          "dotiphant mcp --revoke all  # remove every approved app",
        ]}
      />
      <p>
        A revoked app&apos;s tokens stop working at once. ChatGPT can search and read your archive and start a refresh,
        at most one sync per minute. It can never send anything to Telegram through Dotiphant.
      </p>

      <h2>If it does not connect</h2>
      <ul className="ticks">
        <li>
          &quot;Invalid Host header&quot;: the address in <code>--public-url</code> does not match the tunnel. Restart
          Dotiphant with the exact address.
        </li>
        <li>
          ChatGPT cannot connect: check that both the tunnel and <code>dotiphant mcp --http</code> are running, and
          that the URL ends in <code>/mcp</code>.
        </li>
        <li>
          The code expired: run <code>dotiphant mcp --pair</code> for a new one, or approve in the terminal.
        </li>
      </ul>
      <p>
        Every detail, including Tailscale and Cloudflare setup, is in the{" "}
        <Link href="/docs/remote-access/">remote access docs</Link>. What is stored and who can reach it:{" "}
        <Link href="/docs/security/">Security and privacy</Link>.
      </p>
    </GuidePage>
  );
}
