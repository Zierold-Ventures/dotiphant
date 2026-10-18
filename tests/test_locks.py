"""Processes that change settings.json or one chat at the same time never undo each other.

Covers: one temporary file per settings write, update_settings under settings.lock, no
overwrite of an unreadable settings.json, setup prompts that keep changes made meanwhile,
and the sync lock shared by forget, media downloads and both importers. Every race is
forced with events or hooks, never with timing.
"""
import asyncio
import json
import os
import subprocess
import sys
import threading

import pytest
from telethon import types, utils

from dotiphant import cli, config, db, forget, importer, status, sync
from dotiphant import importer_whatsapp as wa
from tests import fakes
from tests.fakes import FakeClient, msg
from tests.test_importer import personal_chat, write_export
from tests.test_importer import DANA
from tests.test_importer_whatsapp import ios_zip

GROUP = fakes.supergroup(500, "Team")
GROUP_ID = utils.get_peer_id(GROUP)
GROUP_PEER = types.PeerChannel(500)


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setattr(sync, "PAGE_DELAY", 0)
    monkeypatch.setattr(sync, "BACKFILL_DELAY", 0)
    monkeypatch.setattr(sync, "AVATAR_DELAY", 0)


# --- settings.json ---------------------------------------------------------------------

def test_two_writes_at_once_never_mix_their_contents(monkeypatch):
    """A second save lands while the first is about to rename its file: both keep their own."""
    path = config.data_dir() / "settings.json"
    real_replace = os.replace
    calls = []

    def replace(src, dst):
        calls.append(src)
        if len(calls) == 1:
            config.write_private(path, json.dumps({"ai_access": "selected", "favorites": [1]}))
        return real_replace(src, dst)

    monkeypatch.setattr(config.os, "replace", replace)
    config.write_private(path, json.dumps({"ai_access": "selected", "excluded_chats": list(range(300))}))
    assert calls[0] != calls[1]
    assert json.loads(path.read_text())["excluded_chats"] == list(range(300))
    assert sorted(p.name for p in path.parent.iterdir() if "settings" in p.name) == ["settings.json"]


def test_update_settings_waits_for_a_change_in_progress():
    """The viewer toggles a favorite while `dotiphant ai mode selected` runs: neither change is lost."""
    config.Settings(ai_access="all").save()
    inside, release, done = threading.Event(), threading.Event(), threading.Event()

    def slow_favorite(s):
        inside.set()
        assert release.wait(5)
        s.favorites.append(42)

    def ai_mode():
        config.update_settings(lambda s: setattr(s, "ai_access", "selected"))
        done.set()

    viewer = threading.Thread(target=lambda: config.update_settings(slow_favorite))
    viewer.start()
    assert inside.wait(5)
    cli_thread = threading.Thread(target=ai_mode)
    cli_thread.start()
    assert not done.wait(0.2)  # blocked on settings.lock, not racing
    release.set()
    viewer.join(5)
    cli_thread.join(5)
    s = config.Settings.load()
    assert (s.ai_access, s.favorites) == ("selected", [42])


def test_settings_lock_is_shared_with_other_processes(monkeypatch):
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; from dotiphant import config\n"
         "with config.settings_lock():\n"
         "    print('held', flush=True); sys.stdin.readline()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    try:
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(config, "SETTINGS_LOCK_WAIT", 0.1)
        with pytest.raises(config.SettingsBusy):
            config.update_settings(lambda s: setattr(s, "ai_access", "selected"))
    finally:
        holder.communicate("\n", timeout=10)
    assert config.update_settings(lambda s: setattr(s, "ai_access", "selected")).ai_access == "selected"


def test_an_unreadable_settings_file_is_never_overwritten(capsys):
    path = config.data_dir() / "settings.json"
    path.write_text('{"ai_access": "selected", "excluded_chats": [5]')  # cut short
    with pytest.raises(config.SettingsUnreadable):
        config.update_settings(lambda s: s.favorites.append(1))
    with pytest.raises(config.SettingsUnreadable):
        config.Settings(favorites=[1]).save()
    assert path.read_text() == '{"ai_access": "selected", "excluded_chats": [5]'
    assert cli.main(["ai", "mode", "all"]) == cli.EXIT_ERROR
    assert "cannot be read" in capsys.readouterr().err
    assert path.read_text().endswith("[5]")
    # Readers still fall back as before: the MCP server sees nothing.
    assert config.Settings.load(if_unreadable=config.Settings(ai_access="selected")).ai_access == "selected"


