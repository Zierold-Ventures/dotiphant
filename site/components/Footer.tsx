import Link from "next/link";
import { Wordmark } from "./Wordmark";
import { GITHUB } from "@/lib";
import { ThemeSwitch } from "./ThemeSwitch";
import { guides } from "@/content/pages";

export function Footer() {
  return (
    <footer className="foot">
      <div className="foot__inner">
        <div className="foot__row">
          <div className="foot__about">
            <p className="foot__tag">Remembers your chats. Sends nothing.</p>
            <p>
              Your Telegram chats, on your own computer, readable by your AI assistant. macOS and Linux; Windows
              experimental.
            </p>
            <ThemeSwitch />
          </div>
          <nav className="foot__col" aria-labelledby="foot-guides">
            <p className="foot__h" id="foot-guides">Guides</p>
            <ul className="foot__list">
              {guides().map((g) => (
                <li key={g.path}><Link href={g.path}>{g.short}</Link></li>
              ))}
              <li><Link href="/guides/">All guides</Link></li>
            </ul>
          </nav>
          <nav className="foot__col" aria-labelledby="foot-project">
            <p className="foot__h" id="foot-project">Project</p>
            <ul className="foot__list">
            <li><Link href="/docs/install/">Install</Link></li>
            <li><Link href="/docs/remote-access/">Remote access</Link></li>
            <li><Link href="/docs/security/">Security</Link></li>
            <li><a href={GITHUB}>GitHub</a></li>
            <li><a href={`${GITHUB}/blob/main/LICENSE`}>MIT license</a></li>
            <li><a href="https://core.telegram.org/api/terms">Telegram API Terms</a></li>
            <li><Link href="/privacy/">Privacy policy</Link></li>
            <li><Link href="/imprint/">Imprint</Link></li>
            </ul>
          </nav>
        </div>
        <div className="foot__legal">
          <p>
            Dotiphant is an independent, unofficial project. Not affiliated with, endorsed by or supported by Telegram.
            Telegram is a trademark of its owner.
          </p>
          <p>
            Open-source software under the MIT license, provided as is, without warranty. You are responsible for using
            it within Telegram&apos;s terms and the data protection law that applies to you.
          </p>
        </div>
        <a className="foot__mark" href="/#top" aria-label="Dotiphant, back to top">
          <Wordmark />
        </a>
      </div>
    </footer>
  );
}
