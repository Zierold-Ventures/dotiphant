#!/bin/sh
# Dotiphant installer for macOS and Linux.
#
#   curl -LsSf https://dotiphant.com/install.sh | sh
#
# What it does:
#   1. Downloads the latest release wheel, its constraints.txt and SHA256SUMS
#      from GitHub, and stops unless both files match their checksums.
#   2. Installs uv (Astral's Python tool manager) if it is missing.
#   3. Installs the checked wheel with `uv tool install`, with dependencies
#      pinned to the versions the release was tested with, puts `dotiphant` on
#      your PATH, and runs `dotiphant stop`, so a viewer still running the
#      older version does not keep serving it.
#   4. Starts `dotiphant open` to continue setup in the browser.
#
# Dependencies come from PyPI. The installer ignores uv config files and uv
# index variables (UV_INDEX_URL, UV_DEFAULT_INDEX and the like), so a private
# index set up for other work cannot break or redirect this install.
#
# Settings:
#   DOTIPHANT_INDEX_URL=URL       get dependencies from this index instead of PyPI,
#                                 for networks that block PyPI
#
# Settings for testing:
#   DOTIPHANT_SOURCE=PATH_OR_URL  install from a folder, wheel or git URL, unchecked;
#                                 a folder with uv.lock gets its pinned versions
#   DOTIPHANT_VERSION=X.Y.Z       install that release instead of the latest
#   DOTIPHANT_RELEASE_BASE=URL    download vX.Y.Z/<file> from URL instead of GitHub
#                                 (testing only; needs DOTIPHANT_VERSION; may be http)
#   DOTIPHANT_NO_OPEN=1           print the next command instead of running it
#   DOTIPHANT_DRY_RUN_UV=1        if uv is missing, say what would happen and stop
#
# Everything runs from main, called on the last line, so a download that stops
# halfway runs nothing.
set -eu

REPO="Zierold-Ventures/dotiphant"
REPO_URL="https://github.com/$REPO"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"
PYPI_URL="https://pypi.org/simple"
tmp_dir=""

cleanup() { if [ -n "$tmp_dir" ]; then rm -f "$tmp_dir"/*; rmdir "$tmp_dir"; fi; }

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nError: %s\n' "$*" >&2; exit 1; }

# curl may use HTTPS only. DOTIPHANT_RELEASE_BASE may point at a local http
# server for testing, so it allows http too.
curl_proto() { if [ -n "${DOTIPHANT_RELEASE_BASE:-}" ]; then echo '=https,http'; else echo '=https'; fi; }

# download URL FILE
download() {
  if command -v curl >/dev/null 2>&1; then
    curl --proto "$(curl_proto)" --tlsv1.2 -fsSL "$1" -o "$2"
  else
    wget -q -O "$2" "$1"
  fi
}

# GitHub redirects /releases/latest to /releases/tag/<tag>. Print that tag.
latest_tag() {
  if command -v curl >/dev/null 2>&1; then
    url=$(curl --proto '=https' --tlsv1.2 -fsSLI -o /dev/null -w '%{url_effective}' "$REPO_URL/releases/latest") || return 1
  else
    url=$(wget -S --spider "$REPO_URL/releases/latest" 2>&1 | sed -n 's/^ *[Ll]ocation: *//p' | tail -n 1 | tr -d '\r')
  fi
  case "$url" in
    */releases/tag/v*) echo "${url##*/}" ;;
    *) return 1 ;;
  esac
}

# check_sum FILE: compare FILE in tmp_dir with its line in SHA256SUMS.
check_sum() {
  expected=$(awk -v f="$1" '$2 == f || $2 == "*" f { print $1 }' "$tmp_dir/SHA256SUMS")
  [ -n "$expected" ] || fail "SHA256SUMS has no line for $1. Nothing was installed."
  actual=$(sha256 "$tmp_dir/$1" | cut -d' ' -f1)
  [ "$actual" = "$expected" ] || fail "Checksum mismatch for $1. Nothing was installed.
SHA256SUMS says $expected, the download is $actual."
  echo "Checksum OK. SHA-256 of $1: $actual"
}