def test_setup_prompts_keep_changes_made_while_they_wait(monkeypatch):
    config.Settings(include_channels=False, favorites=[1]).save()
    answers = iter(["", "", "y", "", ""])

    def answer(prompt=""):
        if "Channels" in prompt:  # the viewer hides a chat and stars another meanwhile
            config.update_settings(lambda s: (s.set_ai_visible([7], False), s.favorites.append(2)))
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    s = cli.step_settings(cli.build_parser().parse_args(["setup", "--reconfigure"]))
    saved = config.Settings.load()
    assert saved.include_channels is True and s.include_channels is True
    assert saved.ai_hidden_chats == [7] and saved.favorites == [1, 2]


# --- forget and media downloads --------------------------------------------------------

def document(mid, name):
    doc = types.Document(id=mid, access_hash=1, file_reference=b"r", date=fakes.START, mime_type="application/pdf",
                         size=100, dc_id=1, attributes=[types.DocumentAttributeFilename(file_name=name)])
    return types.MessageMediaDocument(document=doc)


async def archive_with_files() -> FakeClient:
    client = FakeClient()
    client.users = [fakes.user(10, "Alice")]
    client.add_chat(GROUP, [msg(GROUP_PEER, i, f"m{i}", sender=10, media=document(i, f"f{i}.pdf")) for i in (1, 2)])
    await sync.run_sync(client=client, settings=config.Settings(history_days=None))
    return client


def chat_count(chat_id: int = GROUP_ID) -> int:
    conn = db.connect(readonly=True)
    try:
        return conn.execute("SELECT count(*) FROM chats WHERE chat_id=?", (chat_id,)).fetchone()[0]
    finally:
        conn.close()


async def test_forget_waits_for_a_download_that_is_still_finding_the_chat():
    """Forget during the slow start of a download (connect, dialog walk) is refused, not undone."""
    client = await archive_with_files()
    reached, release = threading.Event(), threading.Event()
    resolve = client.get_input_entity

    async def slow_resolve(peer):
        reached.set()
        await asyncio.to_thread(release.wait, 5)
        return await resolve(peer)

    client.get_input_entity = slow_resolve
    task = asyncio.create_task(sync.download_media(GROUP_ID, client=client))
    assert await asyncio.to_thread(reached.wait, 5)
    with pytest.raises(forget.Busy):
        await asyncio.to_thread(forget.forget_chat, GROUP_ID)
    release.set()
    assert (await task)["downloaded"] == 2
    forget.forget_chat(GROUP_ID)
    assert not (config.media_dir() / str(GROUP_ID)).exists() and chat_count() == 0


async def test_download_refuses_to_start_while_the_lock_is_held():
    await archive_with_files()
    with status.holding_sync_lock():
        with pytest.raises(sync.SyncBusy):
            await sync.download_media(GROUP_ID, client=FakeClient())
    assert not (config.media_dir() / str(GROUP_ID)).exists()


async def test_download_keeps_nothing_of_a_chat_deleted_meanwhile():
    """The backstop: a chat gone from the archive before a file is moved into place."""
    client = await archive_with_files()
    write = client.download_media

    async def download_then_chat_gone(message, file):
        conn = db.connect()
        conn.execute("DELETE FROM chats WHERE chat_id=?", (GROUP_ID,))
        conn.commit()
        conn.close()
        return await write(message, file)

    client.download_media = download_then_chat_gone
    counts = await sync.download_media(GROUP_ID, client=client)
    assert counts["downloaded"] == 0 and client.downloads == [2]
    assert not (config.media_dir() / str(GROUP_ID)).exists()


