# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**Mikke** (みっけ, the cute Japanese "found it!"): a Telegram reverse image search bot whose persona is a mascot anime girl who finds where pictures come from. Production username **@reverseSearch2Bot**; bot usernames cannot change, and the display name "Mikke" is set once at deploy time (BotFather or `setMyName`), not by the bot at startup. A user sends an image, sticker, GIF or video (or replies `/sauce` to one in a group); the bot looks it up on SauceNAO and answers with the source: title, characters, artist, anime episode and timestamp, plus link buttons. The bot is in many public groups and gets roughly 13k updates a day.

History:

- The first commit of this repo imports [kawaiiDango/reverseSearchBot](https://github.com/kawaiiDango/reverseSearchBot) at `5845465` (Node.js, Telegraf 4, Apache-2.0). It was imported rather than forked because the Python rewrite shares no code with it. The Node.js code was deleted once the rewrite covered it; `git show b171949` has it.
- Production still runs an older fork of that code, [AnyByte/reverseSearchBot](https://github.com/AnyByte/reverseSearchBot) (2020, Telegraf 3, webhook mode). On 2026-09-27 it was revived after four months offline with local patches: SauceNAO JSON API instead of HTML scraping (now behind a Cloudflare challenge), dead SOCKS proxies removed, Node 22. Those patches are not in any git history; the result formatter they introduced is kept for reference in `docs/legacy/deployed-parseSauceNao.js`.
- This repo replaces both with a Python rewrite (not deployed yet, see Production).
- Before the first deployment the rewrite was renamed to Mikke: repo `reverse-search-bot` → `mikke-bot`, package `reverse_search_bot` → `mikke`, console script and container `rsbot` → `mikke`.

Single admin and sole developer: the user.

## Running

Python 3.13, uv, aiogram 3, httpx, pydantic-settings, cachetools; ruff; pytest with pytest-asyncio and respx. Conventions follow `~/Projects/nHentai` (same author, same stack).

- Local: `cp .env.example .env`, fill in a **test** bot token and the SauceNAO key, then `uv sync` and `uv run mikke`. `BOT_MODE=polling` is the default; it calls `deleteWebhook`, so it must never run with the production token.
- Tests: `uv run pytest`. Offline: Telegram is a `FakeSession` (`tests/conftest.py`) that records every Bot API call, SauceNAO is mocked with respx, and time is an injected `Clock`. Test data and update builders live in `tests/payloads.py`.
- Lint: `uv run ruff check .`. **Never run `ruff format` on the whole project.**
- Docker: `docker build .`; `docker-compose.yml` is the production compose file.

## Configuration

`src/mikke/config.py` `Settings` is the only place that reads the environment (and `.env`, gitignored; `.env.example` is the template, blank values count as unset).

`BOT_TOKEN`, `SAUCENAO_API_KEY` (required); `ADMIN_IDS`, `FAVOURITE_GROUPS` (comma-separated ids); `REPORT_RESULTS`, `REPORT_ERRORS` (default on); `BOT_MODE` (`polling` | `webhook`); `PUBLIC_URL` (base URL of the bot's HTTP server; required for webhook mode); `WEBHOOK_SECRET` (required for webhook mode); `WEB_HOST`, `WEB_PORT` (default `0.0.0.0:8080`); `LOG_LEVEL`.

## Architecture

Package `src/mikke/`:

- `__main__.py` — loads `Settings`, sets up logging through `RedactingFormatter` (bot token, SauceNAO key and webhook secret never reach the logs, tracebacks included), wires everything, starts the HTTP server in both modes, then either `setWebhook(<PUBLIC_URL>/, secret_token, allowed_updates)` and waits for SIGTERM, or `deleteWebhook` + long polling. On SIGTERM in webhook mode it stops the listener and gives running updates up to 8 s (`InFlight`) before closing.
- `bot.py` — `build_bot` (HTML parse mode, link previews off), `build_dispatcher` (DI: `searcher`, `reporter`, `in_flight`; error handler) and `InFlight`, an outer update middleware that counts updates in progress, because aiogram does not wait for its background webhook tasks.
- `handlers.py` — one `main` router (`/start`, `/help`, added to a group, inline query, chosen inline result) with a nested `search` router for everything that starts a search: the group trigger (`/sauce`, `/source`, `sauce`, `source`, `what?` in reply to media; commands without media get the usage hint), private media, photos in `FAVOURITE_GROUPS`. Flow: reply with the 🔍 "Mikke is looking..." placeholder, `Searcher`, edit the placeholder (or the inline message). `_edit` must not leave the placeholder spinning: on a 400 it retries without the buttons, on flood control it waits and retries once.
- `flood.py` — `FloodMiddleware`, an inner middleware on the `search` router, so only messages that would start a search count: more than 20 within 3 seconds per user are dropped. Time is the message date, so a backlog replayed after downtime is not a flood.
- `media.py` — `find_media` (largest photo; static sticker, or the thumbnail of animated/video stickers, GIFs and videos; image documents by extension) and `normalize_url` for inline queries.
- `search.py` — `Searcher`: 24h `TTLCache` of accepted matches (200 entries, keyed by `file_unique_id` or `url:<url>`, empty list = cached "no result"; errors and limits are not cached), quota check before downloading, `getFile` + download, SauceNAO, answer, owner report. Always returns an `Answer`; unexpected exceptions become the error text plus an error report. Download failures are re-raised as `DownloadError` without the aiohttp original, whose text quotes the file URL.
- `saucenao.py` — `SauceNao` client. Uploads bytes (multipart `file`) or passes a public URL (inline mode); never a Telegram file URL. Tracks `short_remaining`/`long_remaining` from the JSON header: short window exhausted blocks for 30 s, the daily one for 10 min (it is rolling, so it gets probed again), HTTP 429 likewise (daily if the message says so). The headers only describe finished requests, so it also counts its own sends and allows at most `short_limit` (4) per 30 s. While blocked, `QuotaExceededError` is raised without an HTTP call.
- `results.py` — pure functions: `select` (similarity >= 60, within 7 of the best), `render_text`, `links` (one per site, `Source`, MAL search for AniDB without a MAL link, max 6, first is "View on X"; only URLs Telegram accepts as buttons, since booru `source` fields are free text), `grid` (two per row, last row up to three), `keyboard` (None when empty), `fallback_keyboard` (Google Lens, SauceNAO, TinEye).
- `reports.py` — `Reporter`: to every `ADMIN_IDS` user, found results (SauceNAO link to the public image URL + text + buttons, then the original media resent by file_id with the matching send method, because Telegram cannot resend thumbnails) and errors (with the bot token replaced).
- `web.py` — the single aiohttp app: `POST /` (aiogram `SimpleRequestHandler`, webhook mode only, `X-Telegram-Bot-Api-Secret-Token` checked, 401 otherwise), `GET /img/<file_id>` (validates the id, `getFile` on every request, streams images only, `HEAD` without downloading, 404/502 otherwise), `GET /healthz`.
- `texts.py` — user-facing strings.

## Things to watch

- The bot token must never leave the process: SauceNAO gets bytes, fallback and report links use `<PUBLIC_URL>/img/<file_id>`. aiohttp errors from Telegram file downloads quote the file URL, token included, so never log or report them raw (`DownloadError`, the `/img/` warning, `Reporter.error`, `RedactingFormatter`). httpx logging is set to WARNING because it logs request URLs, and the SauceNAO `api_key` is in the query string.
- `/img/` calls `getFile` per request on purpose: file_ids stay valid, `file_path` links expire after about an hour, and fallback links sit in old messages.
- Inline results carry the 🔍 keyboard on purpose: without a keyboard Telegram sends no `inline_message_id` to edit. `chosen_inline_result` needs inline feedback enabled in @BotFather (`/setinlinefeedback`).
- Group keywords (`sauce` without a slash) and favourite-group photos only reach the bot with privacy mode off.
- Deliberately dropped from production: Amplitude analytics, proxy rotation, TinEye scraping, `webpToPng.php`, pm2, the storebot rating text, the TinEye `sn|` callback buttons, the `/privacy` command.

## Production

- Server `skrime-vps` (SSH alias), compose project `~/BOTS/mikke/`, service and container `mikke` (image `mikke:latest`) in the external Docker network `bots-network`, internal port 8080. `docker-compose.yml` in this repo is that compose file (`BOT_MODE=webhook`, no published ports, `.env` next to it).
- Traefik behind Cloudflare: `https://anybyte.org/telebot/rsbot` → `mikke:8080` with the `/telebot/rsbot` prefix stripped, so the bot sees `/`. `PUBLIC_URL=https://anybyte.org/telebot/rsbot`, so the webhook is `https://anybyte.org/telebot/rsbot/` and images are `https://anybyte.org/telebot/rsbot/img/<file_id>` over the same route. The bot registers the webhook with `WEBHOOK_SECRET` itself on start.
- The rename to Mikke changed the deployment plan, which used to be `~/BOTS/reverseSearchBot/` and container `rsbot`: the Traefik service URL has to point at `mikke:8080` instead of `rsbot:8080`. The public path `/telebot/rsbot` stays, so `PUBLIC_URL` and the webhook URL do not change. The display name is set once by hand: `setMyName` "Mikke" (or BotFather `/setname`).
- Still deployed for the old bot: `https://anybyte.org/rsbotphp` → `nginx:6004` (php tokenHider) and its php container. The rewrite does not use them; after the switch the Traefik route, the php container and the nginx `:6004` server block can be removed.
- Secrets (bot token, SauceNAO key, admin ids, webhook secret) never go into git.
- Do not touch the server or the production bot, and do not call Telegram with the production token, unless the user asks. Deployment is a separate step the user drives.

## Git and license

- Commit identity for this repo (set locally): `Nyasume <87500867+Nyasume@users.noreply.github.com>`.
- Remote: `git@github-nyasume:Nyasume/mikke-bot.git` (SSH host alias for the Nyasume account).
- Upstream is Apache-2.0: keep `LICENSE`, credit kawaiiDango/reverseSearchBot in `README.md` and `NOTICE`.
- `docs/superpowers/` is gitignored; do not commit it.
