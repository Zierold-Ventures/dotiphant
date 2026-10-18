import Link from "next/link";
import { Wordmark } from "./Wordmark";
import { ScrollHeader } from "./ScrollHeader";
import { GITHUB } from "@/lib";

export function Header({ overHero = false }: { overHero?: boolean }) {
  return (
    <ScrollHeader overHero={overHero}>
      <a className="top__brand" href="/#top" aria-label="Dotiphant, home">
        <Wordmark />
      </a>
      {/* Plain anchors: next/link swallows same-page #section links, so they would not scroll. */}
      <nav className="top__nav" aria-label="Sections">
        <a className="pill" href="/#ask">Ask</a>
        <a className="pill" href="/#how">How it works</a>
        <a className="pill" href="/#privacy">Privacy</a>
        <a className="pill" href="/#faq">FAQ</a>
        <Link className="pill" href="/guides/">Guides</Link>
        <a className="pill pill--light" href={GITHUB}>
          GitHub
        </a>
      </nav>
    </ScrollHeader>
  );
}
