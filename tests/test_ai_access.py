"""Per-chat AI access: settings, the shared scope in query.py, and every MCP tool in both modes."""
import json

import pytest

from dotiphant import config, db, query
from dotiphant import mcp_server as ms
from tests.test_mcp import build_archive, fenced_ok

ALICE, BOB, TEAM, TEAM_OLD, NEWS, ROCKET = 111, 222, -100200, -300, -100400, -100500


@pytest.fixture
def archive(tmp_path, monkeypatch):
    return build_archive(tmp_path, monkeypatch)


def set_access(mode: str = "all", hidden=(), allowed=()) -> None:
    s = config.Settings.load()
    s.ai_access, s.ai_hidden_chats, s.ai_allowed_chats = mode, list(hidden), list(allowed)
    s.save()


def looks_missing(out: str) -> None:
    """The answer for a chat AI apps may not read: the same as for a chat that does not exist."""
    assert "No archived chat matches" in out and "hidden" not in out.lower() and "#1" not in out, out


def scope():
    conn = db.connect(readonly=True)
    try:
        return query.ai_scope(conn, config.Settings.load())
    finally:
        conn.close()


# --- settings ----------------------------------------------------------------------

def test_defaults_keep_every_chat_visible(archive):
    s = config.Settings()
    assert (s.ai_access, s.ai_hidden_chats, s.ai_allowed_chats) == ("all", [], [])
    # An older settings.json without the new keys reads the same way.
    (archive / "settings.json").write_text(json.dumps({"favorites": [ALICE]}))
    assert config.Settings.load().ai_access == "all"
    assert all(scope().allows(c) for c in (ALICE, BOB, TEAM, TEAM_OLD, NEWS, ROCKET))
    assert "Alice Partner" in ms.list_chats()


def test_settings_are_cleaned_and_fail_closed(archive):
    (archive / "settings.json").write_text(json.dumps(
        {"ai_access": "some", "ai_hidden_chats": [1, "2", True, "x", 1, 2**70], "ai_allowed_chats": "nope"}))
    s = config.Settings.load()
    assert (s.ai_access, s.ai_hidden_chats, s.ai_allowed_chats) == ("selected", [1, 2], [])
    (archive / "settings.json").write_text("{broken")
    assert config.Settings.load().ai_access == "all"  # the viewer and sync keep their defaults
    assert "No chats match" in ms.list_chats()  # but AI apps see nothing
    looks_missing(ms.get_messages("Alice"))


def test_show_and_hide_keep_both_lists_consistent():
    s = config.Settings()
    s.set_ai_visible([TEAM, TEAM_OLD], False)
    assert s.ai_hidden_chats == [TEAM]
    s.set_ai_visible([TEAM_OLD, TEAM], True)  # either id of the pair un-hides both
    assert (s.ai_hidden_chats, s.ai_allowed_chats) == ([], [TEAM_OLD])
    s.set_ai_visible([TEAM, TEAM_OLD], True)
    assert s.ai_allowed_chats == [TEAM_OLD]  # no duplicate for the same pair


# --- scope -------------------------------------------------------------------------

@pytest.mark.parametrize("hide", [TEAM, TEAM_OLD])
def test_upgraded_groups_are_hidden_together(archive, hide):
    set_access("all", hidden=[hide])
    sc = scope()
    assert not sc.allows(TEAM) and not sc.allows(TEAM_OLD) and sc.allows(ALICE)


@pytest.mark.parametrize("allow", [TEAM, TEAM_OLD])
def test_upgraded_groups_are_allowed_together(archive, allow):
    set_access("selected", allowed=[allow])
    sc = scope()
    assert sc.allows(TEAM) and sc.allows(TEAM_OLD) and not sc.allows(ALICE)


def test_hidden_wins_over_allowed(archive):
    set_access("selected", hidden=[ALICE], allowed=[ALICE, BOB])
    assert not scope().allows(ALICE) and scope().allows(BOB)


def test_scope_sql_handles_empty_and_large_lists(archive):
    assert query.AiScope().sql("c") == "1"
    assert query.AiScope(only=frozenset()).sql("c") == "(0)"
    big = query.AiScope(hidden=frozenset(range(-200_000, 0)))
    conn = db.connect(readonly=True)
    try:  # more ids than SQLite takes as bound values
        assert conn.execute(f"SELECT count(*) FROM chats WHERE {big.sql('chat_id')}").fetchone()[0] == 2
    finally:
        conn.close()


# --- MCP tools, mode "all" ---------------------------------------------------------

def test_all_mode_hides_one_chat_from_every_tool(archive):
    set_access("all", hidden=[ALICE])
    listed = ms.list_chats()
    assert "Alice Partner" not in listed and "Bob Quiet" in listed
    assert "Alice Partner" not in ms.list_chats(query_text="Alice")
    # Search: no hit, no snippet, no title, and no count of matches elsewhere.
    for out in (ms.search_messages("webhook"), ms.search_messages("integration"),
                ms.search_messages("webhook", chat="Bob")):
        fenced_ok(out)
        assert "Alice" not in out and "webhook is ready" not in out and "test pass" not in out
    assert "No other chat matches" in ms.search_messages("webhook", chat="Bob")
    assert "#22 " in ms.search_messages("integration")  # other chats still match
    for ref in ("Alice", "Alice Partner", "@alice", "111", ALICE):
        for out in (ms.get_messages(ref), ms.chat_info(ref), ms.search_messages("x", chat=ref),
                    ms.recent_activity(chats=[ref])):
            looks_missing(out)
    recent = ms.recent_activity(since="2d")
    # Alice still speaks in a visible group, but her own private chat is gone.
    assert "(chat 111" not in recent and "test pass" not in recent and "NEEDS REPLY" not in recent
    assert "Bob Quiet" in recent


