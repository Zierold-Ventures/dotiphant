import Link from "next/link";
import { IndexPage, LinkRows } from "@/components/GuidePage";
import { INDEXES, docs } from "@/content/pages";
import { abs, breadcrumbs, graph, pageMetadata } from "@/content/seo";
import { GITHUB } from "@/lib";

const idx = INDEXES.docs;
export const metadata = pageMetadata({ path: idx.path, title: idx.title, description: idx.description, type: "website" });

const crumbs = [
  { name: "Home", path: "/" },
  { name: idx.title, path: idx.path },
];

const structured = graph(
  {
    "@type": "CollectionPage",
    "@id": `${abs(idx.path)}#page`,
    name: "Dotiphant docs",
    description: idx.description,
    url: abs(idx.path),
    dateModified: idx.updated,
    mainEntity: {
      "@type": "ItemList",
      itemListElement: docs().map((d, i) => ({ "@type": "ListItem", position: i + 1, url: abs(d.path), name: d.title })),
    },
  },
  breadcrumbs(crumbs),
);

export default function DocsIndex() {
  return (
    <IndexPage
      title="Docs"
      crumbs={crumbs}
      structured={structured}
      lede={
        <>
          <p>
            How to install Dotiphant, how to reach it from ChatGPT and claude.ai, and what it stores and who can reach
            it. The same docs live in the <a href={`${GITHUB}/tree/main/docs`}>repository on GitHub</a>.
          </p>
          <p>
            Looking for a walkthrough instead? See the <Link href="/guides/">guides</Link>.
          </p>
        </>
      }
    >
      <section className="guide__sec" aria-label="All docs">
        <LinkRows items={docs().map((d) => ({ href: d.path, label: d.title, note: d.summary }))} />
      </section>
    </IndexPage>
  );
}
