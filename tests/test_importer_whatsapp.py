"""WhatsApp "Export chat" import: formats, placeholders, ids, idempotence, overlap, media, sync guard."""
import shutil
import zipfile
from pathlib import Path

import pytest

from dotiphant import cli, config, db, mcp_server, query
from dotiphant import importer_whatsapp as wa

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"
PHOTO = "00000012-PHOTO-2024-01-05-09-21-40.jpg"
LRM, NNBSP = "‎", " "


def ios_zip(tmp_path, media=True) -> Path:
    path = tmp_path / "WhatsApp Chat - Trip Planning.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.write(FIXTURES / "ios_en_us__chat.txt", "_chat.txt")
        if media:
            zf.writestr(PHOTO, b"\xff\xd8 fake jpeg")
            zf.writestr("../evil.jpg", b"no")
    return path


def placed(tmp_path, fixture: str, name: str) -> Path:
    path = tmp_path / name
    shutil.copy(FIXTURES / fixture, path)
    return path


def rows(chat_id):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT m.*, s.name AS sender FROM messages m LEFT JOIN senders s ON s.peer_id = m.sender_id "
            "WHERE chat_id=? ORDER BY message_id", (chat_id,))]
    finally:
        conn.close()


def chat_row(chat_id):
    conn = db.connect()
    try:
        return dict(conn.execute("SELECT * FROM chats WHERE chat_id=?", (chat_id,)).fetchone())
    finally:
        conn.close()


# --- formats ------------------------------------------------------------------------------

def test_ios_en_us_zip_group(tmp_path):
    path = ios_zip(tmp_path)
    assert wa.detect(path)
    report = wa.import_export(path, me="Elowen Q", tz="Europe/Berlin", copy_media=True)["chats"][0]
    assert report["title"] == "Trip Planning" and report["kind"] == "group"
    assert report["date_order"] == "mdy" and not report["date_order_guessed"]
    assert report["imported"] == 11 and report["media_copied"] == 1 and report["skipped_invalid"] == 0
    cid = report["chat_id"]
    assert wa.is_whatsapp_id(cid) and cid < 2**63
    chat = chat_row(cid)
    assert chat["username"] is None and chat["raw_json"] == '{"source": "whatsapp"}'
    assert chat["last_message_at"] == "2024-01-12T23:00:00+00:00"

    msgs = rows(cid)
    assert [m["message_id"] for m in msgs] == list(range(1, 12))
    # System lines: the encryption notice and the group events written as "Trip Planning: ‎..."
    assert [m["kind"] for m in msgs[:3]] == ["service"] * 3
    assert msgs[1]["service_text"] == 'Elowen Q created group "Trip Planning"' and msgs[1]["sender_id"] is None
    # 9:16:44 AM in Berlin (UTC+1 in January)
    assert msgs[3]["date"] == "2024-01-05T08:16:44+00:00" and msgs[3]["out"] == 1
    assert msgs[4]["text"] == "Me! Flights look cheap\non the 14th.\nAlso the 21st works."
    assert msgs[4]["sender"] == "Maya Chen" and msgs[4]["out"] == 0
    assert (msgs[5]["kind"], msgs[5]["media_type"], msgs[5]["media_name"]) == ("media", "photo", None)
    assert (msgs[6]["media_type"], msgs[6]["media_name"]) == ("photo", PHOTO)
    assert (config.media_dir() / msgs[6]["media_path"]).read_bytes() == b"\xff\xd8 fake jpeg"
    assert (msgs[7]["kind"], msgs[7]["service_text"], msgs[7]["sender"]) == ("service", "Message deleted", "Maya Chen")
    assert msgs[8]["text"] == "Booked the apartment near Alfama" and msgs[8]["edit_date"] is None
    assert '"edited": true' in msgs[8]["raw_json"]
    assert (msgs[9]["media_type"], msgs[9]["media_name"], msgs[9]["text"]) == ("document", "Flight_Confirmation.pdf", "")
    # 12:00:00 AM is midnight
    assert msgs[10]["date"] == "2024-01-12T23:00:00+00:00"
    assert not any(LRM in (m["text"] + (m["service_text"] or "")) for m in msgs)
    assert not (config.media_dir().parent / "evil.jpg").exists()


