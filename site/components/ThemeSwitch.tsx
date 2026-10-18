"use client";

import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";

const KEY = "dotiphant-theme";
const OPTIONS: { value: Theme; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];
let fadeTimer: ReturnType<typeof setTimeout> | undefined;

// The server renders "system"; the saved choice is read after mount.
// The inline script in layout.tsx has already applied it to <html> before paint.
export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    if (t === "light" || t === "dark") setTheme(t);
  }, []);

  function choose(next: Theme) {
    setTheme(next);
    const root = document.documentElement;
    // Fade the colors over for this change only, so the first paint never animates.
    root.classList.add("theme-fade");
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(() => root.classList.remove("theme-fade"), 400);
    if (next === "system") delete root.dataset.theme;
    else root.dataset.theme = next;
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // Storage can be blocked; the choice then lasts for this page view only.
    }
  }

  return (
    <fieldset className="theme">
      <legend>Theme</legend>
      <div className="theme__opts">
        {OPTIONS.map((o) => (
          <label key={o.value}>
            <input
              type="radio"
              name="theme"
              value={o.value}
              checked={theme === o.value}
              onChange={() => choose(o.value)}
            />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
