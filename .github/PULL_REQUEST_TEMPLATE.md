## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] Dotiphant stays read-only toward Telegram. Nothing here sends, edits, deletes, forwards or marks messages as read.
- [ ] Only `sync.py` and `importer.py` write to the archive, and no stored message is deleted.
- [ ] Tests use a temporary `DOTIPHANT_HOME`, `DOTIPHANT_SECRETS=file` and the fakes in `tests/fakes.py`. No network, no real archive, no real keychain.
- [ ] Logs, status files and error messages contain no message text, phone numbers or secrets.
- [ ] No new dependency, or the reason for it is explained above.
- [ ] The viewer still loads nothing from the internet.
- [ ] `uv run pytest -q` and `uvx pyflakes src tests` pass.
- [ ] Docs are updated, with plain ASCII punctuation and short sentences.
