# Releasing Dotiphant

How Dotiphant is versioned, built and published. Releases are automated with [release-please](https://github.com/googleapis/release-please) and `.github/workflows/release.yml`. Commit messages drive the version and the changelog, so they follow [Conventional Commits](https://www.conventionalcommits.org/) (see `CONTRIBUTING.md`).

## Versioning

Dotiphant uses [semantic versioning](https://semver.org) and stays on 0.x until a deliberate 1.0.0. release-please picks the next version from the commits since the last release:

| Commits since the last release | 0.x | 1.0 and later |
| --- | --- | --- |
| Breaking change (`feat!:` or a `BREAKING CHANGE:` footer) | minor: 0.2.0 to 0.3.0 | major |
| `feat:` | patch: 0.2.0 to 0.2.1 | minor |
| `fix:`, `perf:` and others | patch | patch |

This comes from `bump-minor-pre-major` and `bump-patch-for-minor-pre-major` in `release-please-config.json`. Commits of type `chore`, `ci`, `test` and `style` count, but stay out of the changelog.

The version lives in `pyproject.toml`. The code reads it at run time through `importlib.metadata` (see `src/dotiphant/__init__.py`). release-please also bumps the copies in `uv.lock`, both version fields in `server.json` and `site/package.json`, through `extra-files` in its config. `.release-please-manifest.json` records the last released version.

## Where releases go

| Version | GitHub release assets | TestPyPI | PyPI |
| --- | --- | --- | --- |
| 0.x | Yes. This is what users install | Yes, as a dry run of the publish step | No |
| 1.0 and later | Yes | No | Yes |

Users never install from TestPyPI. Mixing it with PyPI as an extra index invites dependency confusion, so no doc, page or script points at it.

### Release assets and attestations

Each GitHub release has four assets:

| Asset | What it is |
| --- | --- |
| `dotiphant-X.Y.Z-py3-none-any.whl` | The wheel. `install.sh` installs it |
| `dotiphant-X.Y.Z.tar.gz` | The sdist |
| `constraints.txt` | Every dependency pinned to its version in `uv.lock`, from `uv export --frozen --no-dev --no-hashes --no-emit-project` |
| `SHA256SUMS` | SHA-256 of the other three, one `HASH  FILENAME` line each, with bare file names |

`install.sh` downloads the wheel, `constraints.txt` and `SHA256SUMS` and refuses to install on a mismatch. It passes `constraints.txt` to `uv tool install --constraints`, so users get the dependency versions the tests ran with, not the newest ones. `constraints.txt` and `SHA256SUMS` are uploaded as their own artifact, `release-checksums`, and never sit in `dist/`, so the PyPI upload sees only the sdist and the wheel.

The `attest` job creates GitHub artifact attestations (SLSA build provenance, signed with Sigstore) for all four files with `actions/attest-build-provenance`. It is the only job with `attestations: write`, and it runs after `build`, so the build step never holds an OIDC token. Anyone can check a file with `gh attestation verify FILE --repo Zierold-Ventures/dotiphant`; see "Verify a download" in [security.md](security.md).

The attestation step has `continue-on-error`, so a failed attestation never blocks a release. The release then has checksums but no attestation.

PyPI and TestPyPI uploads carry their own PEP 740 attestations, made by `pypa/gh-action-pypi-publish`.

## How a release happens

1. Merge work into `main` with Conventional Commit messages.
2. On each push to `main`, the `release-please` job opens or updates a release PR titled like `chore(main): release 0.1.1`. It bumps the version files and adds a section to `CHANGELOG.md`. Edit the changelog text in that PR if it needs it.
3. Merge the release PR. release-please then tags `vX.Y.Z` and creates the GitHub release with the generated notes.
4. In the same workflow run:
   - `test` runs the full test workflow.
   - `build` checks that the tag, `pyproject.toml` and `server.json` agree, builds the sdist and wheel with uv, and uploads them as the artifact `python-package-distributions`. It writes `constraints.txt` from `uv.lock` and `SHA256SUMS` for all three, and uploads both as the artifact `release-checksums`.
   - `attest` checks the files against `SHA256SUMS` and creates build provenance attestations for all four.
   - `github-release` attaches the sdist, the wheel, `constraints.txt` and `SHA256SUMS` to the release. If `docs/release-notes/vX.Y.Z.md` exists, it replaces the generated notes.
   - `publish-testpypi` (0.x) or `publish-pypi` (1.0 and later) uploads the same files with trusted publishing. Uploads carry PEP 740 attestations.
   - `mcp-registry` publishes `server.json` after a PyPI upload, only if the repo variable `MCP_PUBLISH` is `true`.

All of this lives in one workflow on purpose. A tag that release-please creates with `GITHUB_TOKEN` does not start other workflows.

The release PR itself gets no CI checks, for the same reason: GitHub does not run workflows for PRs opened with `GITHUB_TOKEN`. The release run tests again before anything is built.

After a release, check it:

1. The GitHub release lists the sdist, the wheel, `constraints.txt` and `SHA256SUMS`, with the right notes. `gh attestation verify` passes for a downloaded wheel.
2. Smoke test the installer against the new release, in a throwaway setup (see "Install paths").
3. For 1.0 and later: the PyPI page shows the new version, and `uv tool install dotiphant==X.Y.Z` works in a throwaway tool dir.
4. Update over a running viewer in the throwaway setup: start `dotiphant open` with the previous release, run the installer, and check that it closes the older viewer and that `dotiphant open` serves the new version.

### Fallback: a tag pushed by hand

If release-please is stuck, release by hand. This path runs the same test, build and publish jobs.

1. On a clean `main`: `uv version X.Y.Z`, set both versions in `server.json`, and set `.release-please-manifest.json` to `X.Y.Z`. Commit as `chore(release): X.Y.Z`.
2. Write the notes as an annotated tag message, or as `docs/release-notes/vX.Y.Z.md`, which wins if both exist:

   ```sh
   git tag -a vX.Y.Z --cleanup=verbatim -F notes.md
   git push origin main vX.Y.Z
   ```

   `--cleanup=verbatim` keeps Markdown headings. The build job refuses a tag that does not match `pyproject.toml` or `server.json`.

release-please is the primary path. Updating the manifest in step 1 keeps it in step for the next release.

### The first release, 0.1.0

The manifest starts at `0.0.0`, and `initial-version` in the config makes the first release 0.1.0. The setting is ignored once v0.1.0 exists.

`CHANGELOG.md` is seeded with the hand-written 0.1.0 notes from `docs/release-notes/v0.1.0.md`, and `skip-changelog: true` keeps release-please from adding a second 0.1.0 section. The GitHub release gets the same notes through the release-notes file.

After v0.1.0 is out, delete `"skip-changelog": true` from `release-please-config.json` in a `chore(release): ...` commit. From then on, release-please writes the changelog.

### Cutting 1.0.0

1.0.0 never happens by accident: no commit type moves 0.x to 1.0. When commands, MCP tools and the archive format are stable:

1. Push a commit with the footer `Release-As: 1.0.0`:

   ```sh
   git commit --allow-empty -m "chore: release 1.0.0" -m "Release-As: 1.0.0"
   ```

   Or set `"release-as": "1.0.0"` for `"."` in `release-please-config.json`, and remove it again after the release.
2. Merge the release PR. The run publishes to PyPI (approve the `pypi` environment if it asks) and attaches the files to the GitHub release.
3. Before the 1.0.0 release PR is merged, change the image paths in `README.md` (`docs/img/...`) to absolute `https://raw.githubusercontent.com/Zierold-Ventures/dotiphant/main/docs/img/...` URLs. PyPI shows the README as the project description and cannot resolve relative paths. Links in the README are already absolute.
4. Once 1.0.0 is on PyPI, switch the install paths to it: `install.sh` and `site/public/install.sh` (`uv tool install --upgrade dotiphant`), the README, `docs/install-with-ai.md`, and the install text in `site/lib.ts` and its components. Then consider the Homebrew tap and the MCP registry.

`Release-As: X.Y.Z` works the same way for any other version you want to force.

### Hotfix

In 0.x there is one line of development. Fix on `main` with a `fix:` commit, then merge the release PR it creates. Anything else unreleased on `main` ships with it. If that is not ready, revert it on `main` first, or hide it behind a flag.

### Yank or delete a 0.x release

A 0.x release lives only on GitHub (and TestPyPI, which no one installs from).

- To stop new installs of a bad release, ship a fixed patch version. `install.sh` always takes the latest release.
- If it must go now, mark the release as a pre-release (`gh release edit vX.Y.Z --prerelease`). `/releases/latest` then points at the previous release. Or delete it with `gh release delete vX.Y.Z`, which keeps the tag.
- Do not reuse a deleted version. Ship the next patch version, so the version history stays honest.
- From 1.0, PyPI versions can never be reused. Yank a harmful version on PyPI and ship the next patch.

## What CI checks

`.github/workflows/test.yml` runs on every push to `main` and every pull request, and again as part of each release:

- The test suite with `DOTIPHANT_SECRETS=file`: Python 3.12, 3.13 and 3.14 on Ubuntu, Python 3.14 on macOS. Pull requests run only 3.12 and 3.14 on Ubuntu, pushes to main add 3.13, and only release runs include macOS. Changes that touch only `docs/`, `site/` or Markdown files skip this workflow.
- The test suite with `DOTIPHANT_SECRETS=file`: Python 3.12, 3.13 and 3.14 on Ubuntu, Python 3.14 on macOS.
- `uv build`, then a check that the wheel contains the viewer templates, static files, `py.typed` and the `dotiphant` and `phant` entry points, then a clean install of the wheel and `--version` for both commands.

`.github/workflows/commitlint.yml` checks commit messages. On a pull request it checks the PR's own commits. On `main` it checks the commits since the previous tip, or only the pushed commit on a first push.

Locally, husky hooks run the same kind of checks before each commit and push; see `CONTRIBUTING.md`.

Dependabot opens grouped weekly updates for Python dependencies (`uv.lock`) and for GitHub Actions, with `build(deps)` and `ci(deps)` titles.

## Package name

Names: distribution and import package `dotiphant`, commands `dotiphant` and `phant`, repo `Zierold-Ventures/dotiphant`, MCP server `io.github.Zierold-Ventures/dotiphant`.

Check the entry points from a built wheel: `uvx --from dist/dotiphant-X.Y.Z-py3-none-any.whl dotiphant --version`.

## Install paths

Until 1.0, every user-facing install path takes the wheel from the latest GitHub release. Dependencies come from PyPI as usual.

| Path | Command | Notes |
| --- | --- | --- |
| Installer script | `curl -fsSL https://dotiphant.com/install.sh \| sh` | Primary path on the site and in the README |
| Release wheel | Download the wheel, `constraints.txt` and `SHA256SUMS`, check, then `uv tool install --constraints constraints.txt ./<wheel>` | What `docs/install-with-ai.md` tells an AI assistant to run |
| Local clone | `uv tool install .` | For development |

From 1.0, `uv tool install dotiphant`, `uvx dotiphant` and `pipx install dotiphant` work too.

The installer is `install.sh` at the repo root. A copy at `site/public/install.sh` is served by the site at `/install.sh`. Keep the two files identical; the pre-commit hook checks it. The script:

1. Installs uv with Astral's official installer if needed (downloaded to a temp file, then run).
2. Finds the latest release tag from the redirect of `https://github.com/Zierold-Ventures/dotiphant/releases/latest`.
3. Downloads that release's wheel, `constraints.txt` and `SHA256SUMS` to a temp folder and checks the SHA-256 of the wheel and `constraints.txt` (`shasum -a 256` on macOS, `sha256sum` on Linux). It stops on a mismatch or a missing file.
4. Runs `gh attestation verify` if `gh` is installed and logged in, and reports the result without failing on it.
5. Runs `uv tool install --upgrade --constraints constraints.txt` on the checked wheel, and `uv tool update-shell` if `dotiphant` is not on PATH.
6. Runs `dotiphant stop` (ignoring failures) so no viewer keeps serving the old code from memory, and says if it stopped one.
7. Starts `dotiphant open` when attached to a terminal.

The whole script runs from a `main` function called on the last line, so a cut-off download runs nothing. All curl calls use `--proto '=https' --tlsv1.2` (with `DOTIPHANT_RELEASE_BASE` set, http too, for local tests). The uv install passes `--no-config` and `--default-index https://pypi.org/simple` and unsets uv's index variables (`UV_INDEX_URL`, `UV_DEFAULT_INDEX`, `UV_INDEX` and the like), so a private index from the user's uv config cannot fail it with 401 or 403. `DOTIPHANT_INDEX_URL=URL` replaces PyPI for networks that block it.

To update, users run it again; `uv tool upgrade` does not move to a newer release URL. Anyone who updates with uv directly should run `dotiphant stop` afterwards (`uv tool upgrade dotiphant && dotiphant stop` from 1.0).

The site domain is `dotiphant.com`. It appears in `SITE_URL` in `site/lib.ts`, in the README install line, in the comment at the top of `install.sh`, and in the table above.

Test the installer without touching your own setup:

```sh
sb=$(mktemp -d)
HOME=$sb/home UV_TOOL_DIR=$sb/tools UV_TOOL_BIN_DIR=$sb/bin DOTIPHANT_HOME=$sb/data \
  DOTIPHANT_SECRETS=file BROWSER=true DOTIPHANT_NO_OPEN=1 DOTIPHANT_SOURCE=$PWD sh install.sh
```

`DOTIPHANT_SOURCE` installs from a folder, wheel or git URL instead of the latest release, without a checksum check. For a folder with `uv.lock`, the installer exports the same constraints as a release. `DOTIPHANT_VERSION=X.Y.Z` installs that release. `DOTIPHANT_RELEASE_BASE=URL` is for testing only: it downloads `vX.Y.Z/<file>` from that URL instead of the GitHub release, needs `DOTIPHANT_VERSION`, and skips the attestation check. `DOTIPHANT_NO_OPEN=1` prints `dotiphant open` instead of running it. `DOTIPHANT_DRY_RUN_UV=1` stops before downloading uv and says what would happen; combine it with a `PATH` that has no uv to test that branch. Lint with `uvx --from shellcheck-py shellcheck -s sh install.sh`.

Test the checksum path with a fake release served from your machine:

```sh
uv build --wheel -o "$sb/rel/v0.1.0"
uv export --frozen --no-dev --no-hashes --no-emit-project -o "$sb/rel/v0.1.0/constraints.txt"
(cd "$sb/rel/v0.1.0" && sha256sum -- *.whl constraints.txt > SHA256SUMS)
python3 -m http.server 9930 --bind 127.0.0.1 --directory "$sb/rel" &
HOME=$sb/home UV_TOOL_DIR=$sb/tools UV_TOOL_BIN_DIR=$sb/bin DOTIPHANT_HOME=$sb/data \
  DOTIPHANT_SECRETS=file DOTIPHANT_NO_OPEN=1 DOTIPHANT_VERSION=0.1.0 \
  DOTIPHANT_RELEASE_BASE=http://127.0.0.1:9930 sh install.sh
```

Use the version from `uv version --short`. Append a byte to the wheel (`printf x >> FILE`) and run it again: the installer must stop with "Checksum mismatch" and install nothing.

## Homebrew

Recommended install path: the installer script. uv brings its own Python and needs no extra maintenance per release. Homebrew users can get uv with `brew install uv`.

A Homebrew formula is drafted in `packaging/homebrew/dotiphant.rb`. It installs into a virtualenv with every dependency as a pinned resource, which is the Homebrew way but adds work to each release. The formula installs from PyPI, so it waits for 1.0. Steps after the first PyPI release:

1. Create the repo `zierold-ventures/homebrew-tap` and copy the formula to `Formula/dotiphant.rb`.
2. Set `url` to the sdist of the release on PyPI and fill in its `sha256`. release-please does not update the formula.
3. Generate the resource blocks: `brew update-python-resources Formula/dotiphant.rb`. Drop dev-only and Windows-only packages.
4. Check it: `brew install --build-from-source ./Formula/dotiphant.rb && brew test dotiphant && brew audit --strict dotiphant`.
5. Users then run `brew install zierold-ventures/tap/dotiphant`.

On each release, update `url`, `sha256` and resources the same way.

## One-time setup

Do these once, before the first release. The owner of the `Zierold-Ventures` org does them.

1. Settings > Actions > General > Workflow permissions: tick "Allow GitHub Actions to create and approve pull requests". release-please needs it to open the release PR. Keep the default token read-only; the workflow asks for the write scopes it needs per job.
2. Settings > Environments: create `testpypi` and `pypi`. Limit both to the `main` branch and tags matching `v*`.
3. Trusted publishers. No API token is needed; the first successful upload creates the project. Enable 2FA on both accounts first.
   - On test.pypi.org (Account settings > Publishing > Add a new pending publisher): project `dotiphant`, owner `Zierold-Ventures`, repository `dotiphant`, workflow `release.yml`, environment `testpypi`.
   - On pypi.org, the same fields with environment `pypi`. It is first used at 1.0.0.
4. Add yourself as a required reviewer on the `pypi` environment, so each PyPI upload waits for approval.
5. Security: enable private vulnerability reporting, so the link in `SECURITY.md` and the issue form works. Enable Dependabot alerts and security updates.
6. Branches: protect `main`; require the `test` and `commitlint` checks to pass before merging. With squash merges, the PR title becomes the commit message, so it must follow Conventional Commits too.
7. Optional: set the repo variable `MCP_PUBLISH` to `true` to publish to the MCP registry from 1.0 (see below).

## MCP directories

Files for the directory listings live at the repo root: `server.json` (official MCP registry), `glama.json` (Glama maintainers), `Dockerfile` and `.dockerignore` (a container that starts `dotiphant mcp` with an empty archive, for Glama's checks). Paste-ready copy for each directory is in [listing.md](listing.md).

### Package identifier

`server.json` uses `dotiphant` as the PyPI identifier. Registry clients start a PyPI server with `uvx <identifier> <packageArguments>`, here `uvx dotiphant mcp`. That works because the package installs a `dotiphant` command.

### Official MCP registry

The server name is `io.github.Zierold-Ventures/dotiphant`. The registry matches the namespace against the GitHub login exactly, including case, and grants an org namespace only to org Owners. The person who publishes must be an Owner of the `Zierold-Ventures` org.

Ownership of the PyPI package is proved by the line `<!-- mcp-name: io.github.Zierold-Ventures/dotiphant -->` at the end of `README.md`. PyPI keeps HTML comments in the project description, and the registry looks for that token in the description of the exact version being published. Keep the line in the README; a release without it cannot be published to the registry.

`server.json` points at the PyPI package, so it can be published only from 1.0. release-please bumps both version fields in it. The `mcp-registry` job in `release.yml` publishes it after the PyPI upload, logged in with GitHub OIDC, when the repo variable `MCP_PUBLISH` is `true`. It is off by default.

To publish by hand instead:

1. Check that `version` and `packages[0].version` in `server.json` match a version on PyPI. A registry version cannot be changed or reused once published.
2. Validate the file:

   ```sh
   UV_DEFAULT_INDEX=https://pypi.org/simple uvx check-jsonschema \
     --schemafile https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json server.json
   ```

3. Install `mcp-publisher` (`brew install mcp-publisher`, or a binary from the registry's GitHub releases).
4. `mcp-publisher login github`, then `mcp-publisher publish` from the repo root.
5. Check the entry: `curl "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.Zierold-Ventures/dotiphant"`.

When the schema date in `$schema` changes, read the registry changelog before updating it.

### Glama

Glama ingests the official registry, builds each server in a sandbox, and lists it only if the server starts and answers `initialize` and `tools/list`. The awesome-mcp-servers list requires a passing Glama listing and its score badge.

1. Find or add the server at https://glama.ai/mcp/servers and claim it. `glama.json` names the maintainers by GitHub username.
2. If Glama asks for build settings, use the repo `Dockerfile`. It installs from the source tree with `uv sync --locked`, sets `DOTIPHANT_SECRETS=file` and `DOTIPHANT_HOME=/data`, and runs `dotiphant mcp`. No credentials or network access are needed to start.
3. Check the image locally before relying on it:

   ```sh
   docker build -t dotiphant-mcp .
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"0"}}}' \
     | docker run -i --rm --network none dotiphant-mcp
   ```

   The reply names the server `dotiphant`. A full check with the `mcp` client (initialize, then `tools/list`) returns the six tools.

### Smithery

Smithery publishes local stdio servers as an MCPB bundle (`smithery mcp publish ./dotiphant.mcpb -n <namespace>/dotiphant`). Dotiphant has no MCPB bundle yet, so it is not on Smithery. Building one is a separate task.

### Other directories

- PulseMCP reads the official registry. Nothing to submit.
- mcp.so takes submissions on its site. Use the copy in `listing.md`.
- awesome-mcp-servers (punkpeye): open a PR by hand once the Glama listing passes. The exact line is in `listing.md`.

## Website hosting

The site in `site/` is a static Next.js export, served at `https://dotiphant.com`. It also serves the installer at `/install.sh`. It is hosted on Vercel (project `zierold-ventures/dotiphant`), which deploys every push to `main`.

`site/vercel.json` sets the build commands and serves `/install.sh` as `text/plain; charset=utf-8`.

1. Import the repo in Vercel. Root Directory: `site`. Framework: Next.js.
2. Environment variables (Production): `NEXT_PUBLIC_SITE_URL=https://dotiphant.com/`. Leave `NEXT_PUBLIC_BASE_PATH` unset.
3. Settings > Domains: add `dotiphant.com` and `www.dotiphant.com`. The apex redirects to `www`.
4. DNS at the registrar: an `A` record for `@` and a `CNAME` for `www`, with the exact values from the Vercel domain card. The usual A value is `76.76.21.21`, but newer projects can get a different IP and a project-specific CNAME target. Remove any parking records first.

