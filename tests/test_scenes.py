import asyncio

import httpx
import pytest
from aiogram.methods import GetFile

from mikke import texts
from mikke.media import Media
from mikke.reports import Hit, Quota
from mikke.scenes import Alert, SceneSearcher, hit, render
from mikke.tracemoe import SEARCH_URL, TraceMoe
from payloads import CLOUDFLARE_DOWN, SCENE, trace_response

PHOTO = Media("photo-file-id", "photo-unique", "photo", "photo-file-id")


def _scene(**changes) -> dict:
    return {**SCENE, **changes}


def _anilist(**changes) -> dict:
    return {**SCENE["anilist"], **changes}


def _links(markup) -> list[tuple[str, str]]:
    return [(button.text, button.url) for row in markup.inline_keyboard for button in row]


def test_scene_shows_titles_episode_time_and_similarity():
    text, markup = render(SCENE)

    assert text == (
        "🎬 <b>Is the Order a Rabbit?</b>\n"
        "<i>Gochuumon wa Usagi Desu ka?</i>\n"
        "<b>Episode: </b>3\n"
        "<b>Time: </b>12:05 - 12:09\n"
        "<b>Similarity: </b>96.1%"
    )
    assert _links(markup) == [
        ("AniList", "https://anilist.co/anime/20517"),
        ("MyAnimeList", "https://myanimelist.net/anime/21273"),
    ]


def test_scene_for_the_owner_report():
    assert hit(SCENE) == Hit(
        "Is the Order a Rabbit?",
        96.1,
        (("AniList", "https://anilist.co/anime/20517"), ("MyAnimeList", "https://myanimelist.net/anime/21273")),
    )
    assert hit(_scene(anilist=99939, similarity=0.5)) == Hit(
        "Gochuumon wa Usagi desu ka - 03 (BD 1280x720).mp4", 50.0, (("AniList", "https://anilist.co/anime/99939"),)
    )


@pytest.mark.parametrize(("similarity", "shown"), [(0.874, "87.4%"), (0.5, "50.0%")])
def test_below_90_percent_is_only_a_weak_match(similarity, shown):
    text, _ = render(_scene(similarity=similarity))
    assert f"<b>Similarity: </b>{shown}\n{texts.SCENE_WEAK}" in text
    assert text.endswith("Mikke isn't sure about this one.</i>")


def test_weak_match_follows_the_rounded_similarity():
    text, _ = render(_scene(similarity=0.89996))
    assert text.endswith("<b>Similarity: </b>90.0%")


def test_romaji_alone_when_there_is_no_english_title():
    text, _ = render(_scene(anilist=_anilist(title={"native": "けいおん!", "romaji": "K-On!", "english": None})))
    assert text.startswith("🎬 <b>K-On!</b>\n<b>Episode: </b>")


def test_same_english_and_romaji_title_is_shown_once():
    text, _ = render(_scene(anilist=_anilist(title={"romaji": "Clannad", "english": "Clannad"})))
    assert text.startswith("🎬 <b>Clannad</b>\n<b>Episode: </b>")


def test_titles_are_escaped():
    text, _ = render(_scene(anilist=_anilist(title={"romaji": "A <b> & C", "english": None})))
    assert text.startswith("🎬 <b>A &lt;b&gt; &amp; C</b>\n")


def test_without_mal_id_there_is_only_the_anilist_button():
    _, markup = render(_scene(anilist=_anilist(idMal=None)))
    assert _links(markup) == [("AniList", "https://anilist.co/anime/20517")]


def test_bare_anilist_id_falls_back_to_the_file_name():
    text, markup = render(_scene(anilist=99939))
    assert text.startswith("🎬 <b>Gochuumon wa Usagi desu ka - 03 (BD 1280x720).mp4</b>\n<b>Episode: </b>3\n")
    assert _links(markup) == [("AniList", "https://anilist.co/anime/99939")]


@pytest.mark.parametrize(
    ("fields", "episode"),
    [
        ({"episode_start": 1, "episode_end": 2, "episode": [1, 2]}, "1-2"),
        # specials have no AniList episode; the file name still says something
        ({"episode_start": None, "episode_end": None, "episode": "OVA"}, "OVA"),
        ({"episode_start": None, "episode_end": None, "episode": [7, 8]}, "7-8"),
        ({"episode_start": None, "episode_end": None, "episode": 12.5}, "12.5"),
    ],
)
def test_episode(fields, episode):
    text, _ = render(_scene(**fields))
    assert f"\n<b>Episode: </b>{episode}\n" in text


def test_unknown_episode_is_left_out():
    text, _ = render(_scene(episode_start=None, episode_end=None, episode=None))
    assert "Episode" not in text


@pytest.mark.parametrize(
    ("start", "end", "shown"),
    [
        (0.4, 3.2, "00:00 - 00:03"),
        (97.7226, 98.8905, "01:37 - 01:38"),
        (61.1, 61.9, "01:01"),
        (3725.0, 3731.5, "1:02:05 - 1:02:11"),
    ],
)
def test_time_range(start, end, shown):
    text, _ = render(_scene(**{"from": start, "to": end}))
    assert f"\n<b>Time: </b>{shown}\n" in text


