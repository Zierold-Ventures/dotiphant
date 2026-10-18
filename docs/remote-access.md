# Remote access: ChatGPT, claude.ai and other remote apps

Claude Desktop, Claude Code, Codex and Cursor run Dotiphant on your computer, so `dotiphant connect` is all they need. Some AI apps can only reach MCP servers on the internet over HTTPS: ChatGPT, claude.ai on the web and phone, and other apps that support remote MCP servers. For those, you run `dotiphant mcp --http` and put an HTTPS tunnel in front of it.

## The trade-off

- Anyone who finds the tunnel URL still needs your approval. Each app you connect shows an approval page, and approving needs either a command in your terminal or a one-time pairing code that only appears on your computer.
- The AI provider (OpenAI, Anthropic and so on) sees the messages its assistant reads, as with any connector.
- Your computer must be on, awake and running Dotiphant and the tunnel. Stop both when you no longer need them.

## How it works

1. A tunnel gives your computer a public HTTPS address, like `https://abc.ngrok.app`, and forwards requests to Dotiphant on 127.0.0.1. Dotiphant itself never listens on your network.
2. You add `https://abc.ngrok.app/mcp` as a connector in the AI app.
3. The app opens an approval page. It shows the app's name, what it gets (read-only access to your archive) and the full address your browser returns to, with the host highlighted. Approve in one of two ways:
   - Safer: run the command the page shows, like `dotiphant mcp --approve K7QM-2XPA`, in a terminal on your computer. It prints the app name and the exact return address and asks y/N. Answer y, then choose Continue on the page.
   - Or enter the pairing code from your terminal and choose Approve.
4. The app gets its own token. It can search and read your archive, and it can start a refresh (at most one sync per minute). It can never send anything to Telegram.

Check the return address before you approve. The access goes to whoever runs that address. The app name is chosen by the app and proves nothing; the host is what counts (for example `chatgpt.com` or `claude.ai`).

The pairing code works once and expires after 30 minutes. Five wrong codes close that approval request; start connecting again from the app. After 20 wrong codes within 10 minutes, code entry pauses for 10 minutes. Terminal approval still works then. Run `dotiphant mcp --pair` on your computer to see the current code, or get a new one.

## Step 1: start a tunnel

Pick one. Each prints or shows an `https://` address. Keep the tunnel running in its own terminal.

### ngrok

1. Create a free account at [ngrok.com](https://ngrok.com), install ngrok and run the `ngrok config add-authtoken ...` command from your dashboard.
2. Run:

   ```sh
   ngrok http 8766
   ```

3. Copy the `https://...ngrok...` address from the "Forwarding" line. Free accounts get one fixed domain, so the address stays the same next time.

### Tailscale Funnel

1. Install [Tailscale](https://tailscale.com) and log in. Funnel must be allowed for your tailnet; the first run tells you how if it is not.
2. Run:

   ```sh
   tailscale funnel 8766
   ```

3. Copy the address it prints, like `https://my-laptop.tail1234.ts.net`. It stays the same.

### Cloudflare Tunnel (quick tunnel)

No account needed.

1. Install `cloudflared` (on macOS: `brew install cloudflared`).
2. Run:

   ```sh
   cloudflared tunnel --url http://127.0.0.1:8766
   ```

3. Copy the `https://...trycloudflare.com` address it prints. It changes every time you start the tunnel, so you have to update the connector each time. For a fixed address, set up a named Cloudflare Tunnel.

## Step 2: start Dotiphant

In another terminal, with your tunnel address:

```sh
dotiphant mcp --http --public-url https://abc.ngrok.app
```

It prints the pairing code. Leave it running. Dotiphant still listens only on 127.0.0.1; `--public-url` tells it its public name, so its approval pages and the host check use that address.

## Step 3: add the connector

Use your tunnel address with `/mcp` at the end, for example `https://abc.ngrok.app/mcp`. Choose OAuth if the app asks for an authentication type. Leave the client id and secret empty: the app registers itself.

Menu names may differ between versions and plans.

### ChatGPT

Custom connectors need a paid plan and work in ChatGPT on the web.

1. Open Settings > Apps & Connectors > Advanced settings and turn on Developer mode.
2. Back in Apps & Connectors, choose Create.
3. Enter a name (for example "Dotiphant"), the URL ending in `/mcp`, and OAuth as authentication. Confirm that you trust the connector.
4. The approval page opens. Enter the pairing code and choose Approve.
5. In a new chat, turn on the connector from the tools menu (Developer mode).

### claude.ai

Works on the web; once added, the connector is also available in the Claude mobile apps.

1. Open Settings > Connectors and choose Add custom connector. On Team and Enterprise plans an owner adds it under Organization settings > Connectors.
2. Enter a name and the URL ending in `/mcp`, then choose Add.
3. Choose Connect. The approval page opens. Enter the pairing code and choose Approve.

### Other apps

Any app that supports remote MCP servers with OAuth works the same way. Apps that let you set a header can skip OAuth and send `Authorization: Bearer <token>` instead; `dotiphant mcp --show-token` prints that token. Anyone who has this token can read your archive, so keep it private.

## Managing access

```sh
dotiphant mcp --clients             # apps you approved
dotiphant mcp --revoke CLIENT_ID    # remove one app; its tokens stop working at once
dotiphant mcp --revoke all          # remove every approved app
dotiphant mcp --pair                # show the pairing code to approve another app
dotiphant mcp --approve REQUEST_ID  # approve a waiting app after checking its return address
```

If an app ever uses an old refresh token a second time, Dotiphant takes it as a sign that the token was copied and revokes all of that app's tokens. Approve the app again if that was a mistake.

Approved apps, hashed tokens and the pairing code are stored in `oauth.json` in the Dotiphant data folder, readable only by your user.

## Troubleshooting

- "Invalid Host header": the address in `--public-url` does not match the address the app uses. Restart Dotiphant with the exact tunnel address.
- The approval page says the code expired: run `dotiphant mcp --pair` and use the new code, or use `dotiphant mcp --approve`.
- The approval page says code entry is paused: someone entered many wrong codes. Use `dotiphant mcp --approve` with the id the page shows.
- The approval page says the link expired: approval requests last 15 minutes. Start connecting again from the app.
- The app cannot register and mentions `redirect_uri`: Dotiphant only accepts `https://` return addresses (or `http://` on 127.0.0.1, localhost or [::1]), at most 5 of them.
- The app says it cannot connect: check that both the tunnel and `dotiphant mcp --http` are running, and that the URL ends in `/mcp`.
