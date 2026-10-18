import type { ReactNode } from "react";
import { GITHUB } from "@/lib";

// Thin line drawings. Every path has pathLength=1, so CSS can draw it in when the card scrolls into view.
const P = (d: string) => <path d={d} pathLength={1} />;

const drawings: Record<string, ReactNode> = {
  install: (
    <svg viewBox="0 0 200 150" aria-hidden="true">
      {P("M30 30h140a8 8 0 0 1 8 8v80a8 8 0 0 1-8 8H30a8 8 0 0 1-8-8V38a8 8 0 0 1 8-8z")}
      {P("M22 48h156")}
      {P("M42 70l12 10-12 10")}
      {P("M64 90h46")}
      {P("M42 106h80")}
      <circle cx="34" cy="39" r="2.5" />
      <circle cx="44" cy="39" r="2.5" />
      <circle cx="54" cy="39" r="2.5" />
    </svg>
  ),
  qr: (
    <svg viewBox="0 0 200 150" aria-hidden="true">
      {P("M76 14h48a8 8 0 0 1 8 8v106a8 8 0 0 1-8 8H76a8 8 0 0 1-8-8V22a8 8 0 0 1 8-8z")}
      {P("M92 22h16")}
      {P("M80 44h16v16H80zM104 44h16v16h-16zM80 68h16v16H80z")}
      {P("M104 68h6v6h-6zM114 78h6v6h-6zM104 80h4")}
      {P("M84 100h32")}
      {P("M84 110h22")}
      {P("M40 75h18M142 75h18")}
    </svg>
  ),
  ask: (
    <svg viewBox="0 0 200 150" aria-hidden="true">
      {P("M24 30h82a8 8 0 0 1 8 8v24a8 8 0 0 1-8 8H48l-12 10v-10h-4a8 8 0 0 1-8-8V38a8 8 0 0 1 8-8z")}
      {P("M38 46h56M38 56h36")}
      {P("M176 84H96a8 8 0 0 0-8 8v22a8 8 0 0 0 8 8h52l12 10v-10h8a8 8 0 0 0 8-8V92a8 8 0 0 0-8-8z")}
      {P("M102 98h58M102 108h40")}
      <circle className="dot" cx="150" cy="44" r="13" fill="none" />
      <circle className="dot dot--fill" cx="153" cy="41" r="4.5" />
    </svg>
  ),
};

const steps = [
  {
    n: "01",
    title: "Install and open",
    art: "install",
    body: (
      <>
        Install Dotiphant with the install script, then <code>dotiphant open</code> starts the setup in your browser and
        brings the viewer back later. You create your own free Telegram API id at my.telegram.org; it takes about a
        minute.
      </>
    ),
  },
  {
    n: "02",
    title: "Scan the QR code",
    art: "qr",
    body: (
      <>
        In the Telegram app, go to Settings &gt; Devices &gt; Link Desktop Device, and point your phone at the code.
        Choose what to keep: people and groups by default, the last 365 days or everything. The first sync starts.
      </>
    ),
  },
  {
    n: "03",
    title: "Ask your assistant",
    art: "ask",
    body: (
      <>
        The setup connects the AI apps it finds on your computer. Or run <code>dotiphant connect claude-desktop</code>{" "}
        (or <code>claude-code</code>, <code>codex</code>, <code>cursor</code>), restart the app, and ask.
      </>
    ),
  },
];

export function HowItWorks() {
  return (
    <section id="how" className="sec how" aria-labelledby="how-h">
      <div className="wrap">
        <header className="head">
          <h2 id="how-h">Three steps, about five minutes.</h2>
          <p className="head__sub">No account with us. No server to run. The setup is a page in your browser.</p>
        </header>
        <ol className="cards3">
          {steps.map((s) => (
            <li className="scard glass" key={s.n}>
              <span className="scard__n">{s.n}</span>
              <h3>{s.title}</h3>
              <div className="scard__art">{drawings[s.art]}</div>
              <p>{s.body}</p>
            </li>
          ))}
        </ol>
        <p className="fine how__foot">
          Not comfortable with a terminal? Give{" "}
          <a href={`${GITHUB}/blob/main/docs/install-with-ai.md`}>install-with-ai.md</a> to Claude Code, Codex or
          another AI assistant that can run commands, and it will install Dotiphant with you.
        </p>
      </div>
    </section>
  );
}
