import json
from pathlib import Path

import pytest

from mikke import texts
from mikke.results import fallback_keyboard, grid, keyboard, links, render, render_text, select
from payloads import ANIME, result

SAMPLE_NOMATCH = Path(__file__).parent.parent / "docs" / "legacy" / "saucenao-sample-nomatch.json"


def _layout(markup) -> list[list[str]]:
    return [[button.text for button in row] for row in markup.inline_keyboard]


def test_sample_without_match_selects_nothing():
    response = json.loads(SAMPLE_NOMATCH.read_text())
    assert len(response["results"]) == 5
    assert select(response["results"]) == []


def test_select_keeps_results_within_tolerance_of_the_best():
    results = [result(70.0, n=3), result(95.1, n=1), result(88.2, n=2), result(59.9, n=4), result(87.0, n=5)]
    assert [data["n"] for data in select(results)] == [1, 2]


def test_select_skips_malformed_similarity():
    assert select([{"header": {}, "data": {"title": "x"}}, result(80, title="ok")]) == [{"title": "ok"}]


def test_anime_result_shows_episode_year_and_time():
    text, markup = render(select([ANIME]))
    assert text == "<b>One Piece (Ep. 12)</b>\n<b>Year: </b>1999-1999\n<b>Time: </b>00:12:33 / 00:24:40"
    assert _layout(markup) == [["View on AniDB", "MAL", "AniList"]]
    assert markup.inline_keyboard[0][1].url == "https://myanimelist.net/anime/21/"


def test_anidb_without_mal_link_gets_a_mal_search_button():
    anidb = result(90, ext_urls=["https://anidb.net/perl-bin/animedb.pl?show=anime&aid=5"], source="K-On!", anidb_aid=5, part="3")
    text, markup = render(select([anidb]))
    assert text == "<b>K-On! (Ep. 3)</b>"
    assert _layout(markup) == [["View on AniDB", "MAL"]]
    assert markup.inline_keyboard[0][1].url == "https://myanimelist.net/anime.php?q=K-On%21"


def test_booru_and_pixiv_with_tolerance_cut_and_html_escaping():
    danbooru = result(
        95.1,
        ext_urls=["https://danbooru.donmai.us/post/show/1", "https://gelbooru.com/index.php?page=post&s=view&id=2"],
        danbooru_id=1,
        gelbooru_id=2,
        creator="artist <x>",
        material="fate/grand order",
        characters="saber & archer",
        source="https://i.pximg.net/img-original/img/1_p0.png",
    )
    pixiv = result(
        90.2,
        ext_urls=["https://www.pixiv.net/member_illust.php?mode=medium&illust_id=3"],
        title="Tom & Jerry <3",
        pixiv_id=3,
        member_name="pixiv artist",
    )
    too_far = result(87.0, ext_urls=["https://yande.re/post/show/4"], creator="cut off")
    too_low = result(55.0, ext_urls=["https://e621.net/post/show/5"], title="too low")

    text, markup = render(select([danbooru, pixiv, too_far, too_low]))

    assert text == (
        "<b>Tom &amp; Jerry &lt;3</b>\n"
        "<b>Character: </b>saber &amp; archer\n"
        "<b>Material: </b>fate/grand order\n"
        "<b>By: </b>artist &lt;x&gt;"
    )
    assert _layout(markup) == [["View on Danbooru", "Gelbooru"], ["Source", "Pixiv"]]
    assert markup.inline_keyboard[1][0].url == "https://i.pximg.net/img-original/img/1_p0.png"


def test_twitter_result_shows_the_handle():
    tweet = result(
        88.0,
        ext_urls=["https://twitter.com/i/web/status/1234"],
        created_at="2020-01-01T00:00:00Z",
        tweet_id="1234",
        twitter_user_id="55",
        twitter_user_handle="some_artist",
    )
    text, markup = render(select([tweet]))
    assert text == "<b>By: </b>@some_artist"
    assert _layout(markup) == [["View on Twitter"]]


