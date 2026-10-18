import type { ReactNode } from "react";
import { CopyButton } from "./CopyButton";

export type TermLine = { cmd?: string; out?: string; note?: string };
// A line with both cmd and note shows the note as a dimmed comment after the command.

export function Terminal({
  lines,
  copy,
  label = "Terminal",
  className = "",
  caret = false,
}: {
  lines: TermLine[];
  copy?: string;
  label?: string;
  className?: string;
  caret?: boolean; // a blinking cursor after the last line
}) {
  return (
    <div className={`term ${className}`} role="group" aria-label={label}>
      <div className="term__bar" aria-hidden="true">
        <span />
        <span />
        <span />
        <b>Terminal</b>
      </div>
      <pre className="term__body">
        <code>
          {lines.map((l, i): ReactNode => {
            if (l.cmd !== undefined)
              return (
                <span className="term__line" key={i}>
                  <span className="p" aria-hidden="true">$ </span>
                  {l.cmd}
                  {l.note ? <span className="term__note">{l.note}</span> : null}
                  {caret && i === lines.length - 1 ? <span className="term__caret" aria-hidden="true" /> : null}
                </span>
              );
            if (l.note !== undefined)
              return (
                <span className="term__note is-line" key={i}>
                  {l.note}
                </span>
              );
            return (
              <span className="term__out" key={i}>
                {l.out}
              </span>
            );
          })}
        </code>
      </pre>
      {copy ? <CopyButton text={copy} /> : null}
    </div>
  );
}
