"""Sync tests against a fake Telethon client. Nothing here talks to Telegram."""
from __future__ import annotations

import datetime as dt
import json
import os

import pytest
from telethon import errors, functions, types, utils

from dotiphant import config, db, query, sync
from dotiphant import status as sync_status
from tests import fakes
from tests.fakes import FakeClient, msg


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    monkeypatch.setattr(sync, "PAGE_DELAY", 0)
    monkeypatch.setattr(sync, "BACKFILL_DELAY", 0)
    monkeypatch.setattr(sync, "AVATAR_DELAY", 0)
    return tmp_path


def everything(**kw) -> config.Settings:
    return config.Settings(history_days=None, **kw)


def rows(chat_id):
    conn = db.connect(readonly=True)
    try:
        return {r["message_id"]: dict(r) for r in conn.execute("SELECT * FROM messages WHERE chat_id=?", (chat_id,))}
    finally:
        conn.close()


def one(sql, *args):
    conn = db.connect(readonly=True)
    try:
        return conn.execute(sql, args).fetchone()
    finally:
        conn.close()


GROUP = fakes.supergroup(500, "Team")
GROUP_ID = utils.get_peer_id(GROUP)
GROUP_PEER = types.PeerChannel(500)
ALICE = fakes.user(10, "Alice", "A")


def team_client(n=350):
    client = FakeClient()
    client.users = [ALICE]
    client.add_chat(GROUP, [msg(GROUP_PEER, i, f"hello {i}", sender=10) for i in range(1, n + 1)])
    return client


async def test_first_sync_stores_everything_newest_first_and_resumes_without_gaps():
    client = team_client(350)
    client.fail_on_request = 4  # full info, latest page, one backfill page, then the network drops
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["errors"] and summary["errors"][0]["error"] == "RuntimeError"
    stored = rows(GROUP_ID)
    assert sorted(stored) == list(range(151, 351)), "the newest messages come first"
    state = one("SELECT * FROM sync_state WHERE chat_id=?", GROUP_ID)
    assert (state["newest_id"], state["oldest_id"], state["backfill_done"]) == (350, 151, 0)

    client.fail_on_request = None
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and summary["new_messages"] == 150
    stored = rows(GROUP_ID)
    assert sorted(stored) == list(range(1, 351))
    assert one("SELECT count(*) FROM messages")[0] == 350
    assert one("SELECT backfill_done FROM sync_state WHERE chat_id=?", GROUP_ID)[0] == 1
    m = stored[42]
    assert m["text"] == "hello 42" and m["sender_id"] == 10 and m["kind"] == "text" and m["topic_id"] is None
    assert m["date"] == "2026-01-01T00:42:00+00:00"
    assert json.loads(m["raw_json"])["id"] == 42
    assert one("SELECT name FROM senders WHERE peer_id=10")[0] == "Alice A"
    assert one("SELECT last_message_at FROM chats WHERE chat_id=?", GROUP_ID)[0] == stored[350]["date"]


async def test_time_limit_stops_cleanly_and_next_run_continues(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock["now"])
    client = team_client(450)
    client.on_request = lambda req: clock.__setitem__("now", clock["now"] + 10)
    summary = await sync.run_sync(client=client, settings=everything(), max_seconds=25)
    assert summary["stopped"] == "time_limit" and summary["resumable"]
    assert sorted(rows(GROUP_ID)) == list(range(251, 451))
    assert sync_status.read_status()["state"] == "idle"

    clock["now"] = 0.0
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and not summary["resumable"]
    assert sorted(rows(GROUP_ID)) == list(range(1, 451))


async def test_second_sync_fetches_only_newer_messages_and_fills_large_gaps():
    client = team_client(120)
    await sync.run_sync(client=client, settings=everything())
    assert len(rows(GROUP_ID)) == 120

    client.requests.clear()
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["new_messages"] == 0 and client.requests == [], "an unchanged chat costs no requests"

    client.add_messages(GROUP_ID, [msg(GROUP_PEER, i, f"new {i}", sender=10) for i in range(121, 126)])
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["new_messages"] == 5
    assert [(r.offset_id, r.min_id) for r in client.requests] == [(0, 0)]

    # More than a page of new messages: pages go top down with offset_id until the stored edge.
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, i, f"new {i}", sender=10) for i in range(126, 476)])
    client.requests.clear()
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["new_messages"] == 350
    assert sorted(rows(GROUP_ID)) == list(range(1, 476))
    assert one("SELECT newest_id FROM sync_state WHERE chat_id=?", GROUP_ID)[0] == 475
    assert [r.offset_id for r in client.requests] == [0, 376, 276, 176]
    assert all(r.min_id == 0 and r.max_id == 0 and r.add_offset == 0 for r in client.requests)
    assert client.empty_answers == 0


def gaps(chat_id) -> list[tuple[int, int, bool]]:
    """The recorded gaps of a chat: (low, high, deferred), ids strictly between are unfetched."""
    value = one("SELECT value FROM meta WHERE key=?", f"gap:{chat_id}")
    out = []
    for part in (value[0].split(";") if value else []):
        bits = part.split(",")
        out.append((int(bits[0]), int(bits[1]), len(bits) > 2 and bits[2] == "old"))
    return out


def covered(chat_id, unavailable=()) -> bool:
    """Every id from the lowest stored one up to newest_id is stored, not served by Telegram,
    or inside a recorded gap."""
    newest = one("SELECT newest_id FROM sync_state WHERE chat_id=?", chat_id)[0]
    stored = set(rows(chat_id))
    if not stored:
        return True
    spans = gaps(chat_id)
    return all(i in stored or i in unavailable or any(lo < i < hi for lo, hi, _ in spans)
               for i in range(min(stored), newest + 1))


async def test_gap_fill_interrupted_records_the_gap_and_resumes_it():
    client = team_client(10)
    await sync.run_sync(client=client, settings=everything())
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, i, "x", sender=10) for i in range(11, 400)])
    client.requests.clear()
    client.fail_on_request = 3  # latest page ok, one gap page ok, then failure
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["resumable"]
    assert sorted(rows(GROUP_ID)) == list(range(1, 11)) + list(range(200, 400))
    assert one("SELECT value FROM meta WHERE key=?", f"gap:{GROUP_ID}")[0] == "10,200"
    assert covered(GROUP_ID)

    client.fail_on_request = None
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert sorted(rows(GROUP_ID)) == list(range(1, 400))
    assert one("SELECT value FROM meta WHERE key=?", f"gap:{GROUP_ID}") is None
    assert [r.offset_id for r in client.requests] == [200, 100], "the gap resumes where it stopped"


async def test_gap_fill_never_relies_on_min_id():
    """Telegram answers min_id far below the top with nothing; the gap must still be filled."""
    client = team_client(10)
    await sync.run_sync(client=client, settings=everything())
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, i, "x", sender=10) for i in range(11, 1000)])
    client.hidden = {500, 501, 502}  # and some pages come back short
    await sync.run_sync(client=client, settings=everything())
    assert set(rows(GROUP_ID)) == set(range(1, 1000)) - client.hidden
    assert one("SELECT newest_id FROM sync_state WHERE chat_id=?", GROUP_ID)[0] == 999
    assert client.empty_answers == 0 and covered(GROUP_ID, client.hidden)


async def test_gap_fill_stopped_by_time_limit_loses_nothing(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock["now"])
    client = team_client(10)
    await sync.run_sync(client=client, settings=everything())
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, i, "x", sender=10) for i in range(11, 2000)])
    client.on_request = lambda req: clock.__setitem__("now", clock["now"] + 10)
    for _ in range(10):
        clock["now"] = 0.0
        summary = await sync.run_sync(client=client, settings=everything(), max_seconds=35)
        assert covered(GROUP_ID)
        if not summary["resumable"]:
            break
    assert sorted(rows(GROUP_ID)) == list(range(1, 2000))


