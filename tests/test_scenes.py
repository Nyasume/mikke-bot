import asyncio

import httpx
import pytest
from aiogram.methods import GetFile

from mikke import texts
from mikke.media import Media
from mikke.reports import Hit
from mikke.scenes import Alert, hit, render
from mikke.tracemoe import SEARCH_URL
from payloads import SCENE, trace_response

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
        side_effect=[httpx.Response(500, text="boom"), httpx.Response(200, json=trace_response([SCENE]))]
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
