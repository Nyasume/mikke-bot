"""Test data: constants, SauceNAO payloads and Telegram update builders (plain dicts)."""

import itertools
import time
from typing import Any

BOT_TOKEN = "42:TEST-token"
BOT_USERNAME = "SauceTestBot"
API_KEY = "sauce-key"
ADMIN_ID = 1
FAVOURITE_GROUP = -1001
PUBLIC_URL = "https://example.org/rsbot"
IMAGE = b"\xff\xd8\xff\xe0 fake jpeg"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


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


# --- Telegram updates --------------------------------------------------------

_update_ids = itertools.count(1)
USER = {"id": 7, "is_bot": False, "first_name": "User"}
PRIVATE = {"id": 7, "type": "private", "first_name": "User"}
GROUP = {"id": -500, "type": "supergroup", "title": "Group"}
FAV_GROUP = {"id": FAVOURITE_GROUP, "type": "supergroup", "title": "Favourite"}

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