async def test_settings_filters_and_private_chat_senders():
    client = FakeClient()
    bob = fakes.user(20, "Bob")
    bot = fakes.user(30, "Helper", bot=True)
    news = fakes.supergroup(600, "News", broadcast=True)
    hidden = fakes.supergroup(700, "Muted")
    bob_peer = types.PeerUser(20)
    client.add_chat(bob, [msg(bob_peer, 1, "hi"), msg(bob_peer, 2, "hey", out=True)])
    client.add_chat(bot, [msg(types.PeerUser(30), 1, "beep")])
    client.add_chat(news, [msg(types.PeerChannel(600), 1, "post")])
    client.add_chat(hidden, [msg(types.PeerChannel(700), 1, "x", sender=20)])
    client.add_chat(fakes.basic_group(800, "Old friends"), [msg(types.PeerChat(800), 1, "yo", sender=20)],
                    archived=True)
    await sync.run_sync(client=client, settings=config.Settings(excluded_chats=[-1000000000700]))

    conn = db.connect(readonly=True)
    chats = {r["chat_id"]: dict(r) for r in conn.execute("SELECT * FROM chats")}
    conn.close()
    assert set(chats) == {20, -800}
    assert chats[20]["kind"] == "private" and chats[20]["title"] == "Bob"
    assert chats[-800]["kind"] == "group" and chats[-800]["archived"] == 1
    private = rows(20)
    assert private[1]["sender_id"] == 20 and private[1]["out"] == 0
    assert private[2]["sender_id"] == 1 and private[2]["out"] == 1
    assert one("SELECT name FROM senders WHERE peer_id=1")[0] == "Elowen Q"

    # Explicitly requested chats are synced even when the type filter would skip them.
    await sync.run_sync([utils.get_peer_id(news)], client=client, settings=config.Settings())
    assert len(rows(utils.get_peer_id(news))) == 1


async def test_upgraded_basic_group_history_is_merged():
    client = FakeClient()
    client.users = [ALICE]
    client.add_chat(GROUP, [msg(GROUP_PEER, i, f"new era {i}", sender=10, minutes=100 + i) for i in range(1, 4)])
    client.full[GROUP_ID] = fakes.full_channel(500, migrated_from=77)
    old_peer = types.PeerChat(77)
    client.add_messages(-77, [msg(old_peer, i, f"old {i}", sender=10) for i in range(1, 6)])
    client.add_messages(-77, [types.MessageService(id=6, peer_id=old_peer, date=fakes.START, from_id=types.PeerUser(10),
                                                   action=types.MessageActionChatMigrateTo(channel_id=500))])
    await sync.run_sync(client=client, settings=everything())

    pred = one("SELECT * FROM chats WHERE chat_id=-77")
    assert pred["migrated_to"] == GROUP_ID and pred["kind"] == "group"
    assert one("SELECT member_count FROM chats WHERE chat_id=?", GROUP_ID)[0] == 42
    assert sorted(rows(-77)) == [1, 2, 3, 4, 5, 6]
    assert rows(-77)[6]["service_text"] == "The group was upgraded to a supergroup"
    conn = db.connect(readonly=True)
    assert sorted(query.chat_ids_with_predecessor(conn, GROUP_ID)) == sorted([GROUP_ID, -77])
    assert [c["chat_id"] for c in query.list_chats(conn)] == [GROUP_ID]
    conn.close()

    # Full channel info is fetched once, on the first sync only.
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 4, "later", sender=10, minutes=200)])
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert not any(type(r).__name__ == "GetFullChannelRequest" for r in client.requests)


async def test_forum_topics_are_assigned():
    forum = fakes.supergroup(900, "Forum", forum=True)
    fid = utils.get_peer_id(forum)
    peer = types.PeerChannel(900)
    client = FakeClient()
    client.users = [ALICE]
    client.topics[fid] = [fakes.topic(peer, 1, "General", 1), fakes.topic(peer, 5, "Releases", 5)]
    header = types.MessageReplyHeader
    client.add_chat(forum, [
        msg(peer, 2, "in general", sender=10),
        types.MessageService(id=5, peer_id=peer, date=fakes.START, from_id=types.PeerUser(10),
                             action=types.MessageActionTopicCreate(title="Releases", icon_color=0)),
        msg(peer, 6, "first release post", sender=10, reply_to=header(forum_topic=True, reply_to_msg_id=5)),
        msg(peer, 7, "reply in topic", sender=10,
            reply_to=header(forum_topic=True, reply_to_msg_id=6, reply_to_top_id=5)),
    ])
    await sync.run_sync(client=client, settings=everything())
    stored = rows(fid)
    assert {i: stored[i]["topic_id"] for i in stored} == {2: 1, 5: 5, 6: 5, 7: 5}
    assert stored[6]["reply_to_id"] is None and stored[7]["reply_to_id"] == 6
    assert stored[5]["kind"] == "service" and stored[5]["service_text"] == 'Alice A created the topic "Releases"'
    conn = db.connect(readonly=True)
    topics = dict(conn.execute("SELECT topic_id, title FROM topics WHERE chat_id=?", (fid,)).fetchall())
    conn.close()
    assert topics == {1: "General", 5: "Releases"}
    assert one("SELECT is_forum FROM chats WHERE chat_id=?", fid)[0] == 1


async def test_edited_message_keeps_previous_version():
    client = team_client(3)
    await sync.run_sync(client=client, settings=everything())
    edited = msg(GROUP_PEER, 3, "hello 3, corrected", sender=10, edit_date=fakes.START + dt.timedelta(hours=5))
    client.history[GROUP_ID] = [m for m in client.history[GROUP_ID] if m.id != 3] + [edited]
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 4, "next", sender=10)])
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["new_messages"] == 1 and summary["edited_messages"] == 1
    m = rows(GROUP_ID)[3]
    assert m["text"] == "hello 3, corrected" and m["edit_date"] == "2026-01-01T05:00:00+00:00"
    old = one("SELECT text FROM message_versions WHERE chat_id=? AND message_id=3", GROUP_ID)
    assert old[0] == "hello 3"
    conn = db.connect(readonly=True)
    hits = conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'corrected'").fetchall()
    conn.close()
    assert len(hits) == 1


async def test_history_days_cutoff_and_widening():
    now = dt.datetime.now(dt.timezone.utc)
    client = FakeClient()
    client.add_chat(GROUP, [
        types.Message(id=i, peer_id=GROUP_PEER, date=now - dt.timedelta(days=400 - i), message=str(i),
                      from_id=types.PeerUser(10)) for i in range(1, 401)
    ])
    await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    stored = rows(GROUP_ID)
    assert min(stored) >= 370 and max(stored) == 400
    assert one("SELECT backfill_done FROM sync_state WHERE chat_id=?", GROUP_ID)[0] == 1
    await sync.run_sync(client=client, settings=config.Settings(history_days=None))
    assert sorted(rows(GROUP_ID)) == list(range(1, 401))


async def test_lock_prevents_concurrent_runs():
    with open(config.lock_path(), "a+") as f:
        assert sync_status.try_lock(f)
        with pytest.raises(sync.SyncBusy):
            await sync.run_sync(client=team_client(1), settings=everything())
        assert not config.status_path().exists()
        assert sync_status.lock_held()
        sync_status.unlock(f)
    summary = await sync.run_sync(client=team_client(1), settings=everything())
    assert summary["ok"]


