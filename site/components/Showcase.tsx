"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Slide = {
  app: string;
  how: string;
  question: string;
  calls: { fn: string; args: string }[];
  result: string;
};

// Tool names and arguments are Dotiphant's real MCP tools. The chats are the made-up ones from `dotiphant demo`.
const SLIDES: Slide[] = [
  {
    app: "Claude Desktop",
    how: "On your computer, over MCP. One command: dotiphant connect claude-desktop.",
    question: "What did we agree with Acme about the launch date?",
    calls: [
      { fn: "search_messages", args: 'query_text="launch", chat="Acme"' },
      { fn: "get_messages", args: 'chat="Acme", around_message_id=111' },
    ],
    result:
      "You proposed the 28th, with the 4th as fallback if the security review slips. Maya Chen confirmed: \"Works for us. Locking the 28th.\"",
  },
  {
    app: "Claude Code",
    how: "On your computer, over MCP. One command: dotiphant connect claude-code.",
    question: "Summarize this week in Kestrel Protocol Builders and list open questions.",
    calls: [{ fn: "get_messages", args: 'chat="Kestrel Protocol Builders", since="7d"' }],
    result: "Reads the last 7 days of the group and answers with the chat, the sender, the date and the message id, so you can check.",
  },
  {
    app: "Codex",
    how: "On your computer, over MCP. One command: dotiphant connect codex.",
    question: "Find the security questionnaire Lena sent.",
    calls: [{ fn: "search_messages", args: 'query_text="security questionnaire", sender="Lena"' }],
    result: "Finds Lena Fischer's message in Acme x Northwind integration, with the file Security-questionnaire.xlsx attached.",
  },
  {
    app: "Cursor",
    how: "On your computer, over MCP. One command: dotiphant connect cursor.",
    question: "What did I miss this week, and who is waiting for my reply?",
    calls: [{ fn: "recent_activity", args: 'since="7d"' }],
    result: "Gets the latest messages of every chat from the last 7 days, so the assistant can tell you what is new.",
  },
  {
    app: "ChatGPT",
    how: "Remote, through a tunnel you run. You approve the app with a one-time pairing code.",
    question: "Draft a reply to Priya based on our last conversation.",
    calls: [{ fn: "get_messages", args: 'chat="Priya Raman"' }],
    result: "Writes a draft from your last messages with Priya. Dotiphant never sends a draft. You do.",
  },
];

const DURATION = 7600;

export function Showcase() {
  const [i, setI] = useState(0);
  const [typed, setTyped] = useState(SLIDES[0].question.length);
  const [stage, setStage] = useState<"typing" | "calls" | "done">("done");
  const [paused, setPaused] = useState(false);
  const [still, setStill] = useState(true);
  const root = useRef<HTMLDivElement>(null);
  const visible = useRef(false);

  useEffect(() => {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)");
    setStill(reduce.matches);
    const onChange = () => setStill(reduce.matches);
    reduce.addEventListener("change", onChange);
    let started = false;
    const io = new IntersectionObserver(([e]) => {
      visible.current = e.isIntersecting;
      // The first time the card shows up, type the first question.
      if (e.isIntersecting && !started && !reduce.matches) {
        started = true;
        setTyped(0);
        setStage("typing");
      }
    }, { threshold: 0.35 });
    if (root.current) io.observe(root.current);
    return () => {
      reduce.removeEventListener("change", onChange);
      io.disconnect();
    };
  }, []);

  const go = useCallback(
    (n: number) => {
      const next = (n + SLIDES.length) % SLIDES.length;
      setI(next);
      if (still) {
        setTyped(SLIDES[next].question.length);
        setStage("done");
      } else {
        setTyped(0);
        setStage("typing");
      }
    },
    [still],
  );

  // Type the question, then show the tool calls, then the answer.
  useEffect(() => {
    if (still || stage !== "typing") return;
    const q = SLIDES[i].question;
    if (typed >= q.length) {
      const t = setTimeout(() => setStage("calls"), 380);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setTyped((n) => n + 1), 26 + (q.charCodeAt(typed) % 5) * 6);
    return () => clearTimeout(t);
  }, [typed, stage, i, still]);

  useEffect(() => {
    if (still || stage !== "calls") return;
    const t = setTimeout(() => setStage("done"), 900);
    return () => clearTimeout(t);
  }, [stage, still]);

  // Autoplay, paused on hover, focus, off screen, or with reduced motion.
  useEffect(() => {
    if (still || paused) return;
    const t = setInterval(() => {
      if (visible.current) go(i + 1);
    }, DURATION);
    return () => clearInterval(t);
  }, [i, paused, still, go]);

  const s = SLIDES[i];
  const shownCalls = stage === "typing" ? 0 : s.calls.length;

  return (
    <div
      ref={root}
      className={`show${paused || still ? " is-paused" : ""}`}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
    >
      <div className="show__card glass">
        <div className="show__side">
          <span className="show__n">{String(i + 1).padStart(2, "0")}</span>
          <div className="chips" role="group" aria-label="Pick an assistant">
            {SLIDES.map((sl, k) => (
              <button
                key={sl.app}
                type="button"
                className={`chip${k === i ? " is-on" : ""}`}
                aria-pressed={k === i}
                onClick={() => go(k)}
              >
                {sl.app}
              </button>
            ))}
          </div>
          <div className="show__status">
            <p className="show__app" aria-live="polite">
              {s.app} connected
            </p>
            <p className="show__how">{s.how}</p>
          </div>
        </div>

        <div className="show__panel" aria-label={`Example: asking ${s.app}`} role="group">
          <p className="show__label">{s.app}</p>
          <div className="prompt">
            <span className="prompt__plus" aria-hidden="true" />
            <p className="prompt__text">
              <span className="sr">{s.question}</span>
              <span aria-hidden="true">
                {s.question.slice(0, typed)}
                {stage === "typing" ? <span className="caret" /> : null}
              </span>
            </p>
            <span className="prompt__send" aria-hidden="true" />
          </div>
          <ol className="calls" aria-label="Tool calls">
            {s.calls.map((c, k) => (
              <li key={c.fn + k} className={k < shownCalls ? "is-in" : ""} style={{ ["--k" as string]: k }}>
                <span className="calls__ns">dotiphant</span>.{c.fn}
                <span className="calls__args">({c.args})</span>
              </li>
            ))}
          </ol>
          <p className={`show__result${stage === "done" ? " is-in" : ""}`}>{s.result}</p>
        </div>
      </div>

      <div className="show__nav">
        <div className="dots" aria-hidden="true">
          {SLIDES.map((sl, k) => (
            <span key={sl.app} className={`dots__d${k === i ? " is-on" : ""}`} style={{ ["--dur" as string]: `${DURATION}ms` }} />
          ))}
        </div>
        <div className="show__arrows">
          <button type="button" className="arrow" onClick={() => go(i - 1)} aria-label="Previous example">
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5" /></svg>
          </button>
          <button type="button" className="arrow" onClick={() => go(i + 1)} aria-label="Next example">
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 4.5 13 10l-5.5 5.5" /></svg>
          </button>
        </div>
      </div>
    </div>
  );
}
