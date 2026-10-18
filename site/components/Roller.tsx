"use client";

import { useEffect, useState } from "react";

// Rolls through the assistant names. Stays on the first one with reduced motion.
// The names are CSS generated content, so the text of the page holds only `label`.
export function Roller({ words, label, interval = 2600 }: { words: string[]; label: string; interval?: number }) {
  const [i, setI] = useState(0);

  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setI((n) => (n + 1) % words.length), interval);
    return () => clearInterval(t);
  }, [words.length, interval]);

  return (
    <>
      <span className="sr">{label}</span>
      <span className="roller" aria-hidden="true">
        {words.map((w, k) => (
          <span
            key={w}
            data-w={w}
            className={`roller__w${k === i ? " is-on" : k === (i - 1 + words.length) % words.length ? " is-out" : ""}`}
          />
        ))}
      </span>
    </>
  );
}