# uv_install PACKAGE [CONSTRAINTS]: install with dependencies from PyPI (or
# DOTIPHANT_INDEX_URL) only. --no-config skips uv.toml and pyproject.toml
# settings, and the subshell drops uv's index variables, so a private index
# from other work (a CodeArtifact or company index that answers 401 or 403)
# is never asked.
uv_install() {
  (
    unset UV_INDEX UV_INDEX_URL UV_DEFAULT_INDEX UV_EXTRA_INDEX_URL UV_FIND_LINKS \
      UV_NO_INDEX UV_INDEX_STRATEGY UV_CONFIG_FILE UV_CONSTRAINT UV_OVERRIDE
    if [ -n "${2:-}" ]; then
      uv tool install --upgrade --no-config --default-index "$index" --constraints "$2" "$1"
    else
      uv tool install --upgrade --no-config --default-index "$index" "$1"
    fi
  )
}

main() {
  trap cleanup EXIT
  trap 'exit 130' INT TERM

  say "Checking your system"
  case "$(uname -s)" in
    Darwin) echo "macOS found."; sha256() { shasum -a 256 "$1"; } ;;
    Linux) echo "Linux found."; sha256() { sha256sum "$1"; } ;;
    *) fail "This installer supports macOS and Linux only. On Windows, install uv