def test_doujin_with_creator_list_and_no_links_has_no_keyboard():
    doujin = result(
        91.0,
        source="Original",
        creator=["Artist A", "Artist B"],
        eng_name="[Circle] Some Doujin",
        jp_name="(C90) [サークル] 同人誌",
    )
    text, markup = render(select([doujin]))
    assert text == "<b>Original</b>\n<b>By: </b>Artist A, Artist B"
    assert markup is None


def test_title_falls_back_to_eng_then_jp_name():
    assert render_text([{"source": "https://x.test/1", "eng_name": "English"}]) == "<b>English</b>"
    assert render_text([{"jp_name": "日本語"}]) == "<b>日本語</b>"


def test_first_non_empty_value_across_results_wins():
    matches = [{"title": "", "member_name": ""}, {"title": "Second", "author_name": "da artist"}]
    assert render_text(matches) == "<b>Second</b>\n<b>By: </b>da artist"


def test_part_without_anidb_is_its_own_line():
    assert render_text([{"title": "Manga", "part": "Chapter 5"}]) == "<b>Manga</b>\n<b>Part: </b>Chapter 5"


def test_nothing_to_show_says_no_title():
    assert render_text([{"ext_urls": ["https://danbooru.donmai.us/post/show/1"]}]) == texts.NO_TITLE


def test_grid_is_two_per_row_with_up_to_three_on_the_last():
    shapes = {n: [len(row) for row in grid(list(range(n)))] for n in range(1, 7)}
    assert shapes == {1: [1], 2: [2], 3: [3], 4: [2, 2], 5: [2, 3], 6: [2, 2, 2]}


def test_links_are_one_per_site_and_at_most_six():
    urls = [
        "https://danbooru.donmai.us/post/show/1",
        "https://danbooru.donmai.us/post/show/2",
        "https://gelbooru.com/x",
        "https://yande.re/post/show/3",
        "https://konachan.com/post/show/4",
        "https://www.pixiv.net/artworks/5",
        "https://e621.net/post/show/6",
        "https://unknown.example/7",
        "not a url",
    ]
    buttons = links([{"ext_urls": urls}])
    assert [label for label, _ in buttons] == ["View on Danbooru", "Gelbooru", "yandere", "Konachan", "Pixiv", "e621"]
    assert buttons[0][1] == "https://danbooru.donmai.us/post/show/1"
    assert _layout(keyboard(buttons)) == [["View on Danbooru", "Gelbooru"], ["yandere", "Konachan"], ["Pixiv", "e621"]]


def test_unknown_site_is_labelled_by_host():
    assert links([{"ext_urls": ["https://www.example.com/a"]}]) == [("View on example.com", "https://www.example.com/a")]


def test_no_links_means_no_keyboard():
    assert keyboard([]) is None


def test_fallback_keyboard_links_the_image_to_six_engines():
    markup = fallback_keyboard("https://example.org/mikke/img/abc")
    assert _layout(markup) == [["Google Lens", "Yandex", "Bing"], ["SauceNAO", "ascii2d", "TinEye"]]
    encoded = "https%3A%2F%2Fexample.org%2Fmikke%2Fimg%2Fabc"
    assert [button.url for row in markup.inline_keyboard for button in row] == [
        f"https://lens.google.com/uploadbyurl?url={encoded}",
        f"https://yandex.com/images/search?rpt=imageview&url={encoded}",
        f"https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIVSP&sbisrc=UrlPaste&q=imgurl:{encoded}",
        f"https://saucenao.com/search.php?url={encoded}",
        f"https://ascii2d.net/search/url/{encoded}?type=color",
        f"https://tineye.com/search?url={encoded}",
    ]
    assert fallback_keyboard(None) is None


@pytest.mark.parametrize(
    "source",
    [
        "https://i.pximg.net/a.png https://twitter.com/b/status/1",
        "https://twitter.com/b/status/1 (edited)",
        "https://localhost/a.png",
        "http://",
    ],
)
def test_free_text_sources_do_not_become_buttons(source):
    matches = [{"ext_urls": ["https://danbooru.donmai.us/post/show/1"], "source": source}]
    assert [label for label, _ in links(matches)] == ["View on Danbooru"]
    # still a URL, so not a title either
    assert render_text(matches) == texts.NO_TITLE
