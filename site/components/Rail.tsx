"use client";

import { useRef } from "react";
import { asset } from "@/lib";

type Card = {
  tag: string;
  img?: { src: string; w: number; h: number; alt: string; zoom?: number; origin?: string };
  bubble: string;
  mono?: boolean;
  text: string;
};

const CARDS: Card[] = [
  {
    tag: "Search",
    img: { src: "/img/search.webp", w: 800, h: 720, alt: "Search results for 'launch' across chats, with the matching word highlighted in each message." },
    bubble: "launch",
    mono: true,
    text: "Full-text search across all chats. Filter by chat and time.",
  },
  {
    tag: "Chats",
    img: { src: "/img/chats.webp", w: 800, h: 720, alt: "Chat list in the Dotiphant viewer: Acme x Northwind integration and Priya Raman starred as favorites, then Lumen Wallet partners, Helios Labs and Orbit Ventures, Kestrel Protocol Builders and more, with message counts." },
    bubble: "People, groups, forum topics.",
    text: "Every chat your account can read, from the first message. Channels if you want them.",
  },
  {
    tag: "Copy as Markdown",
    img: { src: "/img/chat.webp", w: 1040, h: 800, alt: "Two messages selected in the Acme x Northwind integration chat, with a Copy as Markdown button.", zoom: 1.7, origin: "50% 92%" },
    bubble: "Copy last 7 days",
    text: "Select a few messages, or the last 7 days, and paste them anywhere: a doc, a ticket, any AI chat.",
  },
  {
    tag: "Log in",
    img: { src: "/img/setup.webp", w: 860, h: 640, alt: "Dotiphant setup page, step 2 of 5: Log in to Telegram, with a QR code and numbered instructions to open Settings > Devices > Link Desktop Device.", zoom: 1.6, origin: "70% 20%" },
    bubble: "Settings > Devices > Link Desktop Device",
    text: "Scan a QR code with the Telegram app. The login stays in your system keychain.",
  },
  {
    tag: "Connect",
    img: { src: "/img/connect.webp", w: 800, h: 720, alt: "Dotiphant's Connect AI page listing one command each for Claude Desktop, Claude Code, Codex and Cursor, with example questions below." },
    bubble: "dotiphant connect cursor",
    mono: true,
    text: "The setup connects the AI apps it finds on your computer. Restart the app, and ask.",
  },
  {
    tag: "WhatsApp import",
    bubble: "dotiphant import",
    mono: true,
    text: "Export a chat in WhatsApp (Export chat) and import the file. It joins the same archive, search and AI tools. Nothing connects to WhatsApp.",
  },
];

export function Rail() {
  const track = useRef<HTMLDivElement>(null);

  function move(dir: number) {
    const el = track.current;
    if (!el) return;
    const card = el.querySelector<HTMLElement>(".rcard");
    const step = card ? card.offsetWidth + 24 : 320;
    const smooth = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollBy({ left: dir * step, behavior: smooth ? "smooth" : "auto" });
  }

  return (
    <section className="sec rail" aria-labelledby="rail-h">
      <div className="wrap head head--row">
        <h2 id="rail-h">One archive. Many ways in.</h2>
        <div className="rail__ctrl">
          <p className="head__sub">
            Dotiphant also works without an AI. <code>dotiphant open</code> gives you every archived chat in your
            browser.
          </p>
          <div className="show__arrows">
            <button type="button" className="arrow" onClick={() => move(-1)} aria-label="Scroll cards back">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5" /></svg>
            </button>
            <button type="button" className="arrow" onClick={() => move(1)} aria-label="Scroll cards forward">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 4.5 13 10l-5.5 5.5" /></svg>
            </button>
          </div>
        </div>
      </div>
      <div className="rail__track" ref={track} tabIndex={0} role="region" aria-label="What you can do with Dotiphant">
        {CARDS.map((c) => (
          <article className="rcard glass" key={c.tag}>
            <h3 className="rcard__tag">{c.tag}</h3>
            <div className={`rcard__media${c.img ? "" : " rcard__media--file"}`}>
              {c.img ? (
                <img
                  src={asset(c.img.src)}
                  width={c.img.w}
                  height={c.img.h}
                  loading="lazy"
                  alt={c.img.alt}
                  style={{
                    ["--z" as string]: c.img.zoom ?? 1.5,
                    ["--o" as string]: c.img.origin ?? "0 0",
                  }}
                />
              ) : (
                <div className="file" aria-hidden="true">
                  <span className="file__sheet">
                    <span className="file__ext">.zip</span>
                    <span className="file__name">WhatsApp Chat</span>
                  </span>
                  <span className="file__arrow" />
                  <span className="file__lens" />
                </div>
              )}
              <span className={`rcard__bubble${c.mono ? " is-mono" : ""}`}>{c.bubble}</span>
            </div>
            <p className="rcard__text">{c.text}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
