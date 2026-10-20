# Install Dotiphant with an AI assistant

Dotiphant keeps a private copy of your Telegram chats on your computer and lets your AI assistant read it. If you don't want to use a terminal yourself, an AI assistant that can run commands on your computer (Claude Code, Codex, Cursor, or Claude Desktop with a terminal tool) can install it with you.

Copy this prompt into your assistant:

```
Please install Dotiphant for me, following the instructions at
https://github.com/Zierold-Ventures/dotiphant/blob/main/docs/install-with-ai.md
Explain each step in plain words before you run it, and tell me exactly
what to do whenever you need something from me.
```

If your assistant cannot open web links, paste this whole page instead.

You will need your phone with Telegram on it. The setup takes about 5 minutes.

---

## Instructions for the AI assistant

You are installing Dotiphant, an open-source command line tool that archives the user's Telegram chats into a local SQLite file and serves them to AI apps over MCP. Run commands in the user's terminal; the user does the setup itself in their browser. The user may not be technical: explain what each step does in one plain sentence, and never ask them to edit files by hand.

Rules:

- Never ask the user to paste their Telegram API hash, login code or two-step password into this chat. They type those into the setup page in their browser.
- Do not run `dotiphant logout`, delete files in the Dotiphant data folder, or change anything in their Telegram account.
- Install from the latest GitHub release with `uv tool install`, after checking the download against its checksum, as below. Do not pipe the website's `install.sh` into a shell: you can run each step yourself and explain it, which is clearer for the user.
- Dotiphant is not on PyPI. Do not run `uv tool install dotiphant`, `uvx dotiphant` or `pipx install dotiphant`, and never add another package index.

### 1. Check Python and uv

Run `uv --version`.

If uv is missing, install it with the official instructions at https://docs.astral.sh/uv/getting-started/installation/ (on macOS and Linux this is a single `curl` command shown on that page; on Windows a PowerShell command). Then open a new shell or follow the installer's note about PATH, and check `uv --version` again.

uv brings its own Python, so a system Python is not required.

### 2. Install Dotiphant

Find the latest release. This prints a link that ends in the version tag, such as `.../releases/tag/v0.1.0`:

```sh
curl -fsSLI -o /dev/null -w '%{url_effective}\n' https://github.com/Zierold-Ventures/dotiphant/releases/latest
```

Download the wheel, its pinned dependency versions (`constraints.txt`) and the checksum file from that release into a new temp folder, check both files, then install it. Replace every `0.1.0` with the version you found:

```sh
cd "$(mktemp -d)"
base=https://github.com/Zierold-Ventures/dotiphant/releases/download/v0.1.0
curl --proto '=https' --tlsv1.2 -fsSL -O "$base/dotiphant-0.1.0-py3-none-any.whl" -O "$base/constraints.txt" -O "$base/SHA256SUMS"
shasum -a 256 -c --ignore-missing SHA256SUMS   # on Linux: sha256sum -c --ignore-missing SHA256SUMS
uv tool install --default-index https://pypi.org/simple --constraints constraints.txt ./dotiphant-0.1.0-py3-none-any.whl
```

The check must print `OK` for both `dotiphant-0.1.0-py3-none-any.whl` and `constraints.txt`. If it does not, stop, do not install, and tell the user. If the GitHub CLI is logged in, `gh attestation verify dotiphant-0.1.0-py3-none-any.whl --repo Zierold-Ventures/dotiphant` also checks that the project's release workflow built the file. See "Verify a download" in [security.md](security.md).

Dependencies come from PyPI, at the versions in `constraints.txt`, the ones the release was tested with. From a local clone of the repository, run `uv tool install .` in that folder instead.

Check with `dotiphant --version`. If the command is not found, run `uv tool update-shell` and open a new shell. To update later, repeat this step with the newer version.

### 3. Run the setup in the browser

Run:

```sh
dotiphant open
```

It starts the viewer on this computer and opens the browser at the setup page. The command keeps running, because it serves the page: start it in the background if your tool waits for commands to finish, and leave it running until the user is done. If no browser opens (for example over SSH), run `dotiphant open --no-browser` and give the user the printed link. That link is private: it unlocks the archive, so do not post it anywhere else.

Do not run the terminal wizard `dotiphant setup` from a command tool that cannot type into a running program. The browser setup is made for this case.

Then guide the user through the five steps on the page. They type everything themselves, in the browser:

1. API key. Telegram requires every app to have an API id and hash, and the user creates their own:
   - The page links to https://my.telegram.org. Enter the phone number in international format, like +49 151 12345678. Telegram sends a code to the Telegram app (not by SMS).
   - Click "API development tools" and fill in the form. App title and short name can be anything, for example "Dotiphant" and "dotiphant" (the short name takes 5 to 32 letters or digits). Platform: Desktop. URL and description can stay empty. Click "Create application".
   - If the site only shows "ERROR", try again later, without a VPN, or in another browser.
   - Copy "App api_id" and "App api_hash" into the two fields on the setup page. If saved values are wrong, the page offers "Replace them".
2. Log in. The page shows a QR code. On the phone: Telegram > Settings > Devices > Link Desktop Device, then point the camera at the code. If the account has a two-step verification password, the page asks for it. "Log in with your phone number instead" works too.
3. What to archive. Suggest the defaults (people and groups, last 365 days) unless the user wants more.
4. First sync. From a minute to an hour for large accounts. The user can continue while it runs.
5. Connect your AI. The page lists the AI apps it found, with a Connect button each, and a switch for the background refresh. Recommend turning the refresh on.

If the page says Telegram did not accept the API id and hash, Dotiphant has already removed them. The user copies both again from my.telegram.org into step 1 and logs in again.

To show the user what Dotiphant looks like before logging in, run `dotiphant demo`. It opens the viewer on made-up chats, stored apart from the real archive.

### 4. Connect the user's AI app

If an app was not connected on the setup page, connect it now:

```sh
dotiphant connect               # lists apps and whether they were found
dotiphant connect claude-desktop  # or claude-code, codex, cursor
```

Tell the user to fully quit and restart the AI app afterwards (on macOS: Cmd+Q, not just closing the window). If an app shows as "not found", `dotiphant connect NAME --print` shows the configuration to add by hand. Claude Code is connected through its `claude` command; if that is missing, `dotiphant connect claude-code` prints the command to run later.

ChatGPT and claude.ai (web and phone) only reach MCP servers on the internet over HTTPS. Dotiphant supports them with `dotiphant mcp --http --public-url URL` behind an HTTPS tunnel the user runs (ngrok, Tailscale Funnel or Cloudflare Tunnel). Follow docs/remote-access.md in the repository step by step. Before you start, explain to the user that the tunnel makes the archive reachable from the internet for any app they approve, with the pairing code or a terminal command.

### 5. Verify

Run `dotiphant status`. It should show the account as logged in, a number of chats and messages, the last sync time, and whether background refresh is on. Run `dotiphant doctor` if anything looks wrong and follow its hints.

Then tell the user:

- `dotiphant open` shows the archive in the browser.
- In their AI app they can now ask things like "What did Clara and I agree on about the launch?" or "Summarize my groups since yesterday."
- Their data stays on this computer. The AI app sends the messages it reads to its provider, like anything else typed into it.
