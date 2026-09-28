"""Test data: constants, SauceNAO and trace.moe payloads, Telegram update builders (plain dicts)."""

import asyncio
import itertools
import time
from typing import Any

import aiohttp
from yarl import URL

BOT_TOKEN = "42:TEST-token"
BOT_USERNAME = "MikkeTestBot"
# a second bot served by the same process
EXTRA_BOT_TOKEN = "43:EXTRA-token"
EXTRA_BOT_USERNAME = "MikkeSauceTestBot"
USERNAMES = {42: BOT_USERNAME, 43: EXTRA_BOT_USERNAME}
API_KEY = "sauce-key"
# a user's own key, shaped like the real ones: 40 lowercase hex characters
USER_KEY = "0123456789abcdef0123456789abcdef0123a1b2"
ADMIN_ID = 1
FAVOURITE_GROUP = -1001
PUBLIC_URL = "https://example.org/mikke"
IMAGE = b"\xff\xd8\xff\xe0 fake jpeg"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        """asyncio.sleep in fake time: whatever is ready runs first, then the clock jumps ahead."""
        await asyncio.sleep(0)
        self.now += seconds


# --- SauceNAO payloads -------------------------------------------------------


def sauce_response(results: list[dict], *, short_remaining: int = 3, long_remaining: int = 90, status: int = 0) -> dict:
    return {
        "header": {
            "short_limit": "4",
            "long_limit": "100",
            "short_remaining": short_remaining,
            "long_remaining": long_remaining,
            "status": status,
            "results_returned": len(results),
        },
        "results": results,
    }


# What SauceNAO answered to a made-up key on 2026-09-28, with HTTP 403
UNKNOWN_KEY = {"header": {"status": -1, "message": "The anonymous account type does not permit API usage."}}
DAILY_LIMIT = {"header": {"status": -2, "message": "Daily Search Limit Exceeded."}}


def result(similarity: float, **data: Any) -> dict:
    return {"header": {"similarity": f"{similarity:.2f}", "index_id": 0}, "data": data}


ANIME = result(
    93.1,
    ext_urls=["https://anidb.net/anime/69", "https://myanimelist.net/anime/21/", "https://anilist.co/anime/21/"],
    source="One Piece",
    anidb_aid=69,
    mal_id=21,
    anilist_id=21,
    part="12",
    year="1999-1999",
    est_time="00:12:33 / 00:24:40",
)


# --- trace.moe payloads ------------------------------------------------------


def trace_response(results: list[dict], *, quota: int = 100, quota_used: int = 1) -> dict:
    return {"frameCount": 745506, "error": "", "quota": quota, "quotaUsed": quota_used, "result": results}


SCENE = {
    "anilist": {
        "id": 20517,
        "idMal": 21273,
        "title": {
            "native": "ご注文はうさぎですか？",
            "romaji": "Gochuumon wa Usagi Desu ka?",
            "english": "Is the Order a Rabbit?",
        },
        "synonyms": ["GochiUsa"],
        "isAdult": False,
    },
    "filename": "Gochuumon wa Usagi desu ka - 03 (BD 1280x720).mp4",
    "episode": 3,
    "episode_start": 3,
    "episode_end": 3,
    "duration": 1420.5,
    "from": 725.25,
    "to": 729.9,
    "at": 727.0,
    "similarity": 0.9612,
    "video": "https://api.trace.moe/video/abc",
    "image": "https://api.trace.moe/image/abc",
}


# --- Telegram updates --------------------------------------------------------

_update_ids = itertools.count(1)
USER = {"id": 7, "is_bot": False, "first_name": "User"}
PRIVATE = {"id": 7, "type": "private", "first_name": "User"}
GROUP = {"id": -500, "type": "supergroup", "title": "Group"}
FAV_GROUP = {"id": FAVOURITE_GROUP, "type": "supergroup", "title": "Favourite"}
# made up, like everything here: the repository is public
ASKER = {"id": 1234567, "is_bot": False, "first_name": "Test", "last_name": "User", "username": "test_user"}
PUBLIC_GROUP = {"id": -1001234567890, "type": "supergroup", "title": "Anime Art <Fans>", "username": "example_art"}
PRIVATE_SUPERGROUP = {"id": -1009876543210, "type": "supergroup", "title": "Secret Club"}
BASIC_GROUP = {"id": -4001234, "type": "group", "title": "Old Group"}

PHOTO = [
    {"file_id": "photo-small-id", "file_unique_id": "photo-small-u", "width": 90, "height": 90},
    {"file_id": "photo-large-id", "file_unique_id": "photo-large-u", "width": 1280, "height": 960},
]


def message(chat: dict = PRIVATE, *, date: int | None = None, **fields: Any) -> dict:
    return {
        "message_id": next(_update_ids),
        "date": date if date is not None else int(time.time()),
        "chat": chat,
        "from": USER,
        **fields,
    }


def update(msg: dict) -> dict:
    return {"update_id": next(_update_ids), "message": msg}


def bot_answer(chat: dict, reply_to: dict | None, **fields: Any) -> dict:
    """The bot's answer to a media message, which carries the 🎬 button."""
    bot = {"id": 42, "is_bot": True, "first_name": "Mikke", "username": BOT_USERNAME}
    replied = {"reply_to_message": reply_to} if reply_to else {}
    return message(chat, text="<b>One Piece</b>", **{"from": bot}, **replied, **fields)


def button_press(msg: dict, data: str = "scene", user: dict = USER) -> dict:
    press = {"id": f"cb{next(_update_ids)}", "from": user, "chat_instance": "ci", "message": msg, "data": data}
    return {"update_id": next(_update_ids), "callback_query": press}


def telegram_file_error(token: str, path: str = "photos/file_1.jpg") -> aiohttp.ClientResponseError:
    """What aiohttp raises when Telegram's file server fails: the text quotes the URL, token included."""
    url = URL(f"https://api.telegram.org/file/bot{token}/{path}")
    return aiohttp.ClientResponseError(
        request_info=aiohttp.RequestInfo(url, "GET", {}, url), history=(), status=502, message="Bad Gateway"
    )