async def test_status_json_tracks_the_run_without_message_text():
    seen = []
    client = team_client(250)
    await sync.run_sync(client=client, settings=everything(), progress=seen.append)
    assert seen[0]["state"] == "running" and seen[0]["pid"] == os.getpid() and seen[0]["started_at"]
    assert {"recent", "history"} <= {s["stage"] for s in seen}
    text = config.status_path().read_text()
    status = json.loads(text)
    assert status["state"] == "idle" and status["finished_at"] and status["new_messages"] == 250
    assert set(status) >= {"state", "stage", "chat", "chats_done", "chats_total", "new_messages",
                           "started_at", "finished_at", "message", "pid"}
    assert "hello" not in text
    assert oct(config.status_path().stat().st_mode & 0o777) == "0o600"

    # A "running" status left behind by a dead process reads as idle.
    status.update(state="running", pid=2**22 + 12345, finished_at=None)
    config.write_private(config.status_path(), json.dumps(status))
    current = sync_status.read_status()
    assert current["state"] == "idle" and current["stale"]


async def test_long_flood_wait_stops_gracefully():
    client = team_client(250)
    client.flood_on_request = (2, 3600)
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["stopped"] == "flood_wait" and summary["resumable"]
    assert sync_status.read_status()["state"] == "idle"
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and sorted(rows(GROUP_ID)) == list(range(1, 251))


async def test_not_authorized_asks_for_setup(monkeypatch):
    client = FakeClient()
    client.authorized = False
    from dotiphant import telegram
    monkeypatch.setattr(telegram, "make_client", lambda: client)
    with pytest.raises(sync.NotAuthorized, match="dotiphant setup"):
        await sync.run_sync(settings=everything())
    assert sync_status.read_status()["state"] == "error"


def document(mid, name, size, mime="application/pdf", attrs=()):
    doc = types.Document(id=mid, access_hash=1, file_reference=b"ref", date=fakes.START, mime_type=mime, size=size,
                         dc_id=1, attributes=[types.DocumentAttributeFilename(file_name=name), *attrs])
    return types.MessageMediaDocument(document=doc)


async def test_media_mapping_and_download(home):
    client = FakeClient()
    client.users = [ALICE]
    photo = types.Photo(id=1, access_hash=1, file_reference=b"", date=fakes.START, dc_id=1,
                        sizes=[types.PhotoSize(type="x", w=10, h=10, size=1234)])
    client.add_chat(GROUP, [
        msg(GROUP_PEER, 1, "the report", sender=10, media=document(1, "report/../q3.pdf", 2000)),
        msg(GROUP_PEER, 2, "", sender=10, media=types.MessageMediaPhoto(photo=photo)),
        msg(GROUP_PEER, 3, "", sender=10, media=document(3, "huge.mov", 80 * 1024 * 1024, "video/quicktime",
                                                        [types.DocumentAttributeVideo(duration=1, w=1, h=1)])),
        msg(GROUP_PEER, 4, "", sender=10, media=document(4, "a.ogg", 10, "audio/ogg",
                                                        [types.DocumentAttributeAudio(duration=1, voice=True)])),
        msg(GROUP_PEER, 5, "look", sender=10, fwd_from=types.MessageFwdHeader(date=fakes.START, from_name="Carol")),
    ])
    await sync.run_sync(client=client, settings=everything())
    stored = rows(GROUP_ID)
    assert (stored[1]["kind"], stored[1]["media_type"], stored[1]["media_name"], stored[1]["media_size"]) == \
        ("media", "document", "report/../q3.pdf", 2000)
    assert (stored[2]["media_type"], stored[2]["media_size"]) == ("photo", 1234)
    assert stored[3]["media_type"] == "video" and stored[4]["media_type"] == "voice"
    assert stored[5]["forwarded_from"] == "Carol" and stored[5]["kind"] == "text"
    assert "file_reference" not in stored[1]["raw_json"]

    counts = await sync.download_media(GROUP_ID, client=client)
    assert counts["downloaded"] == 3 and counts["skipped_large"] == 1
    stored = rows(GROUP_ID)
    path = home / "media" / stored[1]["media_path"]
    assert stored[1]["media_path"] == f"{GROUP_ID}/1-report_.._q3.pdf"
    assert path.read_bytes() == b"fake-bytes-1" and path.parent == home / "media" / str(GROUP_ID)
    assert stored[2]["media_path"].endswith("2-photo.jpg")
    assert stored[3]["media_path"] is None

    counts = await sync.download_media(GROUP_ID, [3], client=client)
    assert counts["downloaded"] == 1 and rows(GROUP_ID)[3]["media_path"].endswith("3-huge.mov")
    assert not list((home / "media" / str(GROUP_ID)).glob("*.part"))


async def test_sync_downloads_attachments_of_auto_download_chats(home):
    client = FakeClient()
    client.users = [ALICE]
    client.add_chat(GROUP, [
        msg(GROUP_PEER, 1, "the report", sender=10, media=document(1, "q3.pdf", 2000)),
        msg(GROUP_PEER, 2, "", sender=10, media=document(2, "huge.mov", 80 * 1024 * 1024, "video/quicktime",
                                                        [types.DocumentAttributeVideo(duration=1, w=1, h=1)])),
    ])
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary.get("media_downloaded") == 0 and rows(GROUP_ID)[1]["media_path"] is None, "off by default"

    settings = everything()
    settings.auto_download_chats = [GROUP_ID]
    summary = await sync.run_sync(client=client, settings=settings)
    assert summary["media_downloaded"] == 1
    assert rows(GROUP_ID)[1]["media_path"].endswith("1-q3.pdf") and rows(GROUP_ID)[2]["media_path"] is None, \
        "large files stay on request"


# --- review regressions --------------------------------------------------------------

def dated(peer, ids, now, days_old):
    """Messages with ids `ids`, the oldest days_old days ago and the newest today."""
    ids = list(ids)
    step = dt.timedelta(days=days_old) / max(1, len(ids) - 1)
    return [types.Message(id=i, peer_id=peer, date=now - step * (len(ids) - 1 - n), message=str(i),
                          from_id=types.PeerUser(10)) for n, i in enumerate(ids)]


async def test_error_during_first_sync_leaves_a_first_sync_for_next_time():
    now = dt.datetime.now(dt.timezone.utc)
    client = FakeClient()
    client.add_chat(GROUP, dated(GROUP_PEER, range(1, 1001), now, 1000))
    client.fail_on_request = 2  # full channel info, then the first history page fails
    summary = await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    assert summary["errors"] and not rows(GROUP_ID)
    assert one("SELECT * FROM sync_state WHERE chat_id=?", GROUP_ID) is None, "no misleading state row"

    client.fail_on_request = None
    client.requests.clear()
    await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    stored = rows(GROUP_ID)
    assert max(stored) == 1000 and min(stored) > 960, "history_days still applies"
    history = [r for r in client.requests if isinstance(r, functions.messages.GetHistoryRequest)]
    assert history[0].offset_id == 0 and len(history) == 1


async def test_state_row_with_newest_id_zero_is_a_first_sync():
    now = dt.datetime.now(dt.timezone.utc)
    client = FakeClient()
    client.add_chat(GROUP, dated(GROUP_PEER, range(1, 1001), now, 1000))
    db.init()
    conn = db.open_rw()
    conn.execute("INSERT INTO sync_state(chat_id, newest_id, error) VALUES(?, 0, 'RuntimeError')", (GROUP_ID,))
    conn.commit()
    conn.close()
    await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    stored = rows(GROUP_ID)
    assert max(stored) == 1000 and min(stored) > 960, "not paged upward from id 1"
    assert one("SELECT error FROM sync_state WHERE chat_id=?", GROUP_ID)[0] is None


async def test_short_pages_mid_history_do_not_end_the_backfill():
    client = team_client(600)
    client.hidden = set(range(250, 330))  # Telegram leaves these out: a 20-message page mid-history
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and not summary["resumable"]
    assert set(rows(GROUP_ID)) == set(range(1, 601)) - client.hidden

    group = fakes.basic_group(808, "Small pages")
    peer = types.PeerChat(808)
    other = FakeClient()
    other.users = [ALICE]
    other.add_chat(group, [msg(peer, i, "x", sender=10) for i in range(1, 251)])
    other.page_cap = 30  # every page is short
    await sync.run_sync(client=other, settings=everything())
    assert sorted(rows(-808)) == list(range(1, 251))
    assert one("SELECT backfill_done FROM sync_state WHERE chat_id=-808")[0] == 1


