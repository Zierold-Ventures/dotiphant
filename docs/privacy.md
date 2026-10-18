# Privacy

This notice covers the open-source Dotiphant app. It explains what data exists, where it lives, who receives it, and how to delete it.

## The short version

- Dotiphant runs on your computer. It has no server, no account and no telemetry.
- The people who make Dotiphant never receive your messages, your contacts or your Telegram login.
- Your messages leave your computer only when an AI app you connected reads them to answer you.
- You choose which chats AI apps may read: all chats except the ones you hide, or only the ones you allow.
- You can delete everything with two commands and one folder.

## What data exists

When you log in and sync, Dotiphant stores:

- Your Telegram messages, with the names of chats and senders, and dates.
- Photos and files, only for chats where you asked for media.
- Each chat's profile photo, a small version, shown only in the viewer.
- Your Telegram API id and hash, and your login session.
- Your settings, the AI apps you approved for remote access, and sync logs. Logs do not contain message text, phone numbers or secrets.

These messages include personal data about the people you chat with, not only about you.

## Where it lives

Everything stays on your computer:

- Messages, media, settings and logs are in the Dotiphant data folder. Only your user account can read it. `dotiphant status` shows the path.
- The API id and hash, the login session and access keys are in your system keychain.

The archive is not encrypted by Dotiphant. Turn on disk encryption (FileVault, BitLocker or LUKS).

Telegram knows that your account has a session called "Dotiphant". You can see and end it in Telegram under Settings > Devices.

## Who receives what

- Telegram: Dotiphant sends Telegram the requests needed to read your chats, using the API credentials you created. Dotiphant only reads. It never sends, edits or deletes messages, and it never marks them as read.
- The makers of Dotiphant: nothing. There is no analytics, no crash reporting and no update check that sends data.
- AI apps you connect: when you ask a question, the app calls Dotiphant and receives the matching messages, with sender names, chat names and dates. The app sends that content to its provider (for example Anthropic, OpenAI or Cursor) to answer you. That provider's privacy terms apply from then on. Dotiphant cannot control what happens there. AI apps only receive chats you let them read; see below.
- Remote access: if you run a tunnel (ngrok, Tailscale, Cloudflare), requests pass through that tunnel provider. Only apps you approved can read the archive.

AI apps read your archive to answer your questions. Dotiphant does not train AI models on your messages and does not help anyone else do so. Using Telegram data with AI apps is subject to the [Telegram API Terms of Service](https://core.telegram.org/api/terms) and the [Terms for Content Licensing and AI Scraping](https://telegram.org/tos/content-licensing). Check them before you connect an AI app; you are responsible for how you use your archive.

## Choose which chats AI apps may read

The messages in your archive come from the people you chat with. When an AI app reads a chat, their messages go to the AI provider, and they were not asked. So you decide, chat by chat, what AI apps may read:

- All chats (the default): AI apps can read every chat except the ones you hide.
- Only the ones I choose: AI apps see no chat until you allow it. New chats stay hidden.

Set this during setup, on the Connect AI page of the viewer, or with `dotiphant ai mode all` or `dotiphant ai mode selected`. Hide or allow one chat with the "Visible to AI apps" switch on the chat's page, or with `dotiphant ai hide CHAT` and `dotiphant ai show CHAT`. `dotiphant ai` shows the current choice.

A hidden chat is never listed, searched or read by AI apps, and an AI app cannot start a refresh of it. If an AI app asks for it by id, username or name, it gets the same answer as for a chat that does not exist, so it cannot learn that the chat exists or what it is called. A group that was upgraded to a supergroup counts as one chat. Changes apply from the AI app's next request.

The viewer and the `dotiphant` command are your own tools, so they still show every chat. Hiding a chat does not delete it; `dotiphant forget CHAT` does.

## Your responsibilities toward the people you chat with

Your archive holds other people's messages. They did not choose to use Dotiphant.

- Keep the archive for your own use. Do not publish it, sell it or share it with people who were not in the conversation.
- Before you connect an AI app, check how its provider handles your data. Some consumer plans may use conversations to improve their models unless you opt out. Prefer settings or plans that do not train on your data.
- Be careful with sensitive topics, such as health, politics, religion or money. Only send them to an AI app if you would be comfortable forwarding the messages yourself. Hide chats like these from AI apps with `dotiphant ai hide CHAT`.
- If someone asks you to delete their messages, honor it. Dotiphant keeps messages even after they are deleted or edited in Telegram. Run `dotiphant forget CHAT` to delete that chat, its messages and its media from the archive. Dotiphant then stops syncing it.
- If you use Dotiphant for work, for example for a sales or business development team, data protection law (such as the GDPR in the EU) likely applies in full. Your company is then responsible for having a legal basis, informing the people concerned, and having a data processing agreement with the AI provider. Ask your data protection officer before you start.

## How to delete everything

```sh
dotiphant schedule remove              # stop background refresh
dotiphant logout --forget-credentials  # end the Telegram session, remove stored secrets
```

Then delete the data folder (`dotiphant status` shows the path). If you tried `dotiphant demo`, also delete the demo folder next to it. Remove the `dotiphant` entry from your AI apps' MCP settings. Finally run `uv tool uninstall dotiphant` (or `pipx uninstall dotiphant`).

If Telegram cannot be reached during logout, end the session in Telegram under Settings > Devices.

Messages that AI apps already read stay with their providers. Delete those conversations in each app if you want them gone.

More detail: [security.md](security.md).

## Not affiliated with Telegram

Dotiphant is an independent, unofficial tool. It is not made, endorsed or supported by Telegram.

## Contact

Questions about this notice: open an issue at https://github.com/Zierold-Ventures/dotiphant/issues.
