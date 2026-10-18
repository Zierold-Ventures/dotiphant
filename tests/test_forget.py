"""`dotiphant forget` removes one chat completely and keeps it out of future syncs."""
import pytest

from dotiphant import cli, config, db, forget, query


@pytest.fixture
def archive():
    db.init()
    c = db.connect()
    for cid, title, migrated in [(-1001, "Acme x Northwind", None), (-55, "Acme old", -1001), (42, "Dana", None)]:
        c.execute("INSERT INTO chats(chat_id,kind,title,migrated_to,first_seen_at,updated_at) VALUES(?,?,?,?,'x','x')",
                  (cid, "group", title, migrated))
    for cid, mid, text in [(-1001, 1, "secret oracle plan"), (-55, 1, "older oracle note"), (42, 1, "oracle lunch")]:
        c.execute("INSERT INTO messages(chat_id,message_id,date,text) VALUES(?,?,'2026-09-01T00:00:00+00:00',?)",
                  (cid, mid, text))
    c.execute("INSERT INTO sync_state(chat_id,newest_id) VALUES(-1001,1)")
    c.commit()
    c.close()
    folder = config.media_dir() / "-1001"
    folder.mkdir(parents=True)
    (folder / "1-photo.jpg").write_bytes(b"x")


def test_forget_removes_chat_messages_search_and_media(archive):
    result = forget.forget_chat("Acme x Northwind")
    assert result["messages_removed"] == 2
    conn = db.connect(readonly=True)
    assert [c["chat_id"] for c in query.list_chats(conn)] == [42]
    assert [h["chat_id"] for h in query.search(conn, "oracle")] == [42]
    assert not (config.media_dir() / "-1001").exists()
    assert set(config.Settings.load().excluded_chats) == {-1001, -55}
    with pytest.raises(Exception):
        db.connect().execute("DELETE FROM messages")  # the guard is back


def test_forget_removes_the_profile_photo_only_of_that_chat(archive):
    avatars = config.media_dir() / "avatars"
    avatars.mkdir()
    for name in ("-1001-7.jpg", "-55-3.jpg", "42-9.jpg", "-10011-2.jpg"):
        (avatars / name).write_bytes(b"x")
    forget.forget_chat("Acme x Northwind")
    assert sorted(p.name for p in avatars.iterdir()) == ["-10011-2.jpg", "42-9.jpg"]


def test_forget_cli_asks_and_defaults_to_no(archive, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    assert cli.main(["forget", "Dana"]) == 0
    assert "Nothing deleted" in capsys.readouterr().out
    assert cli.main(["forget", "Dana", "--yes"]) == 0
    conn = db.connect(readonly=True)
    assert all(c["chat_id"] != 42 for c in query.list_chats(conn))


def test_forget_refuses_while_sync_runs(archive):
    from dotiphant import status

    with open(config.data_dir() / "sync.lock", "a+") as f:
        assert status.try_lock(f)
        with pytest.raises(forget.Busy):
            forget.forget_chat("Dana")
        status.unlock(f)


def add_senders():
    c = db.connect()
    with c:
        db.set_meta(c, "owner_id", "1")
        for pid, name, username in [(1, "Me Owner", "me_owner"), (7, "Zelda Quartz", "zquartz"),
                                    (8, "Yuri Both", None), (42, "Dana Private", "dana_p")]:
            c.execute("INSERT INTO senders(peer_id,name,username) VALUES(?,?,?)", (pid, name, username))
        for cid, mid, sender in [(-1001, 2, 1), (-1001, 3, 7), (-1001, 4, 8), (42, 2, 8), (42, 3, 42)]:
            c.execute("INSERT INTO messages(chat_id,message_id,sender_id,date,text)"
                      " VALUES(?,?,?,'2026-09-02T00:00:00+00:00','hi')", (cid, mid, sender))
    c.close()


def senders():
    c = db.connect(readonly=True)
    try:
        return {r[0]: r[1] for r in c.execute("SELECT peer_id, name FROM senders")}
    finally:
        c.close()


def test_forget_removes_people_who_wrote_only_there(archive):
    add_senders()
    forget.forget_chat("Acme x Northwind")
    # Zelda wrote only in the forgotten group; Yuri also writes in Dana's chat; the owner stays.
    assert senders() == {1: "Me Owner", 8: "Yuri Both", 42: "Dana Private"}
    forget.forget_chat("Dana")
    assert senders() == {1: "Me Owner"}  # the other person of a private chat goes with it
    c = db.connect(readonly=True)
    for table in ("chat_stats", "chat_senders"):
        assert c.execute(f"SELECT count(*) FROM {table} WHERE chat_id IN (-1001, -55, 42)").fetchone()[0] == 0
    c.close()


def test_forget_leaves_no_trace_in_the_files(archive):
    add_senders()
    result = forget.forget_chat("Acme x Northwind")
    assert result["log_cleared"]
    wal = config.db_path().with_name(config.db_path().name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    raw = config.db_path().read_bytes()
    for word in (b"secret", b"older", b"Zelda", b"zquartz", b"Northwind"):
        assert word not in raw, word
    assert b"oracle" in raw and b"lunch" in raw  # the other chat is untouched


def test_forget_reports_a_log_it_could_not_clear(archive, monkeypatch):
    monkeypatch.setattr(forget, "CHECKPOINT_WAIT_MS", 0)
    reader = db.connect(readonly=True)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM messages").fetchone()  # holds an old snapshot open
    try:
        assert forget.forget_chat("Dana")["log_cleared"] is False
    finally:
        reader.close()
