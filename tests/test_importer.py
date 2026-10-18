"""Telegram Desktop JSON export import: mapping, idempotence, owner detection, media copy."""
import json

import pytest

from dotiphant import cli, config, db, importer, query

OWNER = 777
DANA = 42
GROUP_BARE = 1234567890
GROUP_ID = -1001234567890


def _ts(day: int, hour: int = 10, minute: int = 0) -> tuple[str, str]:
    import datetime as dt

    t = dt.datetime(2024, 1, day, hour, minute, tzinfo=dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S"), str(int(t.timestamp()))


def msg(mid, day, minute=0, **fields):
    local, unix = _ts(day, 10, minute)
    base = {"id": mid, "type": "message", "date": local, "date_unixtime": unix, "text": "", "text_entities": []}
    base.update(fields)
    return base


def service(mid, day, action, actor="Dana Lee", actor_id=f"user{DANA}", **fields):
    local, unix = _ts(day)
    base = {"id": mid, "type": "service", "date": local, "date_unixtime": unix, "actor": actor,
            "actor_id": actor_id, "action": action, "text": "", "text_entities": []}
    base.update(fields)
    return base


def supergroup_chat():
    edited_local, edited_unix = _ts(3, 11)
    return {
        "name": "Acme x Northwind", "type": "private_supergroup", "id": GROUP_BARE,
        "messages": [
            msg(-999999990, 1, text="from the old basic group"),
            service(1, 1, "migrate_from_group", title="Acme old"),
            service(2, 1, "invite_members", members=["Sam Park"]),
            msg(3, 2, **{"from": "Dana Lee", "from_id": f"user{DANA}",
                         "text": ["Can you ship the ", {"type": "bold", "text": "oracle feed"}, " by Friday?"]}),
            msg(4, 2, 5, **{"from": "Elowen", "from_id": f"user{OWNER}", "text": "Yes, Zephyrlink adapter tomorrow",
                            "reply_to_message_id": 3, "edited": edited_local, "edited_unixtime": edited_unix}),
            msg(5, 3, **{"from": "Dana Lee", "from_id": f"user{DANA}", "photo": "photos/photo_1.jpg",
                         "photo_file_size": 11, "width": 10, "height": 10, "text": "whiteboard"}),
            msg(6, 3, 1, **{"from": "Dana Lee", "from_id": f"user{DANA}", "file": "voice_messages/a.ogg",
                            "media_type": "voice_message", "mime_type": "audio/ogg"}),
            msg(7, 3, 2, **{"from": "Dana Lee", "from_id": f"user{DANA}",
                            "file": "(File not included. Change data exporting settings to download.)",
                            "file_name": "q3.pdf", "mime_type": "application/pdf", "file_size": 2048}),
            msg(8, 4, **{"from": "Dana Lee", "from_id": f"user{DANA}", "forwarded_from": "Treasury Bot",
                         "poll": {"question": "Ship it?", "closed": False, "total_voters": 0, "answers": []}}),
        ],
    }


def personal_chat():
    return {
        "name": "Dana Lee", "type": "personal_chat", "id": DANA,
        "messages": [
            msg(10, 5, **{"from": "Dana Lee", "from_id": f"user{DANA}", "text": "Cafe meeting tomorrow?"}),
            msg(11, 5, 3, **{"from": "Elowen", "from_id": f"user{OWNER}", "text": "Sure, 9am"}),
            service(12, 6, "phone_call", actor="Elowen", actor_id=f"user{OWNER}", duration_seconds=60),
        ],
    }


def full_export():
    return {
        "about": "Here is the data you requested.",
        "personal_information": {"user_id": OWNER, "first_name": "Elowen", "last_name": "Q", "phone_number": "+1"},
        "chats": {"about": "", "list": [supergroup_chat(), personal_chat(),
                                        {"name": "Weird", "type": "something_new", "id": 5, "messages": []}]},
        "left_chats": {"about": "", "list": []},
    }


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTIPHANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOTIPHANT_SECRETS", "file")
    return config.data_dir()


def write_export(folder, data, files=()):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(data), encoding="utf-8")
    for rel in files:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(b"x" * 11)
    return folder


def rows(chat_id):
    conn = db.connect()
    try:
        return {r["message_id"]: dict(r) for r in conn.execute("SELECT * FROM messages WHERE chat_id=?", (chat_id,))}
    finally:
        conn.close()


