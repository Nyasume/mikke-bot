# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Telegram reverse image search bot, production username **@reverseSearch2Bot**. A user sends an image, sticker, GIF or video (or replies `/sauce` to one in a group); the bot looks it up on SauceNAO and answers with the source: title, characters, artist, anime episode and timestamp, plus link buttons. The bot is in many public groups and gets roughly 13k updates a day.

History:

- The first commit of this repo imports [kawaiiDango/reverseSearchBot](https://github.com/kawaiiDango/reverseSearchBot) at `5845465` (Node.js, Telegraf 4, Apache-2.0). It was imported rather than forked because the Python rewrite shares no code with it.
- Production still runs an older fork of that code, [AnyByte/reverseSearchBot](https://github.com/AnyByte/reverseSearchBot) (2020, Telegraf 3, webhook mode). On 2026-09-27 it was revived after four months offline with local patches: SauceNAO JSON API instead of HTML scraping (now behind a Cloudflare challenge), dead SOCKS proxies removed, Node 22. Those patches are not in any git history; the result formatter they introduced is kept for reference in `docs/legacy/deployed-parseSauceNao.js`.
- This repo replaces both with a Python rewrite.

Single admin and sole developer: the user.

## Stack

Python 3.13, uv, aiogram 3, httpx, pydantic-settings; ruff; pytest with pytest-asyncio and respx. Follow the conventions of `~/Projects/nHentai` (same author, same stack): `src/` layout, a `Settings` class as the only place that reads the environment, `.env` gitignored with `.env.example` as the template, `BOT_MODE` switching webhook (prod) and long polling (local).

- Lint: `uv run ruff check .`. **Never run `ruff format` on the whole project.**
- Tests: `uv run pytest`. Tests are offline: no real Telegram or SauceNAO calls, mock HTTP with respx.

## Required behaviour

Parity with the production bot, simplified where noted.

Updates:

- `/start` and `/help` reply with the help text. The bot being added to a group (it appears in `new_chat_members`) also triggers the help text.
- Private chat: a photo (largest size), sticker (for animated and video stickers use the thumbnail), document with a jpg/jpeg/png/webp/bmp name, mp4 document (Telegram GIF, use its thumbnail) or video (thumbnail) starts a search.
- Groups: replying to a media message with `/sauce` or `/source` (also `@<botname>` forms), or with a message that is exactly `sauce`, `source` or `what?` (case-insensitive), searches that media and answers as a reply to it. The command without a reply to media answers with a short usage hint.
- `FAVOURITE_GROUPS` (list of chat ids, may be empty): every photo posted there is searched automatically.
- Inline mode: a query that looks like a URL returns one article, "Tap for reverse search by URL"; on `chosen_inline_result` the bot searches that URL and edits the inline message.
- Flood protection per user (production: more than 20 messages within 3 seconds are ignored).
- Flow: first answer "Pouring some sauce on it..." with a 🍝 placeholder button, then edit that message with the result, no-result, limit or error text.

Search:

- SauceNAO JSON API: `https://saucenao.com/search.php` with `output_type=2`, `api_key`, `db=999`, `numres=5`, `testmode=1`. Upload the image bytes (multipart `file`) instead of passing a Telegram file URL; that URL contains the bot token and production used to leak it to SauceNAO this way.
- Accept results with similarity of at least 60 and within 7 points of the best accepted result.
- Text: bold title (`title`, else `source` when it is not a URL, else `eng_name`, else `jp_name`; for AniDB results with `part`: `Title (Ep. N)`), then the lines Character (`characters`), Material (`material`), By (`creator`, which may be a list, else `member_name`, else `author_name`, else `@twitter_user_handle`), Part, Year, Time (`est_time`). For each field the first non-empty value across the accepted results wins. HTML-escape every value. Show `-no title-` if nothing is left.
- Buttons: `ext_urls` of all accepted results, one per site, labelled by host (Pixiv, Danbooru, Gelbooru, …); `Source` when `data.source` is a URL; a MyAnimeList search link for AniDB results. At most 6, two per row with up to three on the last row, the first labelled "View on X". No keyboard when there are no links (not an empty one).
- No accepted result: "No sauce found" with fallback links to Google Lens, SauceNAO and TinEye for the image.
- Quota: the free SauceNAO account allows 4 requests per 30 seconds and 100 per day, far below the traffic. Track `short_remaining` and `long_remaining` from each response header. While a window is exhausted, answer immediately with the "request limit has reached" text and the same fallback links instead of calling the API. Treat HTTP 429 the same way.
- Cache results per image (`file_unique_id`) or URL for 24 hours, about 200 entries.

Fallback links need a public, token-free image URL. Production uses `php/tokenHider.php` on a separate php-fpm container for this. The rewrite serves it from the bot's own HTTP server instead, e.g. `GET /img/<file_id>` streaming the Telegram file, under a configurable public base URL. Then the whole bot needs one route.

Owner reports: the admin ids (`ADMIN_IDS`) receive every found result (with the SauceNAO link) and the searched image, plus every error. The user wants this kept; make it switchable (`REPORT_RESULTS`, `REPORT_ERRORS`, default on).

Webhook: verify Telegram's secret token header (`X-Telegram-Bot-Api-Secret-Token`); production never did.

Deliberately dropped: Amplitude analytics, proxy rotation, TinEye scraping, `webpToPng.php`, pm2, the storebot rating text, the TinEye `sn|` callback buttons.

## Production

- Server `skrime-vps` (SSH alias), compose project `~/BOTS/reverseSearchBot/`, container `rsbot` in the external Docker network `bots-network`, internal port 8080.
- Traefik behind Cloudflare: `https://anybyte.org/telebot/rsbot` → `rsbot:8080` with the `/telebot/rsbot` prefix stripped, so the bot sees `/`. Webhook URL: `https://anybyte.org/telebot/rsbot/`. `https://anybyte.org/rsbotphp` → `nginx:6004` (php tokenHider); it becomes unnecessary once `/img/` exists.
- Secrets (bot token, SauceNAO key, admin ids) never go into git.
- Do not touch the server or the production bot, and do not call Telegram with the production token, unless the user asks. Deployment is a separate step the user drives.

## Git and license

- Commit identity for this repo (set locally): `Nyasume <87500867+Nyasume@users.noreply.github.com>`.
- Remote: `git@github-nyasume:Nyasume/reverse-search-bot.git` (SSH host alias for the Nyasume account).
- Upstream is Apache-2.0: keep `LICENSE`, credit kawaiiDango/reverseSearchBot in `README.md` and a `NOTICE` file.
