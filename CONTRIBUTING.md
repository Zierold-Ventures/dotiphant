# Contributing

Dotiphant is a small codebase on purpose. Read [docs/architecture.md](docs/architecture.md) first: it lists every module, the data directory and the safety rules.

## Setup

```sh
git clone https://github.com/Zierold-Ventures/dotiphant.git && cd dotiphant
uv sync
uv run pytest -q
pnpm install   # git hooks and commit message checks (Node and pnpm needed)
```

Try changes against made-up data, without a Telegram login:

```sh
DOTIPHANT_HOME=/tmp/dotiphant-dev uv run dotiphant demo
```

The demo archive goes next to the data folder, never inside it: here `/tmp/Dotiphant Demo`.

## Rules

- Dotiphant never writes to Telegram. Pull requests that send, edit, delete or mark messages as read will not be merged.
- Only `sync.py` and the importers add to the archive. Only `forget.py` deletes, on an explicit `dotiphant forget`. `demo.py` writes only its own demo folder.
- Tests never touch the network or a real archive. Use a temporary `DOTIPHANT_HOME`, `DOTIPHANT_SECRETS=file` and the fake client in `tests/fakes.py`.
- Logs and status files never contain message text, phone numbers or secrets.
- Keep dependencies few. The viewer has no build step and loads nothing from the internet.
- Docs use plain ASCII punctuation, short sentences and no emoji.

## Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/). The commit-msg hook and CI check them with commitlint (`commitlint.config.mjs`). Releases and the changelog are built from them, so the type matters (see `docs/releasing.md`).

```
type(scope): short summary in the imperative

Optional body: what changed and why.

Optional footers, such as Refs: #12 or BREAKING CHANGE: ...
```

- Types: `feat` (new for users), `fix` (bug fix), `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- Scopes are optional: `app`, `site`, `sync`, `mcp`, `cli`, `importer`, `oauth`, `docs`, `release`, `deps`, `ci`, `brand`. Another scope gives a warning, not an error.
- The first line is at most 100 characters.
- A breaking change gets a `!` after the type (`feat!: ...`) or a `BREAKING CHANGE:` footer.

Examples: `feat(importer): read WhatsApp exports with media`, `fix(sync): resume after a flood wait`, `docs: explain remote access`.

With squash merges, the PR title becomes the commit message, so write it the same way.

## Git hooks

`pnpm install` at the repo root installs [husky](https://typicode.github.io/husky/) hooks from `.husky/`. Each hook first runs your global hook of the same name, if `git config --global core.hooksPath` points to one, because husky's repo-local hooks path would hide it.

- commit-msg: runs commitlint on the message.
- pre-commit, on staged files only and in a few seconds:
  - blocks files that look like secrets (`*.session`, `.env`, anything under `secrets/`);
  - `uvx pyflakes src tests` when Python files are staged;
  - `shellcheck install.sh` when the installer is staged (skipped with a note if shellcheck is missing);
  - checks that `install.sh` and `site/public/install.sh` are identical;
  - rejects em and en dashes, curly quotes and the ellipsis character in Markdown under `docs/` and in `README.md`, `CONTRIBUTING.md` and `CHANGELOG.md`.
- pre-push: `uv run pytest -q`, `uvx pyflakes src tests`, `uv build` to a temp folder, and `pnpm --dir site build` when the push changes `site/`. It prints the time for each step.

To skip all hooks once, set `HUSKY=0`: `HUSKY=0 git commit ...` or `HUSKY=0 git push`. This also skips your global hooks for that command. CI still runs the tests and commitlint.