async def test_message_empty_page_only_moves_the_cursor():
    group = fakes.basic_group(809, "Deleted middle")
    peer = types.PeerChat(809)
    client = FakeClient()
    client.users = [ALICE]
    history = [msg(peer, i, "x", sender=10) for i in range(1, 101)]
    history += [types.MessageEmpty(id=i, peer_id=peer) for i in range(101, 201)]
    history += [msg(peer, i, "y", sender=10) for i in range(201, 301)]
    client.add_chat(group, history)
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"]
    assert sorted(rows(-809)) == list(range(1, 101)) + list(range(201, 301))
    assert one("SELECT backfill_done FROM sync_state WHERE chat_id=-809")[0] == 1


def upgraded_client():
    client = FakeClient()
    client.users = [ALICE]
    client.add_chat(GROUP, [msg(GROUP_PEER, i, "new era", sender=10, minutes=100 + i) for i in range(1, 4)])
    client.full[GROUP_ID] = fakes.full_channel(500, migrated_from=77)
    client.inaccessible[-77] = errors.ChatIdInvalidError
    return client


def history_peers(client):
    return [utils.get_peer_id(r.peer) for r in client.requests if isinstance(r, functions.messages.GetHistoryRequest)]


async def test_inaccessible_predecessor_is_not_a_failure_and_is_retried_daily():
    client = upgraded_client()
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and summary["inaccessible_chats"] == 1
    assert one("SELECT error FROM sync_state WHERE chat_id=-77")[0].startswith("inaccessible: ChatIdInvalidError")
    assert sync_status.read_status()["message"] is None

    client.requests.clear()
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 4, "later", sender=10, minutes=200)])
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and -77 not in history_peers(client), "skipped while recently checked"

    conn = db.open_rw()
    conn.execute("UPDATE sync_state SET error='inaccessible: ChatIdInvalidError (checked 2020-01-01T00:00:00+00:00)'"
                 " WHERE chat_id=-77")
    conn.commit()
    conn.close()
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert -77 in history_peers(client), "tried again after a day"

    client.requests.clear()
    summary = await sync.run_sync([-77], client=client, settings=everything())
    assert -77 in history_peers(client), "an explicit request always tries"
    assert summary["errors"] and summary["errors"][0]["error"].startswith("inaccessible")


async def test_predecessor_respects_settings_and_is_not_requeued_when_complete():
    client = upgraded_client()
    await sync.run_sync(client=client, settings=everything(excluded_chats=[-77]))
    assert -77 not in history_peers(client)

    client.inaccessible.clear()
    client.add_messages(-77, [msg(types.PeerChat(77), i, "old", sender=10) for i in range(1, 6)])
    await sync.run_sync(client=client, settings=everything())
    assert sorted(rows(-77)) == [1, 2, 3, 4, 5]
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert history_peers(client) == [], "a finished predecessor costs nothing"


async def test_forbidden_dialogs_are_listed_but_not_fetched():
    client = team_client(3)
    client.add_chat(types.ChatForbidden(id=910, title="Old club"))
    client.add_chat(types.ChannelForbidden(id=920, access_hash=1, title="Banned news", broadcast=True))
    gone = types.User(id=40, deleted=True, access_hash=1)
    client.add_chat(gone, [msg(types.PeerUser(40), 1, "hi")])
    summary = await sync.run_sync(client=client, settings=everything(include_channels=True))
    assert summary["ok"] and summary["inaccessible_chats"] == 2
    assert one("SELECT title FROM chats WHERE chat_id=-910")[0] == "Old club"
    assert one("SELECT title FROM chats WHERE chat_id=-1000000000920")[0] == "Banned news"
    assert one("SELECT title FROM chats WHERE chat_id=40")[0] == "Deleted Account"
    assert set(history_peers(client)) == {GROUP_ID, 40}


async def test_backfill_is_round_robin_most_recent_first():
    other = fakes.supergroup(501, "Other")
    other_peer = types.PeerChannel(501)
    other_id = utils.get_peer_id(other)
    client = FakeClient()
    client.users = [ALICE]
    client.add_chat(GROUP, [msg(GROUP_PEER, i, "a", sender=10, minutes=i) for i in range(1, 1201)])
    client.add_chat(other, [msg(other_peer, i, "b", sender=10, minutes=10_000 + i) for i in range(1, 1201)])
    await sync.run_sync(client=client, settings=everything())
    backfill = [utils.get_peer_id(r.peer) for r in client.requests
                if isinstance(r, functions.messages.GetHistoryRequest) and r.offset_id]
    turn = sync.BACKFILL_PAGES_PER_TURN
    assert backfill[:2 * turn] == [other_id] * turn + [GROUP_ID] * turn
    assert sorted(rows(GROUP_ID)) == sorted(rows(other_id)) == list(range(1, 1201))


async def test_backfill_requests_are_spaced_more_than_new_message_requests(monkeypatch):
    monkeypatch.setattr(sync, "BACKFILL_DELAY", 1.0)
    monkeypatch.setattr(sync, "PAGE_DELAY", 0.3)
    clock = {"now": 1000.0}
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock["now"])
    waits = []

    async def fake_sleep(seconds):
        waits.append(round(seconds, 2))
        clock["now"] += seconds

    monkeypatch.setattr(sync.asyncio, "sleep", fake_sleep)
    await sync.run_sync(client=team_client(250), settings=everything())
    assert waits == [0.3, 1.0, 1.0], "full info, latest page, then two backfill pages"


async def test_run_inventory_lists_chats_only():
    client = team_client(5)
    client.add_chat(fakes.user(20, "Bob"), [msg(types.PeerUser(20), 1, "hi")])
    assert await sync.run_inventory(client=client, settings=everything()) == 2
    assert one("SELECT count(*) FROM chats")[0] == 2 and one("SELECT count(*) FROM messages")[0] == 0
    assert not config.status_path().exists()
    assert history_peers(client) == []

    with open(config.lock_path(), "a+") as f:
        assert sync_status.try_lock(f)
        with pytest.raises(sync.SyncBusy):
            await sync.run_inventory(client=client, settings=everything())
        sync_status.unlock(f)


def test_cli_resolves_unknown_names_through_run_inventory(monkeypatch):
    from dotiphant import cli, telegram

    db.init()
    monkeypatch.setattr(telegram, "make_client", lambda **kw: team_client(5))
    assert cli._resolve_names_for_sync(["Team"], quiet=True) == [GROUP_ID]


async def test_channel_post_by_owner_is_attributed_to_the_channel():
    news = fakes.supergroup(600, "News", broadcast=True)
    news_id = utils.get_peer_id(news)
    peer = types.PeerChannel(600)
    client = FakeClient()
    client.add_chat(news, [
        msg(peer, 1, "unsigned", out=True, post=True),
        msg(peer, 2, "signed", out=True, post=True, sender=1, post_author="Elowen"),
        msg(peer, 3, "group-style", out=True),
    ])
    await sync.run_sync(client=client, settings=everything(include_channels=True))
    stored = rows(news_id)
    assert stored[1]["sender_id"] == news_id and stored[2]["sender_id"] == news_id
    assert stored[1]["out"] == 1
    assert stored[3]["sender_id"] == 1
    assert one("SELECT name FROM senders WHERE peer_id=?", news_id)[0] == "News"


# --- fairness, cutoff, topics, senders, predecessors ---------------------------------

def tick(clock, seconds=10):
    return lambda req: clock.__setitem__("now", clock["now"] + seconds)


