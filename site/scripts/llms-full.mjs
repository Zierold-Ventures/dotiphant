// Builds out/llms-full.txt after `next build`: the llms.txt map followed by the text of every
// guide and docs page, taken from the exported HTML so it always matches what the site shows.
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const OUT = "out";
const SITE = "https://www.dotiphant.com";

// The inner HTML of the first element with this class, following nested elements of the same tag.
function inner(html, className) {
  const open = html.search(new RegExp(`<(\\w+)[^>]*class="[^"]*\\b${className}\\b[^"]*"[^>]*>`));
  if (open === -1) return "";
  const tag = html.slice(open + 1).match(/^\w+/)[0];
  const start = html.indexOf(">", open) + 1;
  const re = new RegExp(`<${tag}[\\s>]|</${tag}>`, "g");
  re.lastIndex = start;
  let depth = 1;
  for (let m; (m = re.exec(html)); ) {
    depth += m[0].startsWith("</") ? -1 : 1;
    if (depth === 0) return html.slice(start, m.index);
  }
  return html.slice(start);
}

const decode = (s) =>
  s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#x27;|&#39;|&apos;/g, "'")
    .replace(/&nbsp;|\u00a0/g, " ")
    .replace(/&amp;/g, "&");

const strip = (s) => decode(s.replace(/<[^>]+>/g, ""));

function terminal(pre) {
  const text = strip(
    pre
      .replace(/<span class="(term__line|term__out|term__note is-line)"[^>]*>/g, "\n")
      .replace(/<span class="p"[^>]*>\$ <\/span>/g, "$ "),
  );
  return "\n\n```sh\n" + text.trim() + "\n```\n\n";
}

// A Markdown table: one line per row, a separator after the header row.
function table(html) {
  const rows = [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/g)].map((r) =>
    [...r[1].matchAll(/<t[hd][^>]*>([\s\S]*?)<\/t[hd]>/g)].map((c) => strip(c[1].replace(/<code[^>]*>([\s\S]*?)<\/code>/g, "`$1`")).replace(/\s+/g, " ").trim()),
  );
  if (!rows.length) return "";
  const line = (cells) => `| ${cells.join(" | ")} |`;
  return [line(rows[0]), line(rows[0].map(() => "---")), ...rows.slice(1).map(line)].join("\n");
}

function toText(html) {
  let s = html
    .replace(/<button[\s\S]*?<\/button>/g, "")
    .replace(/<div class="term__bar"[\s\S]*?<\/div>/g, "")
    .replace(/<pre[^>]*>([\s\S]*?)<\/pre>/g, (_, pre) => `@@PRE${Buffer.from(terminal(pre)).toString("base64")}@@`)
    .replace(/<code[^>]*>([\s\S]*?)<\/code>/g, "`$1`")
    .replace(/<a [^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g, (_, href, text) => {
      const url = href.startsWith("/") ? SITE + href : href;
      return `[${text}](${url})`;
    })
    .replace(/<h2[^>]*>/g, "\n\n## ")
    .replace(/<h3[^>]*>/g, "\n\n### ")
    .replace(/<table[^>]*>([\s\S]*?)<\/table>/g, (_, t) => "\n\n" + table(t) + "\n\n")
    .replace(/<li[^>]*>/g, "\n- ")
    .replace(/<\/(p|ul|ol|h2|h3|table|div)>/g, "\n\n")
    .replace(/<p[^>]*>/g, "\n\n");
  s = strip(s)
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n");
  return s.replace(/@@PRE([A-Za-z0-9+/=]+)@@/g, (_, b) => Buffer.from(b, "base64").toString()).replace(/\n{3,}/g, "\n\n").trim();
}

const sitemap = readFileSync(join(OUT, "sitemap.xml"), "utf8");
const paths = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)]
  .map((m) => new URL(m[1]).pathname)
  .filter((p) => /^\/(guides|docs)\/[^/]+\/$/.test(p));

const parts = [readFileSync(join(OUT, "llms.txt"), "utf8").trim()];
for (const path of paths) {
  const html = readFileSync(join(OUT, path, "index.html"), "utf8");
  const title = strip(html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/)[1]);
  const body = toText(inner(html, "guide__answer")) + "\n\n" + toText(inner(html, "guide__body"));
  parts.push(`# ${title}\n\nSource: ${SITE}${path}\n\n${body}`);
}

const text = parts.join("\n\n---\n\n") + "\n";
const bad = text.match(/[^\x00-\x7f]/g);
if (bad) console.warn(`llms-full.txt: non-ASCII characters: ${[...new Set(bad)].join(" ")}`);
writeFileSync(join(OUT, "llms-full.txt"), text);
console.log(`llms-full.txt: ${paths.length} pages, ${text.length} characters`);