def test_mapping_helpers():
    assert importer.marked_chat_id("private_supergroup", GROUP_BARE) == GROUP_ID
    assert importer.marked_chat_id("public_channel", 5) == -1000000000005
    assert importer.marked_chat_id("private_group", 55) == -55
    assert importer.marked_chat_id("personal_chat", 42) == 42
    assert importer.peer_id("user42") == 42
    assert importer.peer_id("chat55") == -55
    assert importer.peer_id("channel5") == -1000000000005
    assert importer.peer_id(None) is None
    assert importer.plain_text(["a ", {"type": "link", "text": "b"}, "c"]) == "a bc"


def test_full_export_rows_and_owner(tmp_path):
    folder = write_export(tmp_path / "export", full_export())
    result = importer.import_export(folder)
    assert result["owner_id"] == OWNER
    by_title = {c["title"]: c for c in result["chats"]}
    assert by_title["Acme x Northwind"]["imported"] == 8
    assert by_title["Acme x Northwind"]["skipped_pre_upgrade"] == 1
    assert by_title["Dana Lee"]["imported"] == 3
    assert "unsupported" in by_title["Weird"]["skipped"]

    g = rows(GROUP_ID)
    assert g[3]["text"] == "Can you ship the oracle feed by Friday?" and g[3]["out"] == 0 and g[3]["sender_id"] == DANA
    assert g[3]["date"] == "2024-01-02T10:00:00+00:00"
    assert g[4]["out"] == 1 and g[4]["reply_to_id"] == 3 and g[4]["edit_date"] == "2024-01-03T11:00:00+00:00"
    assert (g[5]["kind"], g[5]["media_type"], g[5]["media_size"], g[5]["media_path"]) == ("media", "photo", 11, None)
    assert g[6]["media_type"] == "voice"
    assert (g[7]["media_type"], g[7]["media_name"], g[7]["media_size"]) == ("document", "q3.pdf", 2048)
    assert (g[8]["media_type"], g[8]["media_name"], g[8]["forwarded_from"]) == ("poll", "Ship it?", "Treasury Bot")
    assert g[1]["kind"] == "service" and g[1]["service_text"] == 'Upgraded from the basic group "Acme old"'
    assert g[2]["service_text"] == "Dana Lee added Sam Park"
    assert rows(DANA)[12]["service_text"] == "Elowen made a call" and rows(DANA)[12]["out"] == 1

    conn = db.connect()
    try:
        chat = conn.execute("SELECT * FROM chats WHERE chat_id=?", (GROUP_ID,)).fetchone()
        assert (chat["kind"], chat["last_message_at"]) == ("supergroup", "2024-01-04T10:00:00+00:00")
        assert conn.execute("SELECT kind FROM chats WHERE chat_id=?", (DANA,)).fetchone()[0] == "private"
        assert conn.execute("SELECT name FROM senders WHERE peer_id=?", (OWNER,)).fetchone()[0] == "Elowen Q"
        assert conn.execute("SELECT count(*) FROM sync_state").fetchone()[0] == 0
        hits = query.search(conn, "oracle")
        assert [(h["chat_id"], h["message_id"]) for h in hits] == [(GROUP_ID, 3)]
        assert query.search(conn, "q3.pdf")[0]["message_id"] == 7
        msgs = query.get_messages(conn, GROUP_ID)
        assert [m["message_id"] for m in msgs] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert msgs[3]["sender"] == "Elowen Q"
        md = query.to_markdown(query.chat_info(conn, GROUP_ID), msgs)
        assert "> Reply to Dana Lee: Can you ship the oracle feed by Friday?" in md
        assert "[poll: Ship it?]" in md and "_(edited 2024-01-03 11:00)_" in md
    finally:
        conn.close()


