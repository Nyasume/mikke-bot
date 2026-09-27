"""Turning SauceNAO results into the answer text and link buttons."""

import html
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from reverse_search_bot import texts

MIN_SIMILARITY = 60.0
TOLERANCE = 7.0
MAX_BUTTONS = 6
MAL_SEARCH_URL = "https://myanimelist.net/anime.php?"

# Button captions for ext_urls, by host (subdomains included)
SITE_NAMES = {
    "pixiv.net": "Pixiv",
    "danbooru.donmai.us": "Danbooru",
    "gelbooru.com": "Gelbooru",
    "sankakucomplex.com": "Sankaku",
    "anime-pictures.net": "Anime-Pictures",
    "imdb.com": "IMDb",
    "anidb.net": "AniDB",
    "e621.net": "e621",
    "yande.re": "yandere",
    "konachan.com": "Konachan",
    "deviantart.com": "deviantArt",
    "twitter.com": "Twitter",
    "x.com": "Twitter",
    "artstation.com": "ArtStation",
    "mangadex.org": "MangaDex",
    "mangaupdates.com": "MangaUpdates",
    "myanimelist.net": "MAL",
    "anilist.co": "AniList",
    "fanbox.cc": "Fanbox",
    "fantia.jp": "Fantia",
    "nijie.info": "Nijie",
    "furaffinity.net": "FurAffinity",
    "e-hentai.org": "E-Hentai",
    "pawoo.net": "Pawoo",
    "drawr.net": "Drawr",
    "bcy.net": "BCY",
}

Match = dict[str, Any]  # the `data` object of one SauceNAO result


def select(results: list[dict]) -> list[Match]:
    """Accepted results, best first: similarity >= 60 and within 7 points of the best one."""
    scored: list[tuple[float, Match]] = []
    for result in results:
        try:
            similarity = float((result.get("header") or {}).get("similarity"))
        except (TypeError, ValueError):
            continue
        if similarity >= MIN_SIMILARITY:
            scored.append((similarity, result.get("data") or {}))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []
    best = scored[0][0]
    return [data for similarity, data in scored if best - similarity <= TOLERANCE]


def _is_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _text(value: Any) -> str | None:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value if item not in (None, ""))
    if value is None:
        return None
    return str(value).strip() or None


def _first(matches: list[Match], pick: Callable[[Match], Any]) -> str | None:
    """The first non-empty value across the matches."""
    for data in matches:
        if value := _text(pick(data)):
            return value
    return None


def _title(data: Match) -> Any:
    source = data.get("source")
    return (
        _text(data.get("title"))
        or (None if _is_url(source) else _text(source))
        or _text(data.get("eng_name"))
        or _text(data.get("jp_name"))
    )


def _author(data: Match) -> Any:
    handle = _text(data.get("twitter_user_handle"))
    return (
        _text(data.get("creator"))
        or _text(data.get("member_name"))
        or _text(data.get("author_name"))
        or (f"@{handle}" if handle else None)
    )


def render_text(matches: list[Match]) -> str:
    title = _first(matches, _title)
    part = _first(matches, lambda d: d.get("part"))
    if title and part and any(d.get("anidb_aid") for d in matches):
        title = f"{title} (Ep. {part})"
        part = None

    fields = [
        ("Character", _first(matches, lambda d: d.get("characters"))),
        ("Material", _first(matches, lambda d: d.get("material"))),
        ("By", _first(matches, _author)),
        ("Part", part),
        ("Year", _first(matches, lambda d: d.get("year"))),
        ("Time", _first(matches, lambda d: d.get("est_time"))),
    ]
    lines = [f"<b>{html.escape(title, quote=False)}</b>"] if title else []
    lines += [f"<b>{name}: </b>{html.escape(value, quote=False)}" for name, value in fields if value]
    return "\n".join(lines) or texts.NO_TITLE


def site_name(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return None
    for domain, name in SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or None


def links(matches: list[Match]) -> list[tuple[str, str]]:
    """(caption, url) buttons: one per site, at most six, the first one "View on X"."""
    found: dict[str, str] = {}
    for data in matches:
        for url in data.get("ext_urls") or []:
            if _is_url(url) and (name := site_name(url)) and name not in found:
                found[name] = url
        source = data.get("source")
        if _is_url(source) and "Source" not in found:
            found["Source"] = source
    if "MAL" not in found and any(d.get("anidb_aid") for d in matches):
        query = _first(matches, lambda d: None if _is_url(d.get("source")) else d.get("source")) or _first(
            matches, _title
        )
        if query:
            found["MAL"] = MAL_SEARCH_URL + urlencode({"q": query})

    buttons = list(found.items())[:MAX_BUTTONS]
    if buttons:
        buttons[0] = (f"View on {buttons[0][0]}", buttons[0][1])
    return buttons


def grid[T](items: list[T]) -> list[list[T]]:
    """Two per row; the last row takes up to three."""
    rows: list[list[T]] = [[]]
    for index, item in enumerate(items):
        last = index == len(items) - 1
        if len(rows[-1]) >= 2 and not (last and len(rows[-1]) < 3):
            rows.append([])
        rows[-1].append(item)
    return [row for row in rows if row]


def keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, url=url) for text, url in row] for row in grid(buttons)]
    )


def render(matches: list[Match]) -> tuple[str, InlineKeyboardMarkup | None]:
    return render_text(matches), keyboard(links(matches))


def fallback_keyboard(image_url: str | None) -> InlineKeyboardMarkup | None:
    """Links to search the image elsewhere; needs a public, token-free image URL."""
    if not image_url:
        return None
    encoded = quote(image_url, safe="")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Google Lens", url=f"https://lens.google.com/uploadbyurl?url={encoded}"),
                InlineKeyboardButton(text="SauceNAO", url=f"https://saucenao.com/search.php?url={encoded}"),
                InlineKeyboardButton(text="TinEye", url=f"https://tineye.com/search?url={encoded}"),
            ]
        ]
    )
