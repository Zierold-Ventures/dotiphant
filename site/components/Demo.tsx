import { Terminal } from "./Terminal";

export function Demo() {
  return (
    <section id="demo" className="sec demo" aria-labelledby="demo-h">
      <div className="wrap demo__grid">
        <div className="demo__copy">
          <h2 id="demo-h">Try it without Telegram.</h2>
          <p className="head__sub">
            <code>dotiphant demo</code> opens the viewer on a made-up archive: Acme x Northwind, Priya Raman, Kestrel
            Protocol Builders and a few more. No login, no API keys. It&apos;s stored apart from your real data and runs
            on its own port, so it can sit next to <code>dotiphant open</code>.
          </p>
          <p className="fine">
            Prefer your real chats without logging in? <code>dotiphant import</code> reads a Telegram Desktop JSON
            export.
          </p>
        </div>
        <div className="demo__terms">
          <Terminal
            copy="dotiphant demo"
            label="Demo command"
            lines={[
              { cmd: "dotiphant demo" },
              { out: "Demo data lives in its own folder and is separate from your own archive." },
              { out: "Dotiphant viewer at http://127.0.0.1:8764 (Ctrl+C to stop)" },
            ]}
          />
          <Terminal
            className="term--quiet"
            copy={'dotiphant export "Acme" --since 7d | claude -p "What are the open action items?"'}
            label="Export a chat as Markdown into another tool"
            lines={[
              { note: "# Any chat as Markdown, straight into another tool" },
              { cmd: 'dotiphant export "Acme" --since 7d | claude -p "What are the open action items?"' },
            ]}
          />
        </div>
      </div>
    </section>
  );
}
