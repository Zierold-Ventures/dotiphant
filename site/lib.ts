export const BASE = process.env.NEXT_PUBLIC_BASE_PATH || "";
export const asset = (path: string) => `${BASE}${path}`;
export const GITHUB = "https://github.com/Zierold-Ventures/dotiphant";
// The canonical host. The apex dotiphant.com redirects here.
export const SITE_URL = "https://www.dotiphant.com";
// The install line is also in README.md, docs/ and the comment at the top of install.sh, so it keeps the apex host:
// the apex redirects to www and `curl -L` follows. public/install.sh is a copy of install.sh at the repo root.
export const INSTALL = "curl -fsSL https://dotiphant.com/install.sh | sh";
// Releases ship as GitHub release assets; install.sh installs the latest one.
export const RELEASES = `${GITHUB}/releases/latest`;
