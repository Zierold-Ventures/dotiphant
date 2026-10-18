"""A fake Telethon client that serves real telethon.tl.types objects.

It implements only what dotiphant.sync uses and pages history the way Telegram does:
newest first, honouring offset_id, add_offset, limit, min_id and max_id, with the
documented quirks a sync must survive:

- A partial answer is a messages.messagesSlice (channelMessages for channels). Only a
  small, complete history comes back as messages.messages.
- Short pages mid-history: ids in `hidden` are left out (Telegram drops messages it may
  not show), and `page_cap` returns fewer messages than asked for.
- min_id or max_id more than 100 below the newest id returns nothing (see the note in
  telethon/client/messages.py).
- Chats in `inaccessible` raise the given Telegram error (left, banned, private).
- `dialog_flood` (after n dialogs, seconds) makes the dialog walk raise FloodWaitError,
  as Telethon does for waits above its flood_sleep_threshold.
- The dialog list's top message is the highest id in the history, even when it is in
  `hidden` (Telegram does list top messages that getHistory leaves out).
- Profile photos come from each entity's `photo` (see chat_photo and user_photo);
  download_profile_photo raises the error in `photo_errors` for a chat, if any.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

from telethon import errors, functions, types, utils

START = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
ME = types.User(id=1, first_name="Elowen", last_name="Q", username="me_user", is_self=True)


def user(uid: int, first: str, last: str | None = None, bot: bool = False, username: str | None = None):
    return types.User(id=uid, first_name=first, last_name=last, bot=bot, username=username, access_hash=uid * 7)


def chat_photo(photo_id: int):
    return types.ChatPhoto(photo_id=photo_id, dc_id=2)


def user_photo(photo_id: int):
    return types.UserProfilePhoto(photo_id=photo_id, dc_id=2)


def supergroup(cid: int, title: str, forum: bool = False, broadcast: bool = False):
    return types.Channel(id=cid, title=title, photo=types.ChatPhotoEmpty(), date=START, access_hash=cid * 3,
                         megagroup=not broadcast, broadcast=broadcast, forum=forum or None)


def basic_group(cid: int, title: str, migrated_to: int | None = None):
    return types.Chat(id=cid, title=title, photo=types.ChatPhotoEmpty(), participants_count=5, date=START,
                      version=1, migrated_to=types.InputChannel(migrated_to, 0) if migrated_to else None)


def msg(peer, mid: int, text: str = "", *, sender: int | None = None, out: bool = False, minutes: int | None = None,
        **kw):
    return types.Message(id=mid, peer_id=peer, date=START + dt.timedelta(minutes=mid if minutes is None else minutes),
                         message=text, out=out, from_id=types.PeerUser(sender) if sender else None, **kw)


def full_channel(cid: int, migrated_from: int | None = None, members: int = 42):
    full = types.ChannelFull(
        id=cid, about="", read_inbox_max_id=0, read_outbox_max_id=0, unread_count=0,
        chat_photo=types.PhotoEmpty(id=0), notify_settings=types.PeerNotifySettings(), bot_info=[], pts=1,
        participants_count=members, migrated_from_chat_id=migrated_from, migrated_from_max_id=10 if migrated_from else None,
    )
    return types.messages.ChatFull(full_chat=full, chats=[], users=[])


def topic(peer, tid: int, title: str, top_message: int):
    return types.ForumTopic(
        id=tid, date=START, peer=peer, title=title, icon_color=0, top_message=top_message, read_inbox_max_id=0,
        read_outbox_max_id=0, unread_count=0, unread_mentions_count=0, unread_reactions_count=0,
        unread_poll_votes_count=0, from_id=types.PeerUser(1), notify_settings=types.PeerNotifySettings())


class FakeClient:
    def __init__(self, me=ME):
        self.me = me
        self.dialogs: list[dict] = []  # {"entity", "archived", "unread"}
        self.history: dict[int, list] = {}  # chat peer id -> messages
        self.users: list = []
        self.full: dict[int, object] = {}
        self.topics: dict[int, list] = {}
        self.requests: list = []
        self.fail_on_request: int | None = None  # raise RuntimeError on this request number
        self.flood_on_request: tuple[int, int] | None = None  # (request number, seconds)
        self.on_request = None  # callback(request) for tests (e.g. a fake clock)
        self.downloads: list = []
        self.connected = False
        self.authorized = True
        self.hidden: set[int] = set()  # message ids Telegram leaves out of pages
        self.page_cap: int | None = None  # return at most this many messages per page
        self.inaccessible: dict[int, type] = {}  # chat id -> Telegram error class
        self.empty_answers = 0  # requests answered with nothing because of min_id/max_id
        self.dialog_flood: tuple[int, int] | None = None  # (dialogs listed before the error, seconds)
        self.photo_downloads: list[tuple[int, int, bool]] = []  # (chat id, photo id, download_big)
        self.photo_errors: dict[int, Exception] = {}  # chat id -> error download_profile_photo raises

    # setup helpers
    def add_chat(self, entity, messages=(), archived=False, unread=0):
        self.dialogs.append({"entity": entity, "archived": archived, "unread": unread})
        self.history.setdefault(utils.get_peer_id(entity), []).extend(messages)

    def add_messages(self, chat_id: int, messages):
        self.history.setdefault(chat_id, []).extend(messages)

    # client API used by dotiphant.sync
    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return self.me

    def _top(self, chat_id):
        msgs = self.history.get(chat_id) or []
        return max(msgs, key=lambda m: m.id) if msgs else None

    async def iter_dialogs(self, ignore_migrated=False, **kw):
        def date(d):
            top = self._top(utils.get_peer_id(d["entity"]))
            return top.date if top else START
        for n, d in enumerate(sorted(self.dialogs, key=date, reverse=True)):
            if self.dialog_flood and n == self.dialog_flood[0]:
                raise errors.FloodWaitError(request=None, capture=self.dialog_flood[1])
            top = self._top(utils.get_peer_id(d["entity"]))
            yield SimpleNamespace(entity=d["entity"], archived=d["archived"], unread_count=d["unread"],
                                  date=date(d), message=top, id=utils.get_peer_id(d["entity"]))

    def _entities(self):
        return [d["entity"] for d in self.dialogs]

    async def get_input_entity(self, peer):
        pid = utils.get_peer_id(peer)
        for e in self._entities():
            if utils.get_peer_id(e) == pid:
                return utils.get_input_peer(e)
        raise ValueError("unknown entity")

    async def __call__(self, request):
        self.requests.append(request)
        n = len(self.requests)
        if self.on_request:
            self.on_request(request)
        if self.fail_on_request == n:
            raise RuntimeError("simulated network failure")
        if self.flood_on_request and self.flood_on_request[0] == n:
            raise errors.FloodWaitError(request=None, capture=self.flood_on_request[1])
        if isinstance(request, functions.messages.GetHistoryRequest):
            return self._history(request)
        if isinstance(request, functions.channels.GetFullChannelRequest):
            cid = utils.get_peer_id(request.channel)
            if cid in self.inaccessible:
                raise self.inaccessible[cid](request=None)
            if cid not in self.full:
                raise errors.ChannelPrivateError(request=None)
            return self.full[cid]
        if isinstance(request, functions.messages.GetForumTopicsRequest):
            cid = utils.get_peer_id(request.peer)
            return types.messages.ForumTopics(count=len(self.topics.get(cid, [])), topics=self.topics.get(cid, []),
                                              messages=[], chats=[], users=[], pts=1)
        raise AssertionError(f"unexpected request {type(request).__name__}")

    def _history(self, req):
        cid = utils.get_peer_id(req.peer)
        if cid in self.inaccessible:
            raise self.inaccessible[cid](request=None)
        ordered = sorted(self.history.get(cid, []), key=lambda m: -m.id)
        top = ordered[0].id if ordered else 0
        if (req.min_id and req.min_id < top - 100) or (req.max_id and req.max_id < top - 100):
            self.empty_answers += 1
            page = []
        else:
            idx = 0
            if req.offset_id:
                idx = next((i for i, m in enumerate(ordered) if m.id < req.offset_id), len(ordered))
            start = max(0, idx + req.add_offset)
            page = ordered[start:start + req.limit]
            page = [m for m in page if m.id > req.min_id and (not req.max_id or m.id < req.max_id)]
            page = [m for m in page if m.id not in self.hidden]
            if self.page_cap is not None:
                page = page[:self.page_cap]
        common = dict(messages=page, topics=[], chats=self._entities(), users=[self.me, *self.users])
        if isinstance(req.peer, types.InputPeerChannel):
            return types.messages.ChannelMessages(pts=1, count=len(ordered), **common)
        if len(ordered) <= req.limit and self.page_cap is None and not self.hidden:
            return types.messages.Messages(**common)
        return types.messages.MessagesSlice(count=len(ordered), **common)

    async def get_messages(self, peer, ids):
        cid = utils.get_peer_id(peer)
        by_id = {m.id: m for m in self.history.get(cid, [])}
        return [by_id.get(i) for i in ids]

    async def download_media(self, message, file):
        self.downloads.append(message.id)
        Path(file).write_bytes(b"fake-bytes-%d" % message.id)
        return file

    async def download_profile_photo(self, entity, file=None, download_big=True):
        cid = utils.get_peer_id(entity)
        if self.on_request:
            self.on_request(("photo", cid))
        error = self.photo_errors.get(cid)
        if error is not None:
            raise error
        photo = getattr(entity, "photo", None)
        photo_id = getattr(photo, "photo_id", None)
        if photo_id is None:
            return None
        self.photo_downloads.append((cid, photo_id, download_big))
        data = b"\xff\xd8\xff\xe0fake-jpeg-%d-%d" % (cid, photo_id)
        if file is bytes:
            return data
        Path(file).write_bytes(data)
        return file
