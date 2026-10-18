# Dotiphant website

The marketing site for Dotiphant. Next.js (App Router, TypeScript), exported as static HTML. No cookies and no advertising. Fonts are self-hosted with `next/font` and images live in `public/`. Visits are counted with Vercel Web Analytics, which is cookieless and disclosed on the privacy page.

## Develop

```sh
pnpm install
pnpm dev          # http://localhost:3000
```

If `pnpm install` reaches a private registry, the `.npmrc` in this folder points it to the public npm registry.

## Build

```sh
pnpm build        # static site in out/
npx serve out     # or: python3 -m http.server -d out
```

Build-time settings:

- `NEXT_PUBLIC_SITE_URL`: absolute URL of the site with a trailing slash, used for Open Graph links. Defaults to `https://dotiphant.com/`.
- `NEXT_PUBLIC_BASE_PATH`: leave empty for dotiphant.com. Set it only if the site is ever served from a sub-path.

## Deploy

The site deploys on Vercel (project `zierold-ventures/dotiphant`), from the GitHub repository:

- Root directory: `site`. Framework preset: Next.js. Build command and output: the defaults (`output: 'export'` makes Vercel serve the static files).
- Environment variables: `NEXT_PUBLIC_SITE_URL=https://dotiphant.com/`. Redeploy after changing them.
- Domains: add `dotiphant.com` and `www.dotiphant.com` in the project. At the registrar, set the `A` record and the `www` `CNAME` exactly as the Vercel domain card shows.
- `vercel.json` serves `install.sh` as plain text.

Every push to `main` that changes `site/` deploys to production. Pull requests get preview deployments.

## Structure

- `app/page.tsx` puts the sections together. Each section is a component in `components/`.
- `CopyButton`, `Faq`, `Rail`, `Reveal`, `Roller`, `ScrollHeader`, `Showcase`, `ThemeSwitch` and `WorksWith` in `components/` are client components. Everything else renders on the server at build time.
- `components/Wordmark.tsx` draws the placeholder wordmark: a lens mark (a ring with a dot) and the name. Replace it when the final logo is ready. `app/icon.svg` and `app/apple-icon.png` are the favicons, `public/img/og.png` is the Open Graph image.
- The hero (`components/Hero.tsx`) draws the same column of demo messages twice: blurred everywhere, sharp inside the glass lens. The messages are in `components/stream.ts` and come from `dotiphant demo`.
- `app/privacy` and `app/imprint` are the legal pages. The company name, address and contact email they show live in `content/company.ts`.
- `app/globals.css` holds all styles: a pale mesh of mist, sage and paper, frosted glass cards, a dark hero and footer, one clay accent, Host Grotesk and Fragment Mono, and a dark theme that follows the system or the theme switch. All motion sits in one `prefers-reduced-motion: no-preference` block.
- Screenshots in `public/img/` come from `docs/img/` in the main repository, cropped and converted to WebP.