def test_suggestions_skip_hidden_chats(archive):
    assert "Alice Partner" in ms.get_messages("Alise Partnr")
    set_access("all", hidden=[ALICE])
    out = ms.get_messages("Alise Partnr")
    assert "Alice" not in out
    looks_missing(out)
    assert "Alice" not in ms.list_chats(query_text="Alise Partnr")


def test_ambiguous_reference_does_not_list_hidden_candidates(archive):
    assert "several chats" in ms.get_messages("Project")
    set_access("all", hidden=[ROCKET])
    out = ms.get_messages("Project")
    assert "Project News" in out and "Rocket" not in out and "several" not in out


@pytest.mark.parametrize("ref", ["Alice", "Alice Partner", "alice partner", "lice Par", "Alic", "@alice", "111"])
def test_hidden_chat_answers_like_a_missing_one(archive, ref):
    """No reference, exact or partial, tells an AI app that a hidden chat exists or what it is called."""
    def answers():
        return [ms.get_messages(ref), ms.chat_info(ref), ms.search_messages("webhook", chat=ref),
                ms.recent_activity(chats=[ref])]

    set_access("all", hidden=[ALICE])
    hidden = answers()
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM chats WHERE chat_id=?", (ALICE,))
    conn.close()
    set_access("all")
    assert hidden == answers()
    for out in hidden:
        looks_missing(out)


def test_resolve_chat_with_a_scope_never_names_hidden_chats(archive):
    set_access("selected", allowed=[BOB])
    conn = db.connect(readonly=True)
    try:
        for ref in ("Alice", "Ali", "Partner", "Integration", ALICE, "@alice"):
            with pytest.raises(query.ChatNotFound) as err:
                query.resolve_chat(conn, ref, scope=scope())
            assert err.value.candidates == []
        assert query.resolve_chat(conn, "Bob", scope=scope())["chat_id"] == BOB
    finally:
        conn.close()


@pytest.mark.parametrize("hide", [TEAM, TEAM_OLD])
def test_predecessor_and_successor_hide_each_other(archive, hide):
    set_access("all", hidden=[hide])
    assert "Integration Team" not in ms.list_chats()
    for ref in ("Integration Team", TEAM, TEAM_OLD):
        looks_missing(ms.get_messages(ref))
        looks_missing(ms.chat_info(ref))
    out = ms.search_messages("kickoff") + ms.search_messages("integration docs")
    assert "kickoff" not in out.lower().replace("'kickoff'", "") and "#22 " not in out
    assert "Integration Team" not in ms.recent_activity(since="60d")


# --- MCP tools, mode "selected" ----------------------------------------------------

def test_selected_mode_shows_only_allowed_chats(archive):
    set_access("selected")
    assert "No chats match" in ms.list_chats()
    assert "No messages match" in ms.search_messages("integration")
    set_access("selected", allowed=[ALICE])
    listed = ms.list_chats()
    assert "Alice Partner" in listed and "Bob Quiet" not in listed and "Integration Team" not in listed
    out = ms.search_messages("integration")
    assert "(chat 111)" in out and "-100200" not in out and "kickoff" not in out
    assert "#12 " in ms.get_messages("Alice")
    looks_missing(ms.get_messages("Bob"))
    looks_missing(ms.chat_info(ROCKET))
    recent = ms.recent_activity(since="2d")
    assert "Alice Partner" in recent and "Bob Quiet" not in recent and "Integration" not in recent


def test_selected_mode_allows_a_group_with_its_history(archive):
    set_access("selected", allowed=[TEAM])
    out = ms.get_messages("Integration Team", since="60d")
    assert "kickoff" in out and "earlier basic group -300" in out
    assert "kickoff" in ms.search_messages("kickoff")
    assert "Alice" not in ms.chat_info(TEAM).split("## Most active participants")[0]


def test_changes_apply_on_the_next_call(archive):
    server_text = ms.get_messages("Alice")
    assert "#12 " in server_text
    set_access("all", hidden=[ALICE])
    looks_missing(ms.get_messages("Alice"))
    set_access("all")
    assert "#12 " in ms.get_messages("Alice")


async def test_refresh_of_a_hidden_chat_is_refused(archive, monkeypatch):
    set_access("all", hidden=[ALICE])
    monkeypatch.setattr(ms, "_start_sync", lambda chat_id: pytest.fail("must not start"))
    out = await ms.refresh("Alice", wait_seconds=1)
    looks_missing(out)


async def test_hidden_chat_through_the_mcp_client(archive):
    from mcp import Client

    set_access("all", hidden=[ALICE])
    async with Client(ms.build_server()) as client:
        assert "dotiphant ai show" in (client.instructions or "")
        text = (await client.call_tool("get_messages", {"chat": ALICE})).content[0].text
        looks_missing(text)
        text = (await client.call_tool("search_messages", {"query": "webhook"})).content[0].text
        assert "webhook" not in text.replace("'webhook'", "")


def test_owner_tools_are_not_filtered(archive):
    set_access("selected")
    conn = db.connect(readonly=True)
    try:
        assert query.resolve_chat(conn, "Alice")["chat_id"] == ALICE
        assert any(h["chat_id"] == ALICE for h in query.search(conn, "webhook"))
        assert len(query.list_chats(conn)) == 5
    finally:
        conn.close()