async def test_huge_gap_in_one_chat_does_not_starve_new_messages_elsewhere(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock["now"])
    busy = fakes.supergroup(501, "Busy")
    busy_peer = types.PeerChannel(501)
    busy_id = utils.get_peer_id(busy)
    client = FakeClient()
    client.users = [ALICE]
    client.add_chat(GROUP, [msg(GROUP_PEER, i, "a", sender=10, minutes=i) for i in range(1, 6)])
    client.add_chat(busy, [msg(busy_peer, i, "b", sender=10, minutes=100 + i) for i in range(1, 6)])
    await sync.run_sync(client=client, settings=everything())

    client.add_messages(busy_id, [msg(busy_peer, i, "b", sender=10, minutes=100_000 + i) for i in range(6, 50_006)])
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 6, "the one", sender=10, minutes=90_000)])
    client.requests.clear()
    client.on_request = tick(clock)
    summary = await sync.run_sync(client=client, settings=everything(), max_seconds=45)
    assert summary["stopped"] == "time_limit" and summary["resumable"]
    assert 6 in rows(GROUP_ID), "the quiet chat gets its new message in the same run"
    peers = history_peers(client)
    assert peers[:2] == [busy_id, GROUP_ID], "every chat gets its latest page first"
    assert peers[2:] == [busy_id] * sync.GAP_PAGES_PER_TURN
    assert covered(busy_id) and gaps(busy_id) == [(5, 49_606, False)]

    # Later runs keep filling the gap, a few pages per chat per pass, without holes.
    clock["now"] = 0.0
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything(), max_seconds=45)
    assert history_peers(client) == [busy_id] * 5, "an unchanged chat costs nothing"
    assert covered(busy_id)


async def test_dialog_top_that_history_never_serves_costs_one_request():
    client = team_client(10)
    await sync.run_sync(client=client, settings=everything())
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 11, "invisible", sender=10)])
    client.hidden = {11}
    counts = []
    for _ in range(3):
        client.requests.clear()
        await sync.run_sync(client=client, settings=everything())
        counts.append(len(client.requests))
    assert counts == [1, 0, 0]
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 12, "visible", sender=10)])
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert len(client.requests) == 1 and 12 in rows(GROUP_ID)


async def test_forum_topics_are_read_on_first_sync_and_when_a_topic_is_unknown():
    forum = fakes.supergroup(900, "Forum", forum=True)
    fid = utils.get_peer_id(forum)
    peer = types.PeerChannel(900)
    header = types.MessageReplyHeader
    client = FakeClient()
    client.users = [ALICE]
    client.topics[fid] = [fakes.topic(peer, 1, "General", 1), fakes.topic(peer, 5, "Releases", 5)]
    client.add_chat(forum, [msg(peer, 2, "hi", sender=10),
                            msg(peer, 6, "release", sender=10, reply_to=header(forum_topic=True, reply_to_msg_id=5))])

    def topic_requests():
        return sum(isinstance(r, functions.messages.GetForumTopicsRequest) for r in client.requests)

    await sync.run_sync(client=client, settings=everything())
    assert topic_requests() == 1

    client.requests.clear()
    client.add_messages(fid, [msg(peer, 7, "more", sender=10, reply_to=header(forum_topic=True, reply_to_msg_id=5))])
    await sync.run_sync(client=client, settings=everything())
    assert topic_requests() == 0, "known topics: no topic list request"

    client.requests.clear()
    client.topics[fid].append(fakes.topic(peer, 8, "Ideas", 8))
    client.add_messages(fid, [msg(peer, 9, "idea", sender=10, reply_to=header(forum_topic=True, reply_to_msg_id=8))])
    await sync.run_sync(client=client, settings=everything())
    assert topic_requests() == 1
    assert one("SELECT title FROM topics WHERE chat_id=? AND topic_id=8", fid)[0] == "Ideas"

    client.requests.clear()
    client.add_messages(fid, [msg(peer, 10, "x", sender=10, reply_to=header(forum_topic=True, reply_to_msg_id=8))])
    await sync.run_sync(client=client, settings=everything())
    assert topic_requests() == 0


async def test_cutoff_uses_the_lowest_message_not_any_old_one():
    now = dt.datetime.now(dt.timezone.utc)

    def history(peer, outliers):
        out = []
        for i in range(1, 401):
            when = now - dt.timedelta(days=100) if i in outliers else now - dt.timedelta(hours=400 - i)
            out.append(types.Message(id=i, peer_id=peer, date=when, message=str(i), from_id=types.PeerUser(10)))
        return out

    # All within 17 days but one oddly dated message mid-history: it must not end the backfill.
    client = FakeClient()
    client.add_chat(GROUP, history(GROUP_PEER, {250}))
    summary = await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    assert summary["ok"] and sorted(rows(GROUP_ID)) == list(range(1, 401))

    # Cutoff inside the first page: the old run at the bottom is left out, an outlier above is kept.
    group = fakes.basic_group(811, "Dates")
    peer = types.PeerChat(811)
    msgs = []
    for i in range(1, 401):
        when = now - dt.timedelta(days=90 - i / 10) if i < 370 else now - dt.timedelta(hours=1)
        if i == 385:
            when = now - dt.timedelta(days=100)
        msgs.append(types.Message(id=i, peer_id=peer, date=when, message=str(i), from_id=types.PeerUser(10)))
    other = FakeClient()
    other.add_chat(group, msgs)
    await sync.run_sync(client=other, settings=config.Settings(history_days=30))
    assert sorted(rows(-811)) == list(range(370, 401))
    assert covered(-811)


async def test_gap_older_than_the_cutoff_is_deferred_until_history_widens():
    now = dt.datetime.now(dt.timezone.utc)

    def at(i, days):
        return types.Message(id=i, peer_id=GROUP_PEER, date=now - dt.timedelta(days=days), message=str(i),
                             from_id=types.PeerUser(10))

    client = FakeClient()
    client.add_chat(GROUP, [at(i, 90) for i in range(1, 11)])
    await sync.run_sync(client=client, settings=everything())
    client.add_messages(GROUP_ID, [at(i, 60) for i in range(11, 201)] + [at(i, 1) for i in range(201, 401)])
    summary = await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    assert summary["ok"] and not summary["resumable"]
    assert sorted(rows(GROUP_ID)) == list(range(1, 11)) + list(range(201, 401))
    assert gaps(GROUP_ID) == [(10, 201, True)] and covered(GROUP_ID)

    client.requests.clear()
    await sync.run_sync(client=client, settings=config.Settings(history_days=30))
    assert client.requests == [], "a deferred gap costs nothing"

    await sync.run_sync(client=client, settings=everything())
    assert sorted(rows(GROUP_ID)) == list(range(1, 401)) and gaps(GROUP_ID) == []


async def test_messages_sent_as_a_channel_are_attributed_to_the_channel():
    client = team_client(1)
    news = fakes.supergroup(600, "News", broadcast=True)
    client.add_chat(news)
    client.add_messages(GROUP_ID, [
        types.Message(id=2, peer_id=GROUP_PEER, date=fakes.START, message="as channel", out=True,
                      from_id=types.PeerChannel(600)),
        types.Message(id=3, peer_id=GROUP_PEER, date=fakes.START, message="anonymous admin", out=True,
                      from_id=types.PeerChannel(500)),
        types.Message(id=4, peer_id=GROUP_PEER, date=fakes.START, message="as me", out=True,
                      from_id=types.PeerUser(1)),
    ])
    await sync.run_sync(client=client, settings=everything())
    stored = rows(GROUP_ID)
    assert stored[2]["sender_id"] == utils.get_peer_id(news) and stored[2]["out"] == 1
    assert stored[3]["sender_id"] == GROUP_ID
    assert stored[4]["sender_id"] == 1


