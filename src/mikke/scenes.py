"""The anime scene search: trace.moe on demand, behind the button under an answer."""

import html
import logging
from dataclasses import dataclass, replace
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from cachetools import TTLCache

from mikke import texts
from mikke.media import Media
from mikke.reports import Hit, Outcome, Quota
from mikke.results import keyboard
from mikke.search import CACHE_SIZE, CACHE_TTL_SECONDS, Answer, InvalidFileError, KeyLocks, download
from mikke.tracemoe import QuotaExceededError, TraceMoe, UnavailableError

logger = logging.getLogger(__name__)

# trace.moe: "Similarity lower than 90% are most likely incorrect results"
WEAK_SIMILARITY = 0.9

Scene = dict[str, Any]  # one trace.moe result


@dataclass(frozen=True)
class Alert:
    """A popup on the pressed button instead of a message."""

    text: str
    # how the search went, for the owner report
    outcome: Outcome


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


def _info(scene: Scene) -> dict[str, Any]:
    anilist = scene.get("anilist")
    # a bare AniList id when trace.moe could not add the AniList info
    return anilist if isinstance(anilist, dict) else {"id": anilist}


def _titles(scene: Scene) -> tuple[str, str | None]:
    """The title, and the romaji one to show below it when it differs."""
    titles = _info(scene).get("title") or {}
    english, romaji = _clean(titles.get("english")), _clean(titles.get("romaji"))
    title = english or romaji or _clean(titles.get("native")) or _clean(scene.get("filename")) or texts.NO_TITLE
    return title, romaji if english and romaji and romaji != english else None


def _similarity(scene: Scene) -> float:
    similarity = scene.get("similarity")
    # rounded as shown, so 89.99% does not read "90.0%, weak match"
    return round(similarity, 3) if isinstance(similarity, int | float) else 0.0


def _links(scene: Scene) -> list[tuple[str, str]]:
    info, links = _info(scene), []
    if isinstance(anilist_id := info.get("id"), int):
        links.append(("AniList", f"https://anilist.co/anime/{anilist_id}"))
    if isinstance(mal_id := info.get("idMal"), int):
        links.append(("MyAnimeList", f"https://myanimelist.net/anime/{mal_id}"))
    return links


def render(scene: Scene) -> tuple[str, InlineKeyboardMarkup | None]:
    title, romaji = _titles(scene)
    lines = [f"🎬 <b>{html.escape(title, quote=False)}</b>"]
    if romaji:
        lines.append(f"<i>{html.escape(romaji, quote=False)}</i>")
    fields = [("Episode", _episode(scene)), ("Time", _time_range(scene))]
    lines += [f"<b>{name}: </b>{html.escape(value, quote=False)}" for name, value in fields if value]
    similarity = _similarity(scene)
    lines.append(f"<b>Similarity: </b>{similarity:.1%}")
    if similarity < WEAK_SIMILARITY:
        lines.append(texts.SCENE_WEAK)
    return "\n".join(lines), keyboard(_links(scene))


def hit(scene: Scene) -> Hit:
    """The scene, for the owner report."""
    return Hit(_titles(scene)[0], round(_similarity(scene) * 100, 1), tuple(_links(scene)))


def _answer(best: list[Scene], cached: bool = False) -> Answer:
    """The answer for the best scene, or for none."""
    if not best:
        return Answer(texts.SCENE_NOT_FOUND, Outcome("not_found", cached))
    text, markup = render(best[0])
    return Answer(text, Outcome("found", cached, (hit(best[0]),)), markup)


class SceneSearcher:
    """Cache, then trace.moe, then the answer; only ever run when someone asks."""

    def __init__(self, tracemoe: TraceMoe, cache: TTLCache | None = None) -> None:
        self._tracemoe = tracemoe
        # the best scene by file_unique_id; an empty list is a cached "not found"
        self._cache: TTLCache[str, list[Scene]] = (
            cache if cache is not None else TTLCache(maxsize=CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
        )
        self._locks = KeyLocks()

    async def search(self, bot: Bot, media: Media) -> Answer | Alert:
        # presses on the same image wait for the first one and find its answer cached
        async with self._locks.hold(media.file_unique_id):
            return self._with_quota(await self._search(bot, media))

    def _with_quota(self, result: Answer | Alert) -> Answer | Alert:
        """The result, with the quota as trace.moe last reported it, for the owner report.

        Nothing runs between trace.moe's answer and this, so a search gets the numbers
        that came with it; a cached answer or a used up quota gets the last ones known.
        """
        if (usage := self._tracemoe.usage) is None:
            return result
        used, limit = usage
        quota = Quota(used, limit, "sponsor key" if self._tracemoe.sponsored else "guest")
        return replace(result, outcome=replace(result.outcome, quota=quota))

    async def _search(self, bot: Bot, media: Media) -> Answer | Alert:
        key = media.file_unique_id
        best = self._cache.get(key)
        if best is not None:
            return _answer(best, cached=True)
        try:
            # no point in waiting for a turn while the quota is used up
            self._tracemoe.check_quota()
            best = (await self._tracemoe.search(lambda: download(bot, media)))[:1]
            # shown before it is cached: a scene that cannot be shown is one error, not a day of them
            answer = _answer(best)
        except QuotaExceededError:
            return Alert(texts.SCENE_LIMIT, Outcome("limit"))
        except InvalidFileError as e:
            logger.info("Invalid file %s: %s", key, e)
            return Alert(texts.SCENE_INVALID_FILE, Outcome("invalid_file", error=str(e)))
        except UnavailableError as e:
            # trace.moe is down, not our bug
            logger.warning("trace.moe did not answer the scene search for %s: %s", key, e)
            return Alert(texts.SCENE_ERROR, Outcome("error", error=f"{type(e).__name__}: {e}"))
        except Exception as e:
            logger.exception("Scene search failed for %s", key)
            return Alert(texts.SCENE_ERROR, Outcome("error", error=f"{type(e).__name__}: {e}"))
        self._cache[key] = best
        return answer