def test_ios_de_de_private(tmp_path):
    path = placed(tmp_path, "ios_de_de__chat.txt", "WhatsApp Chat mit Clara Hollerbach.txt")
    report = wa.import_export(path, me="Elowen Q", tz="Europe/Berlin")["chats"][0]
    assert (report["title"], report["kind"], report["date_order"]) == ("Clara Hollerbach", "private", "dmy")
    msgs = rows(report["chat_id"])
    assert msgs[0]["kind"] == "service" and msgs[0]["service_text"].startswith("Nachrichten und Anrufe")
    assert msgs[1]["date"] == "2024-02-03T17:42:11+00:00" and msgs[1]["text"].startswith("Hallo!")
    assert msgs[2]["out"] == 1
    assert (msgs[3]["media_type"], msgs[3]["text"]) == ("photo", "")
    assert msgs[4]["service_text"] == "Message deleted"
    assert msgs[5]["text"] == "Die Rechnung kommt per Mail"


def test_android_en_gb_private(tmp_path):
    path = placed(tmp_path, "android_en_gb.txt", "WhatsApp Chat with Dana Lee.txt")
    assert wa.detect(path)
    report = wa.import_export(path, me="Elowen Q", tz="UTC")["chats"][0]
    assert (report["title"], report["kind"], report["date_order"]) == ("Dana Lee", "private", "dmy")
    msgs = rows(report["chat_id"])
    assert msgs[0]["kind"] == "service" and msgs[0]["sender_id"] is None
    assert msgs[1]["date"] == "2024-01-31T14:05:00+00:00"
    assert (msgs[2]["media_type"], msgs[2]["media_name"], msgs[2]["text"]) == (
        "photo", "IMG-20240131-WA0001.jpg", "Scan of page 1")
    assert (msgs[3]["kind"], msgs[3]["media_type"]) == ("media", "other")
    # Two identical messages in the same minute are both kept.
    assert [m["text"] for m in msgs[5:7]] == ["ok", "ok"]
    assert msgs[7]["service_text"] == "Message deleted"
    assert msgs[8]["text"] == "Received the payment"


def test_android_en_us_group(tmp_path):
    path = placed(tmp_path, "android_en_us.txt", "WhatsApp Chat with Book Club.txt")
    report = wa.import_export(path, tz="America/New_York")["chats"][0]
    assert (report["kind"], report["date_order"]) == ("group", "mdy")
    msgs = rows(report["chat_id"])
    assert msgs[1]["service_text"] == 'Sam Ortiz created group "Book Club"'
    assert msgs[3]["date"] == "2023-12-28T23:02:00+00:00"  # 6:02 PM EST
    assert (msgs[5]["media_type"], msgs[5]["media_name"]) == ("voice", "PTT-20231229-WA0003.opus")
    assert msgs[6]["media_type"] == "location"
    assert msgs[7]["kind"] == "service" and msgs[7]["service_text"] == "Priya N left"
    assert all(m["out"] == 0 for m in msgs)


def test_tz_default_is_local_and_tz_option(tmp_path, monkeypatch):
    path = placed(tmp_path, "android_en_gb.txt", "WhatsApp Chat with Dana Lee.txt")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    import time
    time.tzset()
    try:
        report = wa.import_export(path, title="Dana local")["chats"][0]
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()
    assert rows(report["chat_id"])[1]["date"] == "2024-01-31T05:05:00+00:00"
    report = wa.import_export(path, title="Dana Berlin", tz="Europe/Berlin")["chats"][0]
    assert rows(report["chat_id"])[1]["date"] == "2024-01-31T13:05:00+00:00"
    with pytest.raises(wa.ExportFormatError):
        wa.import_export(path, tz="Mars/Olympus")


# --- date order ----------------------------------------------------------------------------

def entries(*dates):
    return wa.split_entries("\n".join(f"{d}, 10:00 - Ann: hi" for d in dates))


def test_date_order_detection():
    assert wa.detect_date_order(entries("13/01/2024")) == ("dmy", False)
    assert wa.detect_date_order(entries("01/13/2024")) == ("mdy", False)
    # No value above 12, but only month-first keeps the messages in time order.
    assert wa.detect_date_order(entries("01/02/2024", "01/03/2024", "02/01/2024")) == ("mdy", False)
    # Same date either way.
    assert wa.detect_date_order(entries("05/05/2024")) == ("dmy", False)
    # Undecidable: the option wins, else a reported guess.
    assert wa.detect_date_order(entries("01/02/2024"), "mdy") == ("mdy", False)
    assert wa.detect_date_order(entries("01/02/2024")) == ("dmy", True)
    with pytest.raises(wa.ExportFormatError):
        wa.detect_date_order(entries("13/01/2024", "01/13/2024"))
    assert wa.detect_date_order(entries("13/01/2024", "01/13/2024"), "dmy") == ("dmy", False)
    iso = wa.split_entries("2024-01-02, 10:00 - Ann: hi")
    assert wa._to_date(iso[0], "mdy") == (2024, 1, 2)