def test_reimport_is_idempotent_and_keeps_live_rows(tmp_path):
    folder = write_export(tmp_path / "export", full_export())
    db.init()
    conn = db.connect()
    conn.execute("INSERT INTO messages(chat_id, message_id, sender_id, date, text) VALUES(?,?,?,?,?)",
                 (DANA, 10, DANA, "2024-01-05T10:00:00+00:00", "live text wins"))
    conn.commit()
    conn.close()
    first = importer.import_export(folder / "result.json")
    dana = next(c for c in first["chats"] if c["chat_id"] == DANA)
    assert (dana["imported"], dana["already_present"]) == (2, 1)
    assert rows(DANA)[10]["text"] == "live text wins"
    second = importer.import_export(folder)
    assert all(c.get("imported", 0) == 0 for c in second["chats"])
    conn = db.connect()
    try:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 11
        assert conn.execute("SELECT count(*) FROM message_versions").fetchone()[0] == 0
        assert len(query.search(conn, "Cafe")) == 0 and len(query.search(conn, "live")) == 1
    finally:
        conn.close()


def test_single_chat_export_owner_needs_me(tmp_path):
    folder = write_export(tmp_path / "single", personal_chat())
    result = importer.import_export(folder)
    assert result["owner_id"] is None
    assert rows(DANA)[11]["out"] == 0
    other = write_export(tmp_path / "single2", {**personal_chat(), "name": "Dana", "id": 43})
    importer.import_export(other, me=OWNER)
    assert rows(43)[11]["out"] == 1
    # The CLI says "pass --me next time": importing the same export again with it fixes the rows.
    importer.import_export(folder, me=OWNER)
    assert rows(DANA)[11]["out"] == 1
    assert rows(DANA)[10]["out"] == 0


def test_owner_from_earlier_live_sync(tmp_path):
    db.init()
    conn = db.connect()
    db.set_meta(conn, "owner_id", str(OWNER))
    conn.commit()
    conn.close()
    importer.import_export(write_export(tmp_path / "single", personal_chat()))
    assert rows(DANA)[11]["out"] == 1


def test_copy_media(tmp_path):
    folder = write_export(tmp_path / "export", full_export(), files=["photos/photo_1.jpg"])
    result = importer.import_export(folder, copy_media=True)
    g = next(c for c in result["chats"] if c["chat_id"] == GROUP_ID)
    assert g["media_copied"] == 1 and g["media_missing"] == 2  # voice file absent, pdf not included
    path = rows(GROUP_ID)[5]["media_path"]
    assert path == f"{GROUP_ID}/5-photo_1.jpg"
    assert (config.media_dir() / path).read_bytes() == b"x" * 11
    again = importer.import_export(folder, copy_media=True)
    assert next(c for c in again["chats"] if c["chat_id"] == GROUP_ID)["media_copied"] == 0


def test_copy_media_rejects_paths_outside_export(tmp_path):
    (tmp_path / "secret.txt").write_text("no")
    data = personal_chat()
    data["messages"][0]["file"] = "../secret.txt"
    folder = write_export(tmp_path / "export", data)
    result = importer.import_export(folder, copy_media=True)
    assert result["chats"][0]["media_missing"] == 1
    assert rows(DANA)[10]["media_path"] is None


def test_forum_topics(tmp_path):
    chat = {"name": "Forum", "type": "public_supergroup", "id": 99, "messages": [
        service(5, 1, "topic_created", title="Launch"),
        msg(6, 1, 1, **{"from": "Dana Lee", "from_id": f"user{DANA}", "text": "in topic", "reply_to_message_id": 5}),
        msg(7, 1, 2, **{"from": "Dana Lee", "from_id": f"user{DANA}", "text": "reply", "reply_to_message_id": 6}),
        msg(8, 1, 3, **{"from": "Dana Lee", "from_id": f"user{DANA}", "text": "general"}),
    ]}
    importer.import_export(write_export(tmp_path / "forum", chat))
    r = rows(-1000000000099)
    assert (r[5]["topic_id"], r[6]["topic_id"], r[6]["reply_to_id"]) == (5, 5, None)
    assert (r[7]["topic_id"], r[7]["reply_to_id"], r[8]["topic_id"]) == (5, 6, 1)
    conn = db.connect()
    try:
        assert conn.execute("SELECT is_forum FROM chats WHERE chat_id=-1000000000099").fetchone()[0] == 1
        assert query.get_messages(conn, -1000000000099)[1]["topic"] == "Launch"
    finally:
        conn.close()


def test_bad_path_and_not_an_export(tmp_path):
    with pytest.raises(importer.ExportFormatError):
        importer.import_export(tmp_path / "missing")
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "result.json").write_text("[1, 2]")
    with pytest.raises(importer.ExportFormatError):
        importer.import_export(tmp_path / "x")


