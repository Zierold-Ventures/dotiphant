"use client";

import type { PointerEvent } from "react";

const local = [
  { name: "Claude Desktop", cmd: "connect claude-desktop" },
  { name: "Claude Code", cmd: "connect claude-code" },
  { name: "Codex", cmd: "connect codex" },
  { name: "Cursor", cmd: "connect cursor" },
];

const remote = [
  { name: "ChatGPT", cmd: "mcp --http" },
  { name: "claude.ai", cmd: "web and phone" },
  { name: "Other remote MCP apps", cmd: "mcp --http" },
];

// The glass tiles light up where the pointer is.
function glow(e: PointerEvent<HTMLElement>) {
  const el = e.currentTarget;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--gx", `${e.clientX - r.left}px`);
  el.style.setProperty("--gy", `${e.clientY - r.top}px`);
}

export function WorksWith() {
  return (
    <section className="sec works" aria-labelledby="works-h">
      <div className="wrap">
        <header className="head">
          <h2 id="works-h">Works with the assistant you already use.</h2>
          <p className="head__sub">One archive on your computer. Every app below reads the same file, over MCP.</p>
        </header>

        <div className="works__group">
          <div className="works__label">
            <h3>On your computer, one command</h3>
            <p className="fine">Over MCP. The app starts Dotiphant itself; nothing listens on the network.</p>
          </div>
          <ul className="tiles">
            {local.map((t) => (
              <li key={t.name} className="tile glass" onPointerMove={glow}>
                <span className="tile__name">{t.name}</span>
                <code className="tile__cmd">{t.cmd}</code>
              </li>
            ))}
          </ul>
        </div>

        <div className="works__group">
          <div className="works__label">
            <h3>Remote, through a tunnel you run</h3>
            <p className="fine">
              With ngrok, Tailscale Funnel or Cloudflare Tunnel. This is the riskiest path, because it exposes your
              archive through a tunnel you run. You approve each app yourself, with a terminal command or a one-time
              pairing code that only your terminal shows, and <code>dotiphant ai</code> limits which chats it can read.
              No need for a remote app? The local apps above need no tunnel.
            </p>
          </div>
          <ul className="tiles">
            {remote.map((t) => (
              <li key={t.name} className="tile glass" onPointerMove={glow}>
                <span className="tile__name">{t.name}</span>
                <code className="tile__cmd">{t.cmd}</code>
              </li>
            ))}
            <li className="tile tile--note">
              <span>Custom connectors in ChatGPT need a paid plan.</span>
            </li>
          </ul>
        </div>
      </div>
    </section>
  );
}
