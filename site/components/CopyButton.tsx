"use client";

import { useEffect, useRef, useState } from "react";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("done");
    } catch {
      setState("failed");
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1800);
  }

  return (
    <button
      type="button"
      className={`copy${state === "done" ? " is-done" : ""}`}
      onClick={copy}
      aria-label={state === "idle" ? `${label}: ${text}` : undefined}
    >
      <span aria-live="polite">{state === "done" ? "Copied" : state === "failed" ? "Select and copy" : label}</span>
    </button>
  );
}
