import Link from "next/link";
import { IndexPage, LinkRows } from "@/components/GuidePage";
import { INDEXES, SECTIONS, docs, guides } from "@/content/pages";
import { abs, breadcrumbs, graph, pageMetadata } from "@/content/seo";

const idx = INDEXES.guides;
export const metadata = pageMetadata({ path: idx.path, title: idx.title, description: idx.description, type: "website" });

const crumbs = [
  { name: "Home", path: "/" },
  { name: idx.title, path: idx.path },
];

const structured = graph(
  {
    "@type": "CollectionPage",
    "@id": `${abs(idx.path)}#page`,
    name: "Dotiphant guides",
    description: idx.description,
    url: abs(idx.path),
    dateModified: idx.updated,
    mainEntity: {
      "@type": "ItemList",
      itemListElement: guides().map((g, i) => ({ "@type": "ListItem", position: i + 1, url: abs(g.path), name: g.title })),
    },
  },
  breadcrumbs(crumbs),
);

export default function GuidesIndex() {
  return (
    <IndexPage
      title="Guides"
      crumbs={crumbs}
      structured={structured}
      lede={
        <p>
          Short, practical guides for Dotiphant, the open-source app that keeps a read-only archive of your Telegram
          chats on your computer and lets your AI assistant read it. Pick the app you use or the job you want done.
        </p>
      }
    >
      {SECTIONS.filter((s) => s.name !== "Docs").map((s) => (
        <section className="guide__sec" key={s.name} aria-labelledby={`sec-${s.name}`}>
          <h2 id={`sec-${s.name}`}>{s.name}</h2>
          <p className="head__sub">{s.blurb}</p>
          <div className="cards3">
            {guides()
              .filter((g) => g.section === s.name)
              .map((g, i) => (
                <Link className="scard glass gcard" href={g.path} key={g.path}>
                  <span className="scard__n">{String(i + 1).padStart(2, "0")}</span>
                  <h3>{g.title}</h3>
                  <p>{g.summary.replace(/`/g, "")}</p>
                </Link>
              ))}
          </div>
        </section>
      ))}
      <section className="guide__sec" aria-labelledby="sec-docs">
        <h2 id="sec-docs">Docs</h2>
        <p className="head__sub">Install, remote access and security, in full.</p>
        <LinkRows items={docs().map((d) => ({ href: d.path, label: d.title, note: d.summary }))} />
      </section>
    </IndexPage>
  );
}