def test_date_order_option_through_import(tmp_path):
    path = tmp_path / "WhatsApp Chat with Ann.txt"
    path.write_text("01/02/2024, 10:00 - Ann: hello\n")
    report = wa.import_export(path, date_order="mdy", tz="UTC")["chats"][0]
    assert rows(report["chat_id"])[0]["date"] == "2024-01-02T10:00:00+00:00"


def test_header_variants():
    lines = [
        f"{LRM}[1/5/24, 9:15:02{NNBSP}PM] Ann: a",
        "[05.01.24, 21:15:02] Ann: b",
        f"1/5/24, 9:15{NNBSP}pm - Ann: c",
        "05/01/2024, 21:15 - Ann: d",
        "2024-01-05, 9:15 p.m. - Ann: e",
        "05.01.24, 21:15 - Ann: f",
    ]
    got = wa.split_entries("\n".join(lines))
    assert [e.lines[0] for e in got] == ["Ann: a", "Ann: b", "Ann: c", "Ann: d", "Ann: e", "Ann: f"]
    assert [e.ampm for e in got] == ["pm", None, "pm", None, "pm", None]


# --- ids, idempotence, overlap ---------------------------------------------------------------

def test_ids_do_not_collide_with_telegram():
    cid = wa.chat_id_for("Clara")
    sid = wa.sender_id_for("Clara")
    assert wa.WHATSAPP_ID_MIN <= cid < wa.SENDER_ID_MIN <= sid < 2**63
    assert cid == wa.chat_id_for("clara ")  # stable and case-insensitive
    assert not wa.is_whatsapp_id(10**13) and not wa.is_whatsapp_id(-1001234567890)


def test_reimport_is_idempotent_and_me_fixes_out(tmp_path):
    path = ios_zip(tmp_path)
    first = wa.import_export(path, tz="UTC")["chats"][0]
    assert all(m["out"] == 0 for m in rows(first["chat_id"]))
    again = wa.import_export(path, tz="UTC", me="elowen q")["chats"][0]
    assert (again["imported"], again["already_present"]) == (0, 11)
    msgs = rows(first["chat_id"])
    assert len(msgs) == 11
    assert {m["sender"] for m in msgs if m["out"]} == {"Elowen Q"}
    conn = db.connect()
    assert conn.execute("SELECT messages FROM chat_stats WHERE chat_id=?", (first["chat_id"],)).fetchone()[0] == 11
    assert conn.execute("SELECT count(*) FROM sync_state").fetchone()[0] == 0
    conn.close()


def test_copy_media_on_reimport(tmp_path):
    path = ios_zip(tmp_path)
    wa.import_export(path, tz="UTC")
    report = wa.import_export(path, tz="UTC", copy_media=True)["chats"][0]
    assert (report["imported"], report["media_copied"]) == (0, 1)


def test_overlapping_newer_export_appends_only_new(tmp_path):
    old = tmp_path / "old" / "WhatsApp Chat with Dana Lee.txt"
    old.parent.mkdir()
    text = (FIXTURES / "android_en_gb.txt").read_text(encoding="utf-8").splitlines()
    old.write_text("\n".join(text[:7]) + "\n", encoding="utf-8")  # up to the first "ok"
    first = wa.import_export(old, tz="UTC")["chats"][0]
    assert first["imported"] == 6
    # The newer export starts later (older messages cut off) and goes further.
    new = tmp_path / "new" / "WhatsApp Chat with Dana Lee.txt"
    new.parent.mkdir()
    new.write_text("\n".join(text[4:]) + "\n31/01/2024, 15:00 - Dana Lee: one more\n", encoding="utf-8")
    second = wa.import_export(new, tz="UTC")["chats"][0]
    assert second["chat_id"] == first["chat_id"]
    assert (second["already_present"], second["imported"]) == (3, 4)
    msgs = rows(first["chat_id"])
    assert [m["text"] for m in msgs if m["text"] == "ok"] == ["ok", "ok"]
    assert [m["message_id"] for m in msgs] == list(range(1, 11))
    assert msgs[-1]["text"] == "one more"


def test_invalid_dates_are_skipped(tmp_path):
    path = tmp_path / "WhatsApp Chat with Ann.txt"
    path.write_text("13/01/2024, 10:00 - Ann: ok\n31/02/2024, 10:00 - Ann: no such day\n13/01/2024, 13:00 pm - Ann: bad\n")
    report = wa.import_export(path, tz="UTC")["chats"][0]
    assert (report["imported"], report["skipped_invalid"]) == (1, 2)