async def test_predecessor_rows_keep_their_own_title_and_retry_monthly():
    client = upgraded_client()  # -77 was never joined: not in the dialog list, not readable
    await sync.run_sync(client=client, settings=everything())
    pred = one("SELECT * FROM chats WHERE chat_id=-77")
    assert pred["title"] == "Team (before upgrade)" and pred["migrated_to"] == GROUP_ID

    def checked(days_ago):
        when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).replace(microsecond=0).isoformat()
        conn = db.open_rw()
        conn.execute("UPDATE sync_state SET error=? WHERE chat_id=-77",
                     (f"inaccessible: ChatIdInvalidError (checked {when})",))
        conn.commit()
        conn.close()

    checked(5)
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert -77 not in history_peers(client), "not retried after a day"
    checked(31)
    client.requests.clear()
    await sync.run_sync(client=client, settings=everything())
    assert -77 in history_peers(client), "retried after a month"

    # A basic group that is in the dialog list keeps its own title and archive flag.
    other = FakeClient()
    other.users = [ALICE]
    other.add_chat(GROUP, [msg(GROUP_PEER, 1, "new era", sender=10, minutes=100)])
    other.full[GROUP_ID] = fakes.full_channel(500, migrated_from=78)
    other.add_chat(fakes.basic_group(78, "Old team", migrated_to=500),
                   [msg(types.PeerChat(78), 1, "old", sender=10)], archived=True)
    await sync.run_sync(client=other, settings=everything(include_archived=True))
    row = one("SELECT * FROM chats WHERE chat_id=-78")
    assert (row["title"], row["archived"], row["migrated_to"]) == ("Old team", 1, GROUP_ID)


async def test_long_flood_wait_in_the_chat_list_syncs_the_chats_listed_so_far():
    client = team_client(3)
    bob = fakes.user(20, "Bob")
    client.add_chat(bob, [msg(types.PeerUser(20), 1, "hi", minutes=0)])
    client.dialog_flood = (1, 120)  # above Telethon's 60 s threshold, below MAX_FLOOD_WAIT
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["chats"] == 1 and sorted(rows(GROUP_ID)) == [1, 2, 3] and not rows(20)

    client.dialog_flood = (0, 3600)  # nothing listed yet and a very long wait: stop
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["stopped"] == "flood_wait"

    client.dialog_flood = None
    await sync.run_sync(client=client, settings=everything())
    assert sorted(rows(20)) == [1]


# --- randomized multi-run property test ----------------------------------------------

async def test_random_runs_never_leave_holes_and_converge(monkeypatch):
    import random

    rng = random.Random(20261002)
    now = dt.datetime.now(dt.timezone.utc)
    clock = {"now": 0.0}
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock["now"])

    def at(i):  # dates rise with the id; ids from 700 up are "now"
        return now - dt.timedelta(hours=max(0, 700 - i))

    def message(peer, i):
        return types.Message(id=i, peer_id=peer, date=at(i), message=f"m{i}", from_id=types.PeerUser(10))

    client = FakeClient()
    client.users = [ALICE]
    chats: dict[int, object] = {}
    for entity, peer in [(fakes.supergroup(501, "A"), types.PeerChannel(501)),
                         (fakes.supergroup(502, "B"), types.PeerChannel(502)),
                         (fakes.basic_group(77, "C"), types.PeerChat(77))]:
        size = rng.randint(0, 450)
        client.add_chat(entity, [message(peer, i) for i in range(1, size + 1)])
        chats[utils.get_peer_id(entity)] = peer
    ever = {cid: {m.id for m in client.history[cid]} for cid in chats}
    client.on_request = tick(clock, 1)

    def visible(cid):
        return {m.id for m in client.history[cid] if not isinstance(m, types.MessageEmpty)} - client.hidden

    def stored_ids(cid):
        conn = db.connect(readonly=True)
        try:
            return {r[0] for r in conn.execute("SELECT message_id FROM messages WHERE chat_id=?", (cid,))}
        finally:
            conn.close()

    def check():
        for cid in chats:
            stored = stored_ids(cid)
            assert stored <= ever[cid], "nothing invented"
            st = one("SELECT * FROM sync_state WHERE chat_id=?", cid)
            if st is None or not st["newest_id"] or st["oldest_id"] is None:
                continue
            spans = gaps(cid)
            for i in visible(cid):
                if st["oldest_id"] <= i <= st["newest_id"]:
                    assert i in stored or any(lo < i < hi for lo, hi, _ in spans), (cid, i, spans)

    for _ in range(50):
        for cid, peer in chats.items():
            history = client.history[cid]
            top = max((m.id for m in history), default=0)
            if rng.random() < 0.6:
                k = rng.choice([1, 3, 40, 99, 101, 150, 260])
                history.extend(message(peer, i) for i in range(top + 1, top + k + 1))
                ever[cid].update(range(top + 1, top + k + 1))
            if history and rng.random() < 0.3:
                for victim in rng.sample(history, min(len(history), rng.randint(1, 5))):
                    history.remove(victim)
                    if rng.random() < 0.5:
                        history.append(types.MessageEmpty(id=victim.id, peer_id=peer))
            if history and rng.random() < 0.2:
                client.hidden.add(rng.choice(history).id)
        client.fail_on_request = len(client.requests) + rng.randint(1, 25) if rng.random() < 0.3 else None
        client.flood_on_request = (len(client.requests) + rng.randint(1, 25), 3600) if rng.random() < 0.1 else None
        clock["now"] = 0.0
        days = rng.choice([None, 3, 10, 40])
        await sync.run_sync(client=client, settings=config.Settings(history_days=days),
                            max_seconds=rng.choice([None, 3, 8, 20]))
        check()

    client.fail_on_request = client.flood_on_request = None
    for days in (10, None):
        for _ in range(5):
            clock["now"] = 0.0
            summary = await sync.run_sync(client=client, settings=config.Settings(history_days=days))
            check()
            if not summary["resumable"]:
                break
        else:
            pytest.fail("no convergence")
        edge = now - dt.timedelta(days=days) + dt.timedelta(hours=1) if days else None
        for cid in chats:
            stored = stored_ids(cid)
            wanted = {i for i in visible(cid) if edge is None or at(i) > edge}
            assert wanted <= stored, (days, cid, sorted(wanted - stored)[:10])
        assert all(not g[2] for cid in chats for g in gaps(cid)) or days is not None


# --- the MCP refresh tool and the sync lock --------------------------------------------

async def test_refresh_waits_for_the_sync_that_holds_the_lock(monkeypatch):
    """The started process exits with EXIT_BUSY: another sync took the lock in between."""
    import asyncio
    import sys

    from dotiphant import mcp_server as ms

    db.init()
    stale = {"state": "idle", "new_messages": 0, "started_at": "2026-01-01T00:00:00+00:00", "pid": None}
    config.write_private(config.status_path(), json.dumps(stale))
    real_popen = sync_status.subprocess.Popen
    monkeypatch.setattr(sync_status.subprocess, "Popen",
                        lambda args, **kw: real_popen([sys.executable, "-c", "raise SystemExit(75)"], **kw))
    lock = open(config.lock_path(), "a+")
    assert sync_status.try_lock(lock)

    async def other_sync_finishes():
        await asyncio.sleep(1.0)
        done = dict(stale, started_at=sync._now(), finished_at=sync._now(), new_messages=4)
        config.write_private(config.status_path(), json.dumps(done))
        sync_status.unlock(lock)
        lock.close()

    finisher = asyncio.ensure_future(other_sync_finishes())
    out = await ms.refresh(wait_seconds=20)
    await finisher
    assert "already running" in out and "4 new messages" in out and "exited" not in out


# --- profile photos -------------------------------------------------------------------

def avatar_row(chat_id):
    return one("SELECT avatar_path, photo_id FROM chats WHERE chat_id=?", chat_id)