def test_cli_import(tmp_path, capsys):
    folder = write_export(tmp_path / "export", full_export())
    assert cli.main(["import", str(folder)]) == 0
    out = capsys.readouterr().out
    assert "Acme x Northwind (supergroup, -1001234567890): 8 imported" in out
    assert "Imported 11 messages from 3 chats" in out
    assert cli.main(["import", str(tmp_path / "nope")]) == 1


def _owner():
    conn = db.connect(readonly=True)
    try:
        return db.get_meta(conn, "owner_id")
    finally:
        conn.close()


def test_full_export_claims_the_archive_for_its_account(tmp_path):
    importer.import_export(write_export(tmp_path / "export", full_export()))
    assert _owner() == str(OWNER)
    # A later sync, or another export, of a different account is refused.
    other = {**full_export(), "personal_information": {"user_id": 2, "first_name": "Bob"}}
    with pytest.raises(db.OtherAccount) as err:
        importer.import_export(write_export(tmp_path / "other", other))
    assert str(err.value) == db.OTHER_ACCOUNT_IMPORT
    assert _owner() == str(OWNER)


def test_import_into_an_archive_of_another_account_is_refused(tmp_path):
    db.init()
    conn = db.connect()
    db.set_meta(conn, "owner_id", "2")
    conn.commit()
    conn.close()
    with pytest.raises(db.OtherAccount):
        importer.import_export(write_export(tmp_path / "export", full_export()))
    # A single-chat export names no account; --me is checked the same way.
    with pytest.raises(db.OtherAccount):
        importer.import_export(write_export(tmp_path / "single", personal_chat()), me=OWNER)
    assert rows(DANA) == {} and rows(GROUP_ID) == {}
    importer.import_export(write_export(tmp_path / "single", personal_chat()), me=2)
    assert rows(DANA)


def test_cli_import_of_another_account_uses_the_auth_exit_code(tmp_path, capsys):
    db.init()
    conn = db.connect()
    db.set_meta(conn, "owner_id", "2")
    conn.commit()
    conn.close()
    assert cli.main(["import", str(write_export(tmp_path / "export", full_export()))]) == cli.EXIT_AUTH
    assert db.OTHER_ACCOUNT_IMPORT in capsys.readouterr().err


def _set_owner(account_id):
    db.init()
    conn = db.connect()
    db.set_meta(conn, "owner_id", str(account_id))
    conn.commit()
    conn.close()


def test_full_export_with_matching_me_records_the_owner(tmp_path):
    importer.import_export(write_export(tmp_path / "export", full_export()), me=OWNER)
    assert _owner() == str(OWNER)


def test_me_cannot_override_the_account_an_export_declares(tmp_path):
    _set_owner(2)
    with pytest.raises(db.OtherAccount):
        importer.import_export(write_export(tmp_path / "export", full_export()), me=2)
    assert rows(DANA) == {} and rows(GROUP_ID) == {}
    assert _owner() == "2"


def test_declared_owner_and_me_must_agree_on_an_empty_archive(tmp_path):
    with pytest.raises(db.OtherAccount):
        importer.import_export(write_export(tmp_path / "export", full_export()), me=2)
    assert rows(DANA) == {} and rows(GROUP_ID) == {}
    assert _owner() is None


def test_single_chat_export_with_me_records_the_owner(tmp_path):
    importer.import_export(write_export(tmp_path / "single", personal_chat()), me=OWNER)
    assert _owner() == str(OWNER)
    assert rows(DANA)[11]["out"] == 1


def test_single_chat_export_with_me_matching_the_stored_owner(tmp_path):
    _set_owner(OWNER)
    importer.import_export(write_export(tmp_path / "single", personal_chat()), me=OWNER)
    assert rows(DANA)[11]["out"] == 1
    assert _owner() == str(OWNER)


def test_cli_import_with_me_for_another_account_uses_the_auth_exit_code(tmp_path, capsys):
    _set_owner(2)
    folder = write_export(tmp_path / "export", full_export())
    assert cli.main(["import", str(folder), "--me", "2"]) == cli.EXIT_AUTH
    assert db.OTHER_ACCOUNT_IMPORT in capsys.readouterr().err
    assert rows(DANA) == {}
