"use client";

import { useEffect } from "react";

// Sections and cards fade up a little the first time they scroll into view. Only content that
// starts below the fold is touched, so nothing visible on load blinks out, and without JS or
// with reduced motion everything simply shows. The CSS lives under "Motion" in globals.css.
const TARGETS = [
  ".head", ".show", ".window", ".rcard", ".scard", ".ledger__col", ".privacy__foot", ".works__group",
  ".table", ".demo__grid > *", ".faq__list", ".faq__side", ".how__foot", ".problems li", ".works__qa", ".lit",
].join(",");

export function Reveal() {
  useEffect(() => {
    if (!("IntersectionObserver" in window) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const io = new IntersectionObserver(
      (entries) => {
        // Items that arrive together are staggered, in document order.
        let k = 0;
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const el = e.target as HTMLElement;
          el.style.setProperty("--d", `${Math.min(k++, 4) * 70}ms`);
          el.classList.add("is-in");
          io.unobserve(el);
        }
      },
      { rootMargin: "0px 0px -8% 0px" },
    );
    for (const el of document.querySelectorAll<HTMLElement>(TARGETS)) {
      if (el.getBoundingClientRect().top < innerHeight) continue;
      el.classList.add("reveal");
      io.observe(el);
    }
    return () => io.disconnect();
  }, []);
  return null;
}
