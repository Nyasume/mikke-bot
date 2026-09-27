# Mikke

<p align="center"><img src="assets/mikke.png" width="320" alt="Mikke, a chibi anime girl detective with pink twin tails holding up a magnifying glass"></p>

Mikke (みっけ, "found it!") is a Telegram bot, [@reverseSearch2Bot](https://t.me/reverseSearch2Bot), who finds where pictures come from. Show her an image, a sticker, a GIF or a video and she answers with the source: title, characters, artist, anime episode and timestamp, with links to the sites she found it on. Searches go to [SauceNAO](https://saucenao.com).

- Private chat: send a picture, a sticker, an image file, a GIF or a video.
- Groups: reply to a media message with `/sauce` or `/source` (or just `sauce`, `source`, `what?`).
- Inline: type `@reverseSearch2Bot <image URL>` in any chat.

When nothing is found, or the SauceNAO limit is reached, Mikke links the image to Google Lens, Yandex, Bing, SauceNAO, ascii2d and TinEye instead.

Answers in chats also carry a 🎬 **Anime scene** button. It asks [trace.moe](https://trace.moe) for the anime, episode and moment of the picture and replies with the titles, the time range, the similarity and AniList / MyAnimeList links. It runs only when someone presses it: trace.moe gives a guest 100 searches a day, one at a time (`TRACE_MOE_API_KEY` raises that for sponsors).

## Running

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
cp .env.example .env    # fill in BOT_TOKEN (a test bot) and SAUCENAO_API_KEY
uv sync
uv run mikke
```

`BOT_MODE=polling` (the default) is for local runs. Production runs `BOT_MODE=webhook` in Docker: GitHub Actions tests every push, builds `main` into `ghcr.io/nyasume/mikke-bot`, and deploys when the CI workflow is run by hand with `deploy` checked. See `.env.example` for all settings.

The bot's HTTP server (port 8080) serves:

- `POST /` — the Telegram webhook (webhook mode only), checked against `WEBHOOK_SECRET`;
- `GET /img/<file_id>` — the Telegram file behind `file_id`, so fallback links can point at a public URL without the bot token in it;
- `GET /healthz`.

## Development

```sh
uv run pytest
uv run ruff check .
```

Tests are offline: Telegram is replaced by a fake session, SauceNAO and trace.moe are mocked with respx.

## Error monitoring

With `SENTRY_DSN` set, unexpected errors go to [Sentry](https://sentry.io): handler and HTTP route exceptions and everything logged at ERROR. Used up quotas, empty results, flood protection and users who blocked the bot or deleted a message are not errors and stay in the logs. The bot token, the API keys and the webhook secret are filtered out of every event and breadcrumb, and no Telegram user data beyond numeric ids is sent. Docker images built by CI carry the release `mikke-bot@<commit sha>` (`SENTRY_RELEASE` build arg).

## Credits

Mikke started as [kawaiiDango/reverseSearchBot](https://github.com/kawaiiDango/reverseSearchBot) (Node.js, Apache-2.0), later run as the fork [AnyByte/reverseSearchBot](https://github.com/AnyByte/reverseSearchBot). The first commit of this repository imports upstream `5845465`; the current code is a from-scratch Python rewrite of its behaviour. See [NOTICE](NOTICE).

Licensed under the [Apache License 2.0](LICENSE).
