import { Showcase } from "./Showcase";
import { Screenshot } from "./Screenshot";

export function Ask() {
  return (
    <section id="ask" className="sec ask" aria-labelledby="ask-h">
      <div className="wrap">
        <header className="head head--row">
          <h2 id="ask-h">
            Ask once.
            <br />
            It answers from your chats.
          </h2>
          <p className="head__sub">
            Your assistant searches the archive, reads the messages around each hit, and answers with the chat, the
            sender, the date and the message id, so you can check.
          </p>
        </header>
        <Showcase />
        <p className="fine show__cap">
          Illustration with made-up chats from <code>dotiphant demo</code>. Tool names are Dotiphant&apos;s real ones.
        </p>
        <Screenshot />
      </div>
    </section>
  );
}
