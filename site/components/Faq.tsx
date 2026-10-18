"use client";

import Link from "next/link";
import { useState, type MouseEvent } from "react";
import { FAQ, segments } from "@/content/faq";
import { GITHUB } from "@/lib";

function Paragraph({ text }: { text: string }) {
  return (
    <p>
      {segments(text).map((s, i) =>
        s.code ? (
          <code key={i}>{s.text}</code>
        ) : s.href?.startsWith("/") ? (
          <Link key={i} href={s.href}>{s.text}</Link>
        ) : s.href ? (
          <a key={i} href={s.href}>{s.text}</a>
        ) : (
          s.text
        ),
      )}
    </p>
  );
}

const links = [
  { label: "Docs", note: "Install, remote access, security", href: "/docs/" },
  { label: "Guides", note: "Claude, ChatGPT, Cursor, search, export", href: "/guides/" },
  { label: "Security guide", note: "What is stored where, how to wipe it", href: "/docs/security/" },
  { label: "GitHub", note: "Code, releases, issues", href: GITHUB },
];

function LinkBody({ label, note }: { label: string; note: string }) {
  return (
    <>
      <span className="faq__link">{label}</span>
      <span className="faq__note">{note}</span>
      <span className="faq__arrow" aria-hidden="true">
        <svg viewBox="0 0 20 20"><path d="M6 14 14 6M8 6h6v6" /></svg>
      </span>
    </>
  );
}

export function Faq() {
  const [open, setOpen] = useState<number | null>(0);

  function toggle(i: number, e: MouseEvent) {
    e.preventDefault();
    setOpen((cur) => (cur === i ? null : i));
  }

  return (
    <section id="faq" className="sec faq" aria-labelledby="faq-h">
      <div className="wrap faq__grid">
        <div className="faq__side">
          <h2 id="faq-h">Questions, answered plainly.</h2>
          <p className="fine faq__more">
            More in the <a href={`${GITHUB}/blob/main/docs/faq.md`}>FAQ on GitHub</a>, or ask in{" "}
            <a href={`${GITHUB}/discussions`}>Discussions</a>.
          </p>
          <ul className="faq__links">
            {links.map((l) => (
              <li key={l.label}>
                {l.href.startsWith("/") ? (
                  <Link href={l.href}>
                    <LinkBody label={l.label} note={l.note} />
                  </Link>
                ) : (
                  <a href={l.href}>
                    <LinkBody label={l.label} note={l.note} />
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
        <div className="faq__list glass">
          {FAQ.map((item, i) => (
            <details key={item.q} open={open === i}>
              <summary onClick={(e) => toggle(i, e)}>
                <span>{item.q}</span>
                <span className="faq__icon" aria-hidden="true" />
              </summary>
              <div className="a">
                {item.a.map((p) => (
                  <Paragraph key={p} text={p} />
                ))}
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