# --- the same image, searched at the same time ---------------------------------


async def test_concurrent_presses_on_the_same_image_ask_trace_moe_once(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE]))
    harness.session.slow = True

    first, second = await asyncio.gather(
        harness.scenes.search(harness.bot, PHOTO),
        harness.scenes.search(harness.bot, Media("another-id", "photo-unique", "photo", "another-id")),
    )

    assert route.call_count == 1
    assert (first.outcome.cached, second.outcome.cached) == (False, True)
    assert second.text == first.text


async def test_a_press_that_waited_for_a_failed_search_runs_its_own(respx_mock, harness):
    route = respx_mock.post(SEARCH_URL).mock(
        side_effect=[httpx.Response(400, text="boom"), httpx.Response(200, json=trace_response([SCENE]))]
    )
    harness.session.slow = True

    failed, found = await asyncio.gather(
        harness.scenes.search(harness.bot, PHOTO), harness.scenes.search(harness.bot, PHOTO)
    )

    assert isinstance(failed, Alert)
    assert failed.outcome.status == "error"
    assert (found.outcome.status, found.outcome.cached) == ("found", False)
    assert route.call_count == 2


@pytest.mark.parametrize(
    "scene", ["not a scene", {**SCENE, "anilist": {"id": 1, "title": "not a mapping"}}], ids=["string", "title"]
)
async def test_a_scene_that_cannot_be_shown_is_an_error_and_is_not_cached(respx_mock, harness, scene):
    route = respx_mock.post(SEARCH_URL).respond(json=trace_response([scene]))

    alert = await harness.scenes.search(harness.bot, PHOTO)
    await harness.scenes.search(harness.bot, PHOTO)

    assert isinstance(alert, Alert)
    assert (alert.text, alert.outcome.status) == (texts.SCENE_ERROR, "error")
    assert route.call_count == 2


async def test_trace_moe_down_is_an_alert_and_no_error_log(respx_mock, harness, caplog):
    route = respx_mock.post(SEARCH_URL).respond(521, html=CLOUDFLARE_DOWN)

    alert = await harness.scenes.search(harness.bot, PHOTO)

    assert isinstance(alert, Alert)
    assert alert.text == texts.SCENE_ERROR
    assert alert.outcome.status == "error"
    assert alert.outcome.error == "UnavailableError: HTTP 521 (Cloudflare: web server is down)"
    assert route.call_count == 2
    # trace.moe's outage, not our bug: no error for Sentry
    assert {record.levelname for record in caplog.records if record.name.startswith("mikke")} == {"WARNING"}
    # an outage is not cached
    respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE]))
    assert (await harness.scenes.search(harness.bot, PHOTO)).outcome.status == "found"


async def test_presses_in_line_for_trace_moe_download_nothing_once_the_quota_is_gone(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(402, json={"error": "Search quota depleted (quota per 24 hours: 100)"})
    harness.session.slow = True

    first, second = await asyncio.gather(
        harness.scenes.search(harness.bot, PHOTO),
        harness.scenes.search(harness.bot, Media("other-id", "other-unique", "photo", "other-id")),
    )

    assert first.outcome.status == second.outcome.status == "limit"
    # the second file is fetched only once its search has trace.moe's turn, and by then there is no quota
    assert [call.file_id for call in harness.session.calls(GetFile)] == ["photo-file-id"]


# --- the quota, for the owner report -------------------------------------------


async def test_the_quota_goes_into_the_outcome_cached_or_not(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE], quota_used=11))

    first = await harness.scenes.search(harness.bot, PHOTO)
    cached = await harness.scenes.search(harness.bot, PHOTO)

    assert first.outcome.quota == Quota(12, 100, "guest")
    assert (cached.outcome.cached, cached.outcome.quota) == (True, Quota(12, 100, "guest"))


async def test_no_quota_before_trace_moe_has_told_it(respx_mock, harness):
    respx_mock.post(SEARCH_URL).respond(402, json={"error": "Concurrency limit exceeded"})

    alert = await harness.scenes.search(harness.bot, PHOTO)

    assert isinstance(alert, Alert)
    assert alert.outcome.quota is None


async def test_a_used_up_quota_stays_known_without_calling_trace_moe(respx_mock, harness):
    depleted = {"quota": 100, "quotaUsed": 100, "error": "Search quota depleted (quota per 24 hours: 100, used: 100)"}
    route = respx_mock.post(SEARCH_URL).respond(402, json=depleted)

    await harness.scenes.search(harness.bot, PHOTO)
    alert = await harness.scenes.search(harness.bot, Media("other-id", "other-unique", "photo", "other-id"))

    assert route.call_count == 1
    assert (alert.outcome.status, alert.outcome.quota) == ("limit", Quota(100, 100, "guest"))


async def test_a_sponsors_key_is_named_but_not_shown(respx_mock, harness, http):
    respx_mock.post(SEARCH_URL).respond(json=trace_response([SCENE], quota=1000, quota_used=11))

    answer = await SceneSearcher(TraceMoe(http, "sponsor-key")).search(harness.bot, PHOTO)

    assert answer.outcome.quota == Quota(12, 1000, "sponsor key")
    assert "sponsor-key" not in repr(answer.outcome)
