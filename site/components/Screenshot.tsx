import { asset } from "@/lib";

const alt =
  "The Dotiphant viewer in a browser: the Acme x Northwind integration group chat, with two messages about the launch date selected and a 'Copy as Markdown' button at the bottom.";

// The real product: the viewer as it runs on your computer, in a glass browser frame.
export function Screenshot() {
  return (
    <figure className="window glass">
      <div className="window__chrome" aria-hidden="true">
        <i />
        <i />
        <i />
        <span className="window__url">127.0.0.1:8765</span>
      </div>
      {/* Both versions are in the page; CSS shows the one for the current theme. */}
      <img className="only-light" src={asset("/img/chat.webp")} width={1040} height={800} loading="lazy" alt={alt} />
      <img className="only-dark" src={asset("/img/chat-dark.webp")} width={1040} height={800} loading="lazy" alt={alt} />
      <figcaption className="window__cap">
        The viewer runs on your computer and is reachable from it only. Pick messages and copy them as Markdown,
        search every chat, or let your assistant do the reading.
      </figcaption>
    </figure>
  );
}