async def test_a_file_that_appears_during_forget_does_not_stop_it(monkeypatch):
    """A file lands between rmtree listing the folder and removing it."""
    await archive_with_files()
    folder = config.media_dir() / str(GROUP_ID)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "1-f1.pdf").write_bytes(b"old")
    real_rmdir = os.rmdir
    landed = []

    def rmdir(path, *a, **kw):
        if not landed and os.fspath(path) in (str(folder), folder.name):
            landed.append(True)
            (folder / "2-f2.pdf").write_bytes(b"late")
        return real_rmdir(path, *a, **kw)

    monkeypatch.setattr(os, "rmdir", rmdir)
    result = forget.forget_chat(GROUP_ID)
    assert landed and result["media_left"] == [] and not folder.exists()
    assert GROUP_ID in config.Settings.load().excluded_chats


async def test_a_forget_that_stops_halfway_still_keeps_the_chat_out_of_syncs(monkeypatch):
    await archive_with_files()
    real_open = db.open_rw

    class FailsOnDelete:
        def __init__(self):
            self.conn = real_open()

        def execute(self, sql, *args):
            if sql.startswith("DELETE FROM messages"):
                raise OSError("disk full")
            return self.conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def __enter__(self):
            return self.conn.__enter__()

        def __exit__(self, *exc):
            return self.conn.__exit__(*exc)

    monkeypatch.setattr(forget.db, "open_rw", FailsOnDelete)
    with pytest.raises(OSError):
        forget.forget_chat(GROUP_ID)
    assert chat_count() == 1  # nothing deleted, but the next sync leaves the chat alone
    conn = db.connect(readonly=True)
    try:  # the guard against deleting messages is back in place
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='messages_no_delete'").fetchone()
    finally:
        conn.close()
    assert GROUP_ID in config.Settings.load().excluded_chats


def test_forget_reports_a_folder_it_could_not_delete(monkeypatch):
    folder = config.media_dir() / "-1"
    folder.mkdir()
    monkeypatch.setattr(forget.shutil, "rmtree", lambda path, ignore_errors=False: None)
    assert forget._remove_folder(folder) is False
    assert forget._remove_folder(config.media_dir() / "-2") is True


# --- importers -------------------------------------------------------------------------

def test_telegram_import_holds_the_sync_lock(tmp_path):
    folder = write_export(tmp_path / "export", {"chats": {"list": [personal_chat()]}})
    with status.holding_sync_lock():
        with pytest.raises(status.LockBusy):
            importer.import_export(folder)
    assert importer.import_export(folder)["chats"][0]["imported"] == 3


def test_telegram_import_skips_forgotten_chats_unless_asked(tmp_path, capsys):
    folder = write_export(tmp_path / "export", {"chats": {"list": [personal_chat()]}})
    config.Settings(excluded_chats=[DANA]).save()
    report = importer.import_export(folder)["chats"][0]
    assert "--include-forgotten" in report["skipped"] and chat_count(DANA) == 0
    assert cli.main(["import", str(folder)]) == cli.EXIT_OK
    assert "skipped, excluded from the archive" in capsys.readouterr().out
    assert cli.main(["import", str(folder), "--include-forgotten"]) == cli.EXIT_OK
    assert chat_count(DANA) == 1
    assert DANA in config.Settings.load().excluded_chats  # still left out of syncs


def test_whatsapp_import_holds_the_lock_and_skips_forgotten_chats(tmp_path, capsys):
    path = ios_zip(tmp_path)
    with status.holding_sync_lock():
        assert cli.main(["import", str(path)]) == cli.EXIT_BUSY
    assert "Try the import again" in capsys.readouterr().err
    chat_id = wa.import_export(path, tz="UTC")["chats"][0]["chat_id"]
    forget.forget_chat(chat_id)
    report = wa.import_export(path, tz="UTC", copy_media=True)["chats"][0]
    assert "skipped" in report and chat_count(chat_id) == 0
    assert not (config.media_dir() / str(chat_id)).exists()
    assert wa.import_export(path, tz="UTC", include_forgotten=True)["chats"][0]["imported"] > 0

