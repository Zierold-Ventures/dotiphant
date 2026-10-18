const cols = ["Dotiphant", "Bot-based tools", "Cloud CRMs"];

const rows: { q: string; us: [string, string]; others: [string, string] }[] = [
  {
    q: "Sees your full history",
    us: ["Yes", ", every chat your account can read, except secret chats"],
    others: ["No, only messages after the bot is added", "Usually yes"],
  },
  {
    q: "Data stays on your computer",
    us: ["Yes", ", one SQLite file"],
    others: ["Usually no", "No, stored on their servers"],
  },
  {
    q: "Your login stays with you",
    us: ["Yes", ", in your OS keychain"],
    others: ["No login needed, so no history", "No, the session lives on their servers"],
  },
  {
    q: "Works with your AI assistant",
    us: ["Yes", ", over MCP"],
    others: ["Varies", "Some, through their own tools"],
  },
  { q: "Open source", us: ["Yes", ", MIT"], others: ["Usually not", "Usually not"] },
  {
    q: "Setup time",
    us: ["About 5 minutes", ""],
    others: ["Minutes, then add the bot to each group", "Minutes"],
  },
  { q: "Price", us: ["Free", ""], others: ["Often a subscription", "Usually per seat, per month"] },
];

export function Comparison() {
  return (
    <section id="compare" className="sec compare" aria-labelledby="compare-h">
      <div className="wrap">
        <header className="head">
          <h2 id="compare-h">How it compares.</h2>
          <p className="head__sub">
            The usual ways to get Telegram into an AI or a CRM, in general terms. Individual products vary.
          </p>
        </header>
        <div className="table glass" tabIndex={0} role="region" aria-labelledby="compare-h">
          <table>
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr">Question</span>
                </th>
                {cols.map((c, i) => (
                  <th scope="col" key={c} className={i === 0 ? "us" : undefined}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.q}>
                  <th scope="row">{r.q}</th>
                  <td className="us">
                    <b>{r.us[0]}</b>
                    {r.us[1]}
                  </td>
                  {r.others.map((o, i) => (
                    <td key={i}>{o}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="fine compare__hint">Scroll the table sideways to see every column.</p>
      </div>
    </section>
  );
}