def test_not_an_export(tmp_path):
    (tmp_path / "notes.txt").write_text("just some notes\n")
    assert not wa.detect(tmp_path / "notes.txt")
    with pytest.raises(wa.ExportFormatError):
        wa.import_export(tmp_path / "notes.txt", title="x")
    (tmp_path / "bad.zip").write_bytes(b"not a zip")
    assert not wa.detect(tmp_path / "bad.zip")
    with pytest.raises(wa.ExportFormatError):
        wa.import_export(tmp_path / "bad.zip")
    folder = tmp_path / "f"
    folder.mkdir()
    shutil.copy(FIXTURES / "ios_de_de__chat.txt", folder / "_chat.txt")
    with pytest.raises(wa.ExportFormatError, match="--title"):
        wa.import_export(folder)
    assert wa.import_export(folder, title="Clara")["chats"][0]["imported"] == 6


def test_folder_copy_media_stays_inside(tmp_path):
    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "WhatsApp Chat with Eve.txt").write_text(
        "13/01/2024, 10:00 - Eve: IMG-20240113-WA0001.jpg (file attached)\n"
        "13/01/2024, 10:01 - Eve: <attached: ../secret.jpg>\n")
    (folder / "IMG-20240113-WA0001.jpg").write_bytes(b"img")
    (tmp_path / "secret.jpg").write_bytes(b"secret")
    report = wa.import_export(folder, tz="UTC", copy_media=True)["chats"][0]
    assert (report["media_copied"], report["media_missing"]) == (1, 1)
    files = sorted(p.name for p in (config.media_dir() / str(report["chat_id"])).iterdir())
    assert files == ["1-IMG-20240113-WA0001.jpg"]


# --- search, MCP, CLI and sync --------------------------------------------------------------

def test_search_and_mcp_render(tmp_path):
    report = wa.import_export(ios_zip(tmp_path), me="Elowen Q", tz="UTC")["chats"][0]
    conn = db.connect(readonly=True)
    try:
        hits = query.search(conn, "Alfama")
        hits = hits["results"] if isinstance(hits, dict) else hits
        assert any(h["chat_id"] == report["chat_id"] for h in hits)
    finally:
        conn.close()
    out = mcp_server.get_messages("Trip Planning")
    assert "Flights look cheap" in out and "Maya Chen" in out and "Message deleted" in out
    assert "Booked the apartment near Alfama" in out and "<This message was edited>" not in out
    found = mcp_server.search_messages("Lisbon")
    assert "Who is in for [Lisbon] in March?" in found


def test_cli_import_detects_whatsapp(tmp_path, capsys):
    path = ios_zip(tmp_path)
    assert cli.main(["import", str(path), "--me", "Elowen Q", "--tz", "UTC", "--copy-media"]) == 0
    out = capsys.readouterr().out
    assert "Trip Planning (group," in out and "11 imported" in out and "1 files copied" in out
    txt = placed(tmp_path, "android_en_gb.txt", "chat.txt")
    assert cli.main(["import", str(txt), "--source", "whatsapp", "--title", "Dana", "--tz", "UTC"]) == 0
    captured = capsys.readouterr()
    assert "Dana (private," in captured.out and "--me \"Your Name\"" in captured.err
    assert cli.main(["import", str(txt), "--source", "telegram"]) == 1
    assert cli.main(["import", str(tmp_path / "result.json"), "--me", "Elowen"]) == 1


def test_cli_date_order_guess_note(tmp_path, capsys):
    path = tmp_path / "WhatsApp Chat with Ann.txt"
    path.write_text("01/02/2024, 10:00 - Ann: hello\n")
    assert cli.main(["import", str(path), "--tz", "UTC"]) == 0
    assert "--date-order mdy" in capsys.readouterr().err


async def test_sync_skips_whatsapp_chats(tmp_path, monkeypatch):
    from dotiphant import sync
    from tests.fakes import FakeClient

    monkeypatch.setattr(sync, "PAGE_DELAY", 0)
    report = wa.import_export(ios_zip(tmp_path), tz="UTC")["chats"][0]
    cid = report["chat_id"]
    client = FakeClient()
    summary = await sync.run_sync([cid], client=client, settings=config.Settings(history_days=None))
    assert summary["errors"] == [{"chat_id": cid, "error": "imported from a WhatsApp export; nothing to sync"}]
    await sync.run_sync(client=client, settings=config.Settings(history_days=None))
    conn = db.connect()
    try:
        assert conn.execute("SELECT count(*) FROM sync_state WHERE chat_id=?", (cid,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM messages WHERE chat_id=?", (cid,)).fetchone()[0] == 11
    finally:
        conn.close()

    class NoClient:
        def __getattr__(self, name):
            raise AssertionError(f"Telegram was called: {name}")

    counts = await sync.download_media(cid, client=NoClient())
    assert counts["unavailable"] == 3 and counts["downloaded"] == 0
