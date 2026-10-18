"""Remove one chat from the archive, for example when someone asks you to delete their data.

This is the only code path that deletes messages. It runs only on an explicit user
command and takes the sync lock, which syncs, media downloads and imports also hold, so
nothing writes the chat back while it is deleted. It adds the chat to the excluded chats
first, so even a forget that stops halfway keeps the next sync from fetching it again.

Deleted text must not linger on disk: secure_delete zeroes freed pages, the search index
is merged so no old segment keeps the words, VACUUM rewrites the file, and a TRUNCATE
checkpoint empties the write-ahead log, which holds copies of earlier pages.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import config, db, query, status


class Busy(RuntimeError):
    pass


# How long the final checkpoint waits for open readers (the viewer, an MCP server) to finish.
CHECKPOINT_WAIT_MS = 10_000


def forget_chat(chat_ref: str | int) -> dict[str, Any]:
    """Delete a chat (and the group it was upgraded from), its messages, its media and its profile photo."""
    lock_file = open(config.data_dir() / "sync.lock", "a+")
    try:
        if not status.try_lock(lock_file):
            raise Busy("A sync, download or import is running. Try again when it finishes.")
        try:
            return _forget(chat_ref)
        finally:
            status.unlock(lock_file)
    finally:
        lock_file.close()


def _forget(chat_ref: str | int) -> dict[str, Any]:
    conn = db.open_rw()
    try:
        chat = query.resolve_chat(conn, chat_ref)
        ids = query.chat_ids_with_predecessor(conn, chat["chat_id"])

        def exclude(settings: config.Settings) -> None:
            settings.excluded_chats = sorted(set(settings.excluded_chats) | set(ids))
            settings.favorites = [c for c in settings.favorites if c not in ids]

        config.update_settings(exclude)
        marks = ",".join("?" * len(ids))
        conn.execute("PRAGMA secure_delete=ON")
        # People who wrote in this chat, and the chat's own peer (the other person of a private
        # chat, or a channel posting as itself). Their names go once no other message is theirs.
        people = {r[0] for r in conn.execute(
            f"SELECT DISTINCT sender_id FROM messages WHERE chat_id IN ({marks}) AND sender_id IS NOT NULL", ids)}
        people |= set(ids)
        owner = db.get_meta(conn, "owner_id")
        people.discard(int(owner) if owner and owner.lstrip("-").isdigit() else None)
        with conn:
            # Begin explicitly: sqlite3 does not open a transaction for DROP TRIGGER, and a
            # failure later on must roll the guard back into place.
            conn.execute("BEGIN IMMEDIATE")
            # The archive normally refuses deletes; lift that guard for this one transaction.
            conn.execute("DROP TRIGGER IF EXISTS messages_no_delete")
            conn.execute(
                f"INSERT INTO messages_fts(messages_fts, rowid, text) SELECT 'delete', row_id,"
                f" text || ' ' || coalesce(media_name, '') FROM messages WHERE chat_id IN ({marks})",
                ids,
            )
            removed = conn.execute(f"DELETE FROM messages WHERE chat_id IN ({marks})", ids).rowcount
            for table in ("message_versions", "topics", "sync_state", "chat_stats", "chat_senders"):
                conn.execute(f"DELETE FROM {table} WHERE chat_id IN ({marks})", ids)
            for cid in ids:
                conn.execute("DELETE FROM meta WHERE key IN (?, ?)", (f"gap:{cid}", f"top:{cid}"))
            conn.execute(f"DELETE FROM chats WHERE chat_id IN ({marks})", ids)
            conn.executemany(
                "DELETE FROM senders WHERE peer_id=? AND NOT EXISTS (SELECT 1 FROM messages WHERE sender_id=?)",
                [(p, p) for p in people],
            )
            # 'delete' only marks the rows; merging the index drops the words from its pages.
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")
            conn.execute(
                "CREATE TRIGGER messages_no_delete BEFORE DELETE ON messages"
                " BEGIN SELECT RAISE(ABORT, 'Dotiphant never deletes stored messages'); END"
            )
        conn.execute("VACUUM")
        conn.execute(f"PRAGMA busy_timeout={int(CHECKPOINT_WAIT_MS)}")
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
    finally:
        conn.close()

    media_left = []
    for cid in ids:
        folder = config.media_dir() / str(cid)
        if not _remove_folder(folder):
            media_left.append(str(folder))
        avatars = config.media_dir() / "avatars"
        for photo in avatars.glob(f"{cid}-*") if avatars.is_dir() else []:
            photo.unlink(missing_ok=True)
    return {"chat_id": chat["chat_id"], "title": chat["title"], "messages_removed": removed, "chat_ids": ids,
            "log_cleared": not busy, "media_left": media_left}


def _remove_folder(folder: Path, tries: int = 3) -> bool:
    """Delete a media folder. A file that appears or resists is retried; never raises.

    Returns False when the folder is still there, so the caller can tell the user.
    """
    for _ in range(tries):
        if not folder.exists():
            return True
        shutil.rmtree(folder, ignore_errors=True)
    return not folder.exists()
