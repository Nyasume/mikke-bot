import pytest

from mikke import texts
from mikke.scenes import render
from payloads import SCENE


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
