import Link from "next/link";
import type { ReactNode } from "react";
import { Header } from "./Header";
import { Footer } from "./Footer";
import { JsonLd } from "./JsonLd";
import { Terminal } from "./Terminal";
import { VerifyNote } from "./Trust";
import { INDEXES, page, type PageInfo } from "@/content/pages";
import { article, breadcrumbs, graph } from "@/content/seo";
import { GITHUB, INSTALL, RELEASES } from "@/lib";

export type Crumb = { name: string; path: string };

export function crumbsFor(p: PageInfo): Crumb[] {
  const index = p.section === "Docs" ? INDEXES.docs : INDEXES.guides;
  return [
    { name: "Home", path: "/" },
    { name: index.title, path: index.path },
    { name: p.short, path: p.path },
  ];
}

export function Breadcrumbs({ items }: { items: Crumb[] }) {
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <ol>
        {items.map((c, i) => (
          <li key={c.path}>
            {i < items.length - 1 ? <Link href={c.path}>{c.name}</Link> : <span aria-current="page">{c.name}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

// A row link with a note and the round arrow, as in the FAQ side list.
export function LinkRows({ items }: { items: { href: string; label: string; note: string }[] }) {
  return (
    <ul className="faq__links rows">
      {items.map((l) => (
        <li key={l.href}>
          <Link href={l.href}>
            <span className="faq__link">{l.label}</span>
            <span className="faq__note">{l.note}</span>
            <span className="faq__arrow" aria-hidden="true">
              <svg viewBox="0 0 20 20"><path d="M6 14 14 6M8 6h6v6" /></svg>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function InstallCta() {
  return (
    <section className="cta glass" aria-labelledby="cta-h">
      <div className="cta__copy">
        <h2 id="cta-h">Install Dotiphant</h2>
        <p>
          Free and open source (MIT). macOS and Linux; Windows experimental. The command installs the latest{" "}
          <a href={RELEASES}>GitHub release</a> and opens the setup in your browser. It takes about five minutes.
        </p>
        <p className="fine">
          The script is about 150 lines. <a href={`${GITHUB}/blob/main/install.sh`}>Read it first</a>, or see{" "}
          <Link href="/docs/install/">other ways to install</Link>. <VerifyNote />
        </p>
      </div>
      <Terminal
        label="Install commands"
        copy={INSTALL}
        lines={[{ cmd: INSTALL.replace(" | ", "\u00a0|\u00a0") }, { cmd: "dotiphant open", note: "  # setup opens in your browser" }]}
      />
    </section>
  );
}

// The frame of every guide and docs page: breadcrumbs, the title, a direct answer, the body,
// related pages and the install call to action. JSON-LD (TechArticle and BreadcrumbList) comes along.
export function GuidePage({ info, answer, children }: { info: PageInfo; answer: ReactNode; children: ReactNode }) {
  const crumbs = crumbsFor(info);
  const related = info.related.map(page);
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main" className="guide">
        <article className="wrap guide__wrap">
          <Breadcrumbs items={crumbs} />
          <p className="eyebrow">{info.section}</p>
          <h1>{info.title}</h1>
          <div className="guide__answer">{answer}</div>
          <div className="guide__body glass prose">{children}</div>
          <InstallCta />
          <section className="guide__related" aria-labelledby="related-h">
            <h2 id="related-h">Related</h2>
            <LinkRows items={related.map((r) => ({ href: r.path, label: r.title, note: r.summary.replace(/`/g, "") }))} />
          </section>
          <p className="fine guide__meta">
            Updated <time dateTime={info.updated}>{info.updated}</time>. Something wrong or unclear?{" "}
            <a href={`${GITHUB}/issues`}>Open an issue</a>.
          </p>
        </article>
      </main>
      <Footer />
      <JsonLd data={graph(article(info), breadcrumbs(crumbs))} />
    </>
  );
}

// Shell for the /guides and /docs index pages.
export function IndexPage({
  title,
  lede,
  crumbs,
  children,
  structured,
}: {
  title: string;
  lede: ReactNode;
  crumbs: Crumb[];
  children: ReactNode;
  structured: object;
}) {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main" className="guide">
        <div className="wrap">
          <Breadcrumbs items={crumbs} />
          <h1 className="guide__h1--index">{title}</h1>
          <div className="guide__answer">{lede}</div>
          {children}
        </div>
      </main>
      <Footer />
      <JsonLd data={structured} />
    </>
  );
}

// A terminal block for a guide. Each string is one command; text after "  # " is a dimmed comment.
// A string that starts with "# " is a comment line on its own. The copy button copies the commands only.
export function Cmd({ lines, label = "Commands" }: { lines: string[]; label?: string }) {
  const parsed = lines.map((l) => {
    if (l.startsWith("# ")) return { note: l };
    const i = l.indexOf("  # ");
    return i === -1 ? { cmd: l } : { cmd: l.slice(0, i), note: l.slice(i) };
  });
  const copy = parsed.filter((l) => l.cmd !== undefined).map((l) => l.cmd).join("\n");
  return <Terminal label={label} copy={copy} lines={parsed} />;
}
