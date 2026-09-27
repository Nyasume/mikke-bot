"""The anime scene search: trace.moe on demand, behind the button under an answer."""

import html
import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from cachetools import TTLCache

from mikke import texts
from mikke.media import Media
from mikke.reports import Reporter
from mikke.results import keyboard
from mikke.search import CACHE_SIZE, CACHE_TTL_SECONDS, Answer, InvalidFileError, download
from mikke.tracemoe import QuotaExceededError, TraceMoe

logger = logging.getLogger(__name__)

# trace.moe: "Similarity lower than 90% are most likely incorrect results"
WEAK_SIMILARITY = 0.9

Scene = dict[str, Any]  # one trace.moe result


@dataclass(frozen=True)
class Alert:
    """A popup on the pressed button instead of a message."""

    text: str


def _clean(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes:02}:{secs:02}"


def _time_range(scene: Scene) -> str | None:
    start, end = scene.get("from"), scene.get("to")
    if not isinstance(start, int | float):
        return None
    if not isinstance(end, int | float) or _timestamp(end) == _timestamp(start):
        return _timestamp(start)
    return f"{_timestamp(start)} - {_timestamp(end)}"


def _range(first: Any, last: Any) -> str:
    return str(first) if last in (None, first) else f"{first}-{last}"


def _episode(scene: Scene) -> str | None:
    # episode_start/episode_end follow AniList's numbering, and are null for specials
    if (start := scene.get("episode_start")) is not None:
        return _range(start, scene.get("episode_end"))
    # parsed from the file name: a number, a string, a list for a range, or null
    episode = scene.get("episode")
    if isinstance(episode, list):
        parts = [part for part in episode if part is not None]
        return _range(parts[0], parts[-1]) if parts else None
    return str(episode) if episode not in (None, "") else None


def render(scene: Scene) -> tuple[str, InlineKeyboardMarkup | None]:
    anilist = scene.get("anilist")
    # a bare AniList id when trace.moe could not add the AniList info
    info: dict[str, Any] = anilist if isinstance(anilist, dict) else {"id": anilist}
    titles = info.get("title") or {}
    english, romaji = _clean(titles.get("english")), _clean(titles.get("romaji"))
    title = english or romaji or _clean(titles.get("native")) or _clean(scene.get("filename")) or texts.NO_TITLE

    lines = [f"🎬 <b>{html.escape(title, quote=False)}</b>"]
    if english and romaji and romaji != english:
        lines.append(f"<i>{html.escape(romaji, quote=False)}</i>")
    fields = [("Episode", _episode(scene)), ("Time", _time_range(scene))]
    lines += [f"<b>{name}: </b>{html.escape(value, quote=False)}" for name, value in fields if value]
    similarity = scene.get("similarity")
    # rounded as shown, so 89.99% does not read "90.0%, weak match"
    similarity = round(similarity, 3) if isinstance(similarity, int | float) else 0.0
    lines.append(f"<b>Similarity: </b>{similarity:.1%}")
    if similarity < WEAK_SIMILARITY:
        lines.append(texts.SCENE_WEAK)

    buttons = []
    if isinstance(anilist_id := info.get("id"), int):
        buttons.append(("AniList", f"https://anilist.co/anime/{anilist_id}"))
    if isinstance(mal_id := info.get("idMal"), int):
        buttons.append(("MyAnimeList", f"https://myanimelist.net/anime/{mal_id}"))
    return "\n".join(lines), keyboard(buttons)


class SceneSearcher:
    """Cache, then trace.moe, then the answer; only ever run when someone asks."""

    def __init__(self, tracemoe: TraceMoe, reporter: Reporter, cache: TTLCache | None = None) -> None:
        self._tracemoe = tracemoe
        self._reporter = reporter
        # the best scene by file_unique_id; an empty list is a cached "not found"
        self._cache: TTLCache[str, list[Scene]] = (
            cache if cache is not None else TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
        )

    async def search(self, bot: Bot, media: Media) -> Answer | Alert:
        key = media.file_unique_id
        best = self._cache.get(key)
        if best is None:
            try:
                # no point in downloading the file while the quota is used up
                self._tracemoe.check_quota()
                image, filename = await download(bot, media)
                best = (await self._tracemoe.search(image, filename))[:1]
            except QuotaExceededError:
                return Alert(texts.SCENE_LIMIT)
            except InvalidFileError as e:
                logger.info("Invalid file %s: %s", key, e)
                return Alert(texts.SCENE_INVALID_FILE)
            except Exception as e:
                logger.exception("Scene search failed for %s", key)
                await self._reporter.error(bot, f"Scene search failed for {key}\n{type(e).__name__}: {e}")
                return Alert(texts.SCENE_ERROR)
            self._cache[key] = best

        if not best:
            return Answer(texts.SCENE_NOT_FOUND)
        return Answer(*render(best[0]))