(https://docs.astral.sh/uv/) and run: uv tool install <URL of the .whl file at
$REPO_URL/releases/latest>" ;;
  esac

  command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
    fail "Neither curl nor wget is installed. Install one of them and run this again."

  index="${DOTIPHANT_INDEX_URL:-$PYPI_URL}"
  constraints=""
  tmp_dir=$(mktemp -d)
  if [ -n "${DOTIPHANT_SOURCE:-}" ]; then
    PACKAGE="$DOTIPHANT_SOURCE"
    echo "Source: $PACKAGE (DOTIPHANT_SOURCE is set, so no checksum check)"
  else
    base="${DOTIPHANT_RELEASE_BASE:-$REPO_URL/releases/download}"
    version="${DOTIPHANT_VERSION:-}"
    if [ -z "$version" ]; then
      [ -z "${DOTIPHANT_RELEASE_BASE:-}" ] || fail "DOTIPHANT_RELEASE_BASE needs DOTIPHANT_VERSION."
      say "Finding the latest Dotiphant release"
      tag=$(latest_tag) || fail "Could not find the latest release at $REPO_URL/releases.
Check your internet connection and try again."
      version="${tag#v}"
    fi
    wheel="dotiphant-$version-py3-none-any.whl"
    say "Downloading Dotiphant $version and its checksums from $base/v$version/"
    download "$base/v$version/$wheel" "$tmp_dir/$wheel" || fail "Could not download $wheel."
    download "$base/v$version/constraints.txt" "$tmp_dir/constraints.txt" ||
      fail "Could not download constraints.txt, the tested dependency versions. Nothing was installed."
    download "$base/v$version/SHA256SUMS" "$tmp_dir/SHA256SUMS" ||
      fail "Could not download SHA256SUMS, so the wheel cannot be checked. Nothing was installed."
    check_sum "$wheel"
    check_sum constraints.txt
    if [ -n "${DOTIPHANT_RELEASE_BASE:-}" ]; then
      echo "DOTIPHANT_RELEASE_BASE is set, so no attestation check."
    elif command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
      if gh attestation verify "$tmp_dir/$wheel" --repo "$REPO" >/dev/null 2>&1; then
        echo "Attestation OK: GitHub confirms that $REPO's release workflow built this file."
      else
        echo "Warning: gh found no valid build attestation for this file."
        echo "See $REPO_URL/blob/main/docs/security.md"
      fi
    else
      echo "To also check where it was built, install the GitHub CLI (gh), log in, and run:"
      echo "  gh attestation verify $wheel --repo $REPO"
    fi
    PACKAGE="$tmp_dir/$wheel"
    constraints="$tmp_dir/constraints.txt"
  fi

  # The uv installer puts uv here. A new shell finds it; this one may not yet.
  PATH="$HOME/.local/bin:$PATH"

  if command -v uv >/dev/null 2>&1; then
    echo "uv is installed: $(uv --version)"
  else
    say "Installing uv, which Dotiphant uses to install itself and its own Python"
    echo "Source: $UV_INSTALLER_URL (the official installer from Astral)"
    if [ "${DOTIPHANT_DRY_RUN_UV:-}" = "1" ]; then
      echo "Dry run: would download that script to a temp file, then run it with sh."
      echo "Dry run: would then run: uv tool install --upgrade $PACKAGE"
      exit 0
    fi
    download "$UV_INSTALLER_URL" "$tmp_dir/uv-install.sh"
    sh "$tmp_dir/uv-install.sh"
    command -v uv >/dev/null 2>&1 || fail "uv was installed but cannot be found. Open a new terminal and run this again."
  fi

  # A source folder with a lock file gets the same pins a release would.
  if [ -n "${DOTIPHANT_SOURCE:-}" ] && [ -f "$DOTIPHANT_SOURCE/uv.lock" ]; then
    uv export --quiet --frozen --no-dev --no-hashes --no-emit-project \
      --project "$DOTIPHANT_SOURCE" -o "$tmp_dir/constraints.txt" ||
      fail "Could not read the pinned versions from $DOTIPHANT_SOURCE/uv.lock."
    constraints="$tmp_dir/constraints.txt"
  fi

  say "Installing Dotiphant: uv tool install --upgrade $PACKAGE"
  if [ -n "$constraints" ]; then
    echo "Dependencies: the tested versions, from $index"
  else
    echo "Dependencies: the newest matching versions, from $index"
  fi
  uv_install "$PACKAGE" "$constraints" || fail "uv could not install Dotiphant. See uv's message above.
If it mentions 401, 403 or an index, your network may block $index.
Set DOTIPHANT_INDEX_URL to a package index you can reach and run this again."

  bin_dir=$(uv tool dir --bin)
  if ! command -v dotiphant >/dev/null 2>&1; then
    say "Adding $bin_dir to your PATH, so new terminals find the dotiphant command"
    # This fails if your shell config already has it; a new terminal fixes that.
    if ! uv tool update-shell; then
      echo "Open a new terminal to use dotiphant, or add $bin_dir to your PATH."
    fi
    PATH="$bin_dir:$PATH"
  fi

  # A viewer started before this update still runs the old code. Ask it to stop;
  # `dotiphant stop` never stops anything else, and its failure does not matter here.
  stop_out=$(dotiphant stop 2>&1) || true
  case "$stop_out" in
    *Stopped*)
      say "Closed the Dotiphant viewer that was running the older version."
      echo "'dotiphant open' starts the updated one." ;;
    *"cannot be stopped"*)
      say "A Dotiphant viewer from before this update is still running"
      echo "$stop_out" ;;
  esac

  say "Done. Dotiphant $(dotiphant --version | cut -d' ' -f2) is installed."
  echo "  Command:        $bin_dir/dotiphant"
  echo "  Program files:  $(uv tool dir)/dotiphant"
  echo "  Your archive:   created during setup; 'dotiphant status' shows the folder"
  echo "  Update:         run this installer again"
  echo "  Uninstall:      uv tool uninstall dotiphant (this keeps your archive folder)"

  # With `curl ... | sh` the script itself arrives on stdin, so check the
  # output and the terminal device instead.
  if [ "${DOTIPHANT_NO_OPEN:-}" != "1" ] && [ -t 1 ] && (: </dev/tty) 2>/dev/null; then
    say "Starting Dotiphant. Setup continues in your browser. Press Ctrl+C to stop."
    dotiphant open </dev/tty
  else
    say "Next, run this to set up Dotiphant in your browser:"
    echo "  dotiphant open"
  fi
}

main "$@"