def avatar_files(home):
    folder = home / "media" / "avatars"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


async def test_profile_photos_download_only_on_change_and_replace_the_old_file(home):
    group = fakes.supergroup(700, "Pics")
    group.photo = fakes.chat_photo(11)
    bob = fakes.user(20, "Bob")
    bob.photo = fakes.user_photo(21)
    carol = fakes.user(30, "Carol")  # no photo at all
    client = FakeClient()
    client.add_chat(group, [msg(types.PeerChannel(700), 1, "hi")])
    client.add_chat(bob, [msg(types.PeerUser(20), 1, "hey")])
    client.add_chat(carol, [msg(types.PeerUser(30), 1, "yo")])
    gid = utils.get_peer_id(group)

    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and summary["avatars"] == {"downloaded": 2, "removed": 0, "failed": 0}
    assert sorted(client.photo_downloads) == [(gid, 11, False), (20, 21, False)], "small version only"
    assert tuple(avatar_row(gid)) == (f"avatars/{gid}-11.jpg", 11)
    assert tuple(avatar_row(30)) == (None, None)
    assert avatar_files(home) == sorted([f"{gid}-11.jpg", "20-21.jpg"])
    assert (home / "media" / "avatars" / f"{gid}-11.jpg").read_bytes().startswith(b"\xff\xd8")

    client.photo_downloads.clear()
    await sync.run_sync(client=client, settings=everything())
    assert client.photo_downloads == [], "an unchanged photo costs no request"

    group.photo = fakes.chat_photo(12)
    await sync.run_sync(client=client, settings=everything())
    assert client.photo_downloads == [(gid, 12, False)]
    assert tuple(avatar_row(gid)) == (f"avatars/{gid}-12.jpg", 12)
    assert avatar_files(home) == sorted([f"{gid}-12.jpg", "20-21.jpg"]), "the old file is removed"

    (home / "media" / "avatars" / "20-21.jpg").unlink()
    client.photo_downloads.clear()
    await sync.run_sync(client=client, settings=everything())
    assert client.photo_downloads == [(20, 21, False)], "a missing file is fetched again"


async def test_removed_profile_photo_clears_the_row_and_the_file(home):
    bob = fakes.user(20, "Bob")
    bob.photo = fakes.user_photo(21)
    client = FakeClient()
    client.add_chat(bob, [msg(types.PeerUser(20), 1, "hey")])
    await sync.run_sync(client=client, settings=everything())
    assert avatar_files(home) == ["20-21.jpg"]
    # A stray file next to it, and one outside media/avatars, are never touched.
    (home / "media" / "avatars" / "keep.txt").write_text("x")
    (home / "media" / "20-21.jpg").write_text("x")

    bob.photo = types.UserProfilePhotoEmpty()
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["avatars"]["removed"] == 1
    assert tuple(avatar_row(20)) == (None, None)
    assert avatar_files(home) == ["keep.txt"]
    assert (home / "media" / "20-21.jpg").exists()


async def test_profile_photos_skip_excluded_chats_and_survive_errors(home):
    a = fakes.supergroup(701, "Private now")
    a.photo = fakes.chat_photo(1)
    b = fakes.supergroup(702, "Excluded")
    b.photo = fakes.chat_photo(2)
    c = fakes.user(20, "Gone")
    c.photo = fakes.user_photo(3)
    d = fakes.user(21, "Fine")
    d.photo = fakes.user_photo(4)
    client = FakeClient()
    for i, e in enumerate((a, b, c, d)):
        client.add_chat(e, [msg(utils.get_peer(utils.get_peer_id(e)), 1, "x", minutes=i)])
    aid, bid = utils.get_peer_id(a), utils.get_peer_id(b)
    client.photo_errors = {aid: errors.ChannelPrivateError(request=None), 20: errors.PhotoInvalidError(request=None)}

    summary = await sync.run_sync(client=client, settings=everything(excluded_chats=[bid]))
    assert summary["ok"], "a photo that cannot be read does not fail the sync"
    assert summary["avatars"] == {"downloaded": 1, "removed": 0, "failed": 2}
    assert [d[0] for d in client.photo_downloads] == [21]
    assert tuple(avatar_row(aid)) == (None, None) and tuple(avatar_row(20)) == (None, None)
    assert avatar_files(home) == ["21-4.jpg"]

    # By id, an excluded chat still gets no photo.
    await sync.run_sync([bid], client=client, settings=everything(excluded_chats=[bid]))
    assert [d[0] for d in client.photo_downloads] == [21]


async def test_profile_photos_come_after_new_messages_and_respect_the_time_limit(monkeypatch, home):
    clock = {"now": 0.0}
    monkeypatch.setattr(sync.time, "monotonic", lambda: clock["now"])
    client = FakeClient()
    people = []
    for uid in range(20, 26):
        u = fakes.user(uid, f"P{uid}")
        u.photo = fakes.user_photo(uid * 10)
        people.append(u)
        client.add_chat(u, [msg(types.PeerUser(uid), 1, "x", minutes=uid)])
    order = []
    client.on_request = lambda req: (order.append("photo" if isinstance(req, tuple) else "history"),
                                     clock.__setitem__("now", clock["now"] + 10))
    summary = await sync.run_sync(client=client, settings=everything(), max_seconds=85)
    assert summary["stopped"] == "time_limit"
    assert order[:6] == ["history"] * 6, "every chat gets its messages before any photo"
    assert len(client.photo_downloads) == 3, "photos count toward the time limit"
    assert all(len(rows(u)) == 1 for u in range(20, 26))

    clock["now"] = 0.0
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and len(client.photo_downloads) == 6


async def test_profile_photo_flood_wait_is_waited_out_or_stops_the_run(home):
    bob = fakes.user(20, "Bob")
    bob.photo = fakes.user_photo(21)
    client = FakeClient()
    client.add_chat(bob, [msg(types.PeerUser(20), 1, "hey")])
    floods = []

    def flood(req):
        if isinstance(req, tuple) and floods:
            raise errors.FloodWaitError(request=None, capture=floods.pop(0))

    client.on_request = flood
    floods.append(0)
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["ok"] and client.photo_downloads == [(20, 21, False)], "a short wait is waited out"

    bob.photo = fakes.user_photo(22)
    floods.append(3600)
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["stopped"] == "flood_wait"
    assert tuple(avatar_row(20)) == ("avatars/20-21.jpg", 21), "the stored photo stays until a new one arrives"


async def test_run_avatars_fetches_photos_without_messages(home):
    bob = fakes.user(20, "Bob")
    bob.photo = fakes.user_photo(21)
    eve = fakes.user(22, "Eve")
    eve.photo = fakes.user_photo(23)
    client = FakeClient()
    client.add_chat(bob, [msg(types.PeerUser(20), 1, "hey")])
    client.add_chat(eve, [msg(types.PeerUser(22), 1, "hi")])
    result = await sync.run_avatars([20, 99], client=client, settings=everything())
    assert result["downloaded"] == 1 and result["missing"] == [99] and result["chats"] == 1
    assert history_peers(client) == [] and one("SELECT count(*) FROM messages")[0] == 0
    assert not config.status_path().exists()

    result = await sync.run_avatars(client=client, settings=everything())
    assert result["downloaded"] == 1, "only Eve's photo is new"
    result = await sync.run_avatars(client=client, settings=everything(), refresh=True)
    assert result["downloaded"] == 2

    with open(config.lock_path(), "a+") as f:
        assert sync_status.try_lock(f)
        with pytest.raises(sync.SyncBusy):
            await sync.run_avatars(client=client, settings=everything())
        sync_status.unlock(f)


# --- one archive, one Telegram account ------------------------------------------------

ACCOUNT_A = types.User(id=1, first_name="Alice", is_self=True)
ACCOUNT_B = types.User(id=2, first_name="Bob", is_self=True)
CAROL = fakes.user(10, "Carol")
CAROL_PEER = types.PeerUser(10)


