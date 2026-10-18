import { Terminal } from "./Terminal";
import { Roller } from "./Roller";
import Link from "next/link";
import { MATCHES, STREAM } from "./stream";
import { asset, GITHUB, INSTALL, RELEASES } from "@/lib";

const QUERY = "launch";
const COPIES = 4; // the drifting columns repeat their messages so the loop is seamless
const HIT_COPIES = 2;

// The backdrop is decoration. Its message text lives once, in CSS generated content, and the
// repeated bubbles in the HTML are empty. So screen readers, crawlers, search snippets and
// AI readers never see dozens of chat messages before the headline, while the server-rendered
// first paint still shows the full backdrop.
const css = (s: string) => `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/</g, "\\3c ")}"`;

function backdropCss(): string {
  const rules: string[] = [];
  STREAM.forEach((col, c) =>
    col.forEach((m, k) =>
      rules.push(
        `.stream__col:nth-child(${c + 1}) .bubble:nth-child(${col.length}n+${k + 1}){--c:${css(m.chat)};--w:${css(m.who)};--t:${css(m.text)}}`,
      ),
    ),
  );
  MATCHES.forEach((m, k) => {
    rules.push(`.lens__track .hit:nth-child(${MATCHES.length}n+${k + 1}){--c:${css(m.chat)};--w:${css(m.who)}}`);
    parts(m.text).forEach((p, j) =>
      rules.push(`.lens__track .hit:nth-child(${MATCHES.length}n+${k + 1}) .hit__text>:nth-child(${j + 1})::before{content:${css(p)}}`),
    );
  });
  rules.push(
    ".bubble__chat::before,.hit__chat::before{content:var(--c)}",
    ".bubble__who::before,.hit__who::before{content:var(--w)}",
    ".bubble__text::before{content:var(--t)}",
    `.lens__qt::before{content:${css(QUERY)}}`,
    `.lens__count::before{content:${css("7 matches in 2 chats")}}`,
  );
  return rules.join("\n");
}

// A match split around the query: plain, query, plain, ... The query parts are the odd ones.
function parts(text: string): string[] {
  return text.split(new RegExp(`(${QUERY})`, "i"));
}

// One drifting layer of blurred chat messages behind the lens. The bubbles are empty; CSS fills them.
function Stream() {
  return (
    <div className="layer layer--blur">
      <div className="stream">
        {STREAM.map((col, c) => (
          <div className="stream__col" key={c} style={{ ["--speed" as string]: `${64 + c * 9}s`, ["--shift" as string]: `${(c % 2) * -18}%` }}>
            <div className="stream__track">
              {Array.from({ length: COPIES * col.length }, (_, k) => (
                <div className="bubble" key={k}>
                  <span className="bubble__chat" />
                  <span className="bubble__who" />
                  <span className="bubble__text" />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// Inside the lens, the chats come into focus: the matches for "launch" as whole, sharp cards, rolling slowly upward.
function Results() {
  return (
    <div className="lens__results">
      <div className="lens__track">
        {Array.from({ length: HIT_COPIES * MATCHES.length }, (_, k) => (
          <div className="hit" key={k}>
            <span className="hit__chat" />
            <span className="hit__who" />
            <span className="hit__text">
              {parts(MATCHES[k % MATCHES.length].text).map((_, j) => (j % 2 ? <mark key={j} /> : <span key={j} />))}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Hero() {
  return (
    <section className="hero" id="top" aria-labelledby="hero-h">
      <div className="hero__stage">
        <style dangerouslySetInnerHTML={{ __html: backdropCss() }} />
        <div className="hero__backdrop" aria-hidden="true" data-nosnippet="">
          <Stream />
          <div className="lens">
            <Results />
            <span className="lens__q">
              <span className="lens__icon" />
              <span className="lens__qt" />
              <span className="lens__caret" />
            </span>
            <span className="lens__count" />
          </div>
          <div className="hero__shade" />
        </div>

        <div className="hero__content">
          <div className="hero__copy">
            {/* The headline reads "Now your AI assistant can search your Telegram." exactly once: the rolling app
                names are CSS generated content, with "AI assistant" as hidden text in their place. */}
            <h1 id="hero-h" className="hero__title">
              <span className="hero__line">
                Now your <Roller label="AI assistant" words={["Claude", "ChatGPT", "Codex", "Cursor"]} />
              </span>{" "}
              <span className="hero__line">can search your Telegram.</span>
            </h1>
            {/* Shown above the headline (CSS order), read after it, so the headline is the first text on the page. */}
            <p className="hero__kicker">A small open-source tool. Read-only. No cloud.</p>
            <p className="hero__lede">
              Dotiphant reads your Telegram, never writes to it, and keeps the archive in one file on your computer.
              Your AI assistant can answer from it. Set up in about five minutes.
            </p>
          </div>

          <div className="hero__install" id="install">
            <Terminal
              label="Install commands"
              copy={INSTALL}
              caret
              // "| sh" never wraps away from the URL on a phone; the copy button still copies the plain command.
              lines={[{ cmd: INSTALL.replace(" | ", "\u00a0|\u00a0") }, { cmd: "dotiphant open", note: "  # setup opens in your browser" }]}
            />
            <p className="fine">
              macOS and Linux; Windows experimental. The script (<a href={asset("/install.sh")}>read it first</a>)
              installs uv if needed, then the <a href={RELEASES}>latest release</a> from GitHub, checked against its
              published checksums (<Link href="/docs/security/#verify">verify it yourself</Link>). Open source, MIT.
            </p>
            <div className="hero__cta">
              <a className="pill pill--light pill--lg" href={GITHUB}>
                View on GitHub
              </a>
              <a className="pill pill--lg" href="#demo">
                Try the demo
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
