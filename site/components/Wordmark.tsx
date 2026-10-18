// Placeholder wordmark: a lens (a ring with a dot in focus) and the name in the text face.
// The final logo replaces this component; it takes the current text color.
export function LensMark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <circle cx="16" cy="16" r="12.25" fill="none" stroke="currentColor" strokeWidth="3.5" />
      <circle className="lens-dot" cx="19" cy="13" r="4.25" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={`wordmark ${className ?? ""}`}>
      <LensMark className="wordmark__mark" />
      <span className="wordmark__name">dotiphant</span>
    </span>
  );
}