def carol_client(me, texts):
    """An account whose private chat with Carol holds `texts` as ids 1, 2, ..."""
    client = FakeClient(me=me)
    client.users = [CAROL]
    client.add_chat(CAROL, [msg(CAROL_PEER, i, t, sender=10) for i, t in enumerate(texts, 1)])
    return client


@pytest.mark.parametrize("b_messages", [6, 2], ids=["would-overwrite", "would-skip"])
async def test_another_account_cannot_sync_into_the_archive(b_messages):
    # Both accounts chat with Carol: the private chat id is Carol's user id for both, and
    # the message ids overlap. B's higher top would rewrite A's rows as "edits"; a lower
    # top would skip B's messages without a word.
    await sync.run_sync(client=carol_client(ACCOUNT_A, [f"A {i}" for i in range(1, 5)]), settings=everything())
    before = rows(10)
    other = carol_client(ACCOUNT_B, [f"B {i}" for i in range(1, b_messages + 1)])
    with pytest.raises(sync.OtherAccount) as err:
        await sync.run_sync(client=other, settings=everything())
    assert str(err.value) == db.OTHER_ACCOUNT
    assert "DOTIPHANT_HOME" in str(err.value)
    assert other.requests == [], "nothing is read for the other account"
    assert rows(10) == before
    assert one("SELECT count(*) FROM message_versions")[0] == 0
    assert one("SELECT value FROM meta WHERE key='owner_id'")[0] == "1"
    status = sync_status.read_status()
    assert status["state"] == "error" and status["message"] == db.OTHER_ACCOUNT

    # The owner syncs as before.
    summary = await sync.run_sync(client=carol_client(ACCOUNT_A, [f"A {i}" for i in range(1, 6)]),
                                  settings=everything())
    assert summary["ok"] and summary["new_messages"] == 1


async def test_other_account_is_refused_for_the_chat_list_and_photos_too():
    await sync.run_sync(client=carol_client(ACCOUNT_A, ["hi"]), settings=everything())
    other = carol_client(ACCOUNT_B, ["hey"])
    with pytest.raises(sync.OtherAccount):
        await sync.run_inventory(client=other, settings=everything())
    with pytest.raises(sync.OtherAccount):
        await sync.run_avatars(client=other, settings=everything())
    assert one("SELECT count(*) FROM chats")[0] == 1


async def test_archive_without_an_owner_takes_the_first_account():
    db.init()  # an import-only archive has no owner_id
    summary = await sync.run_sync(client=carol_client(ACCOUNT_B, ["hey"]), settings=everything())
    assert summary["ok"]
    assert one("SELECT value FROM meta WHERE key='owner_id'")[0] == "2"


def test_cli_sync_with_another_account_exits_with_the_auth_code(monkeypatch, capsys):
    from dotiphant import cli, telegram

    db.init()
    conn = db.open_rw()
    db.set_meta(conn, "owner_id", "1")
    conn.commit()
    conn.close()
    other = carol_client(ACCOUNT_B, ["hey"])
    monkeypatch.setattr(telegram, "make_client", lambda **kw: other)
    assert cli.run_sync_cli(None, settings=everything(), quiet=True) == cli.EXIT_AUTH
    err = capsys.readouterr().err
    assert db.OTHER_ACCOUNT in err and "dotiphant setup" not in err
    assert other.requests == []


# --- partial failures ----------------------------------------------------------------

async def test_partial_failure_is_counted_in_the_status():
    client = team_client(3)
    other = fakes.supergroup(600, "Other")
    client.add_chat(other, [msg(types.PeerChannel(600), i, f"o{i}", sender=10) for i in range(1, 4)])
    client.fail_on_request = 2  # one chat's first page fails
    summary = await sync.run_sync(client=client, settings=everything())
    assert len(summary["errors"]) == 1
    status = sync_status.read_status()
    assert status["state"] == "idle" and status["errors"] == 1
    assert status["message"] == "1 chat(s) could not be read; they are retried next time."
    assert [f["chat_id"] for f in status["failed_chats"]] == [summary["errors"][0]["chat_id"]]
    assert status["failed_chats"][0]["error"] == "RuntimeError"

    client.fail_on_request = None
    await sync.run_sync(client=client, settings=everything())
    status = sync_status.read_status()
    assert status["errors"] == 0 and status["failed_chats"] == [] and status["message"] is None


# --- edits in quiet chats ------------------------------------------------------------

def edit(client, mid, text):
    client.history[GROUP_ID] = [m for m in client.history[GROUP_ID] if m.id != mid] + [
        msg(GROUP_PEER, mid, text, sender=10, edit_date=fakes.START + dt.timedelta(hours=9))]


async def test_syncing_one_chat_picks_up_edits_without_new_messages():
    client = team_client(3)
    await sync.run_sync(client=client, settings=everything())
    edit(client, 2, "hello 2, corrected")

    # A run over all chats stays cheap: the dialog list shows nothing new, so no page is read.
    before = len(history_peers(client))
    summary = await sync.run_sync(client=client, settings=everything())
    assert len(history_peers(client)) == before
    assert summary["edited_messages"] == 0 and rows(GROUP_ID)[2]["text"] == "hello 2"

    # Syncing the chat on its own reads its latest page again.
    summary = await sync.run_sync([GROUP_ID], client=client, settings=everything())
    assert summary["ok"] and summary["edited_messages"] == 1 and summary["new_messages"] == 0
    assert rows(GROUP_ID)[2]["text"] == "hello 2, corrected"
    assert one("SELECT text FROM message_versions WHERE chat_id=? AND message_id=2", GROUP_ID)[0] == "hello 2"
    state = one("SELECT newest_id, backfill_done FROM sync_state WHERE chat_id=?", GROUP_ID)
    assert tuple(state) == (3, 1)
    assert one("SELECT count(*) FROM messages")[0] == 3


async def test_new_message_brings_edits_on_the_latest_page_only():
    client = team_client(250)
    await sync.run_sync(client=client, settings=everything())
    edit(client, 249, "recent, corrected")
    edit(client, 50, "old, corrected")
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 251, "new", sender=10)])
    summary = await sync.run_sync(client=client, settings=everything())
    assert summary["new_messages"] == 1 and summary["edited_messages"] == 1
    assert rows(GROUP_ID)[249]["text"] == "recent, corrected"
    assert rows(GROUP_ID)[50]["text"] == "hello 50", "older messages are not read again"


async def test_download_through_another_account_is_refused(home):
    client = team_client(1)
    client.add_messages(GROUP_ID, [msg(GROUP_PEER, 2, "", sender=10, media=document(2, "a.pdf", 10))])
    await sync.run_sync(client=client, settings=everything())
    other = team_client(1)
    other.me = ACCOUNT_B
    other.add_messages(GROUP_ID, [msg(GROUP_PEER, 2, "", sender=10, media=document(2, "b.pdf", 10))])
    with pytest.raises(sync.OtherAccount):
        await sync.download_media(GROUP_ID, client=other)
    assert other.downloads == []
    assert one("SELECT media_path FROM messages WHERE chat_id=? AND message_id=2", GROUP_ID)[0] is None
    assert (await sync.download_media(GROUP_ID, client=client))["downloaded"] == 1


async def test_sync_after_a_single_chat_import_with_me_checks_that_account(tmp_path):
    from dotiphant import importer

    folder = tmp_path / "single"
    folder.mkdir()
    (folder / "result.json").write_text(json.dumps({"name": "Carol", "type": "personal_chat", "id": 10,
                                                    "messages": []}))
    importer.import_export(folder, me=1)
    with pytest.raises(sync.OtherAccount):
        await sync.run_sync(client=carol_client(ACCOUNT_B, ["hey"]), settings=everything())
