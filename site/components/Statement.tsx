"use client";

import { useEffect, useRef } from "react";

const LINES = [
  "Your deals, partners and community live in Telegram.",
  "Your AI assistant can't see any of it.",
  "Dotiphant copies your chats into one file on your own computer, and lets the assistant read it when you ask.",
];

// The words light up one by one as the section scrolls through the viewport.
// Without JS, or with reduced motion, every word is lit from the start.
export function Statement() {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = root.current;
    if (!el || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const words = Array.from(el.querySelectorAll<HTMLElement>(".lit__w"));
    el.classList.add("is-armed");
    let frame = 0;
    const update = () => {
      frame = 0;
      const r = el.getBoundingClientRect();
      // 0 when the text enters the lower part of the screen, 1 when it reaches the upper third.
      const p = Math.min(1, Math.max(0, (innerHeight * 0.85 - r.top) / (r.height + innerHeight * 0.35)));
      const lit = Math.round(p * words.length * 1.15);
      // Once lit, a word stays lit.
      words.forEach((w, i) => {
        if (i < lit) w.classList.add("is-on");
      });
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll);
    return () => {
      removeEventListener("scroll", onScroll);
      removeEventListener("resize", onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);

  let k = 0;
  return (
    <section className="sec statement" aria-label="Why Dotiphant">
      <div className="wrap">
        <div className="lit" ref={root}>
          {LINES.map((line, l) => (
            <p key={l} className={l === 2 ? "lit__answer" : undefined}>
              {line.split(" ").map((w) => (
                <span key={k} className="lit__w" style={{ ["--i" as string]: k++ }}>
                  {w}{" "}
                </span>
              ))}
            </p>
          ))}
        </div>
      </div>
    </section>
  );
}
