"use client";

import { useEffect, useState, type ReactNode } from "react";

// The fixed header sits on the dark hero first. Once the hero has scrolled away,
// it switches its wordmark to the page ink ("is-past").
export function ScrollHeader({ children, overHero }: { children: ReactNode; overHero: boolean }) {
  const [past, setPast] = useState(!overHero);

  useEffect(() => {
    if (!overHero) return;
    const hero = document.getElementById("top");
    const onScroll = () => {
      const edge = hero ? hero.getBoundingClientRect().bottom : 0;
      setPast(edge < 64);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [overHero]);

  return <header className={`top${past ? " is-past" : " is-over"}`}>{children}</header>;
}
