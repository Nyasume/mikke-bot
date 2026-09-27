# Mikke

<p align="center"><img src="assets/mikke.png" width="320" alt="Mikke, a chibi anime girl detective with pink twin tails holding up a magnifying glass"></p>

Mikke (みっけ, "found it!") is a Telegram bot, [@reverseSearch2Bot](https://t.me/reverseSearch2Bot) and [@MikkeSauceBot](https://t.me/MikkeSauceBot), who finds where pictures come from. Show her an image, a sticker, a GIF or a video and she answers with the source: title, characters, artist, anime episode and timestamp, with links to the sites she found it on. Searches go to [SauceNAO](https://saucenao.com).

- Private chat: send a picture, a sticker, an image file, a GIF or a video.
- Groups: reply to a media message with `/sauce` or `/source` (or just `sauce`, `source`, `what?`).
- Inline: type `@reverseSearch2Bot <image URL>` (or `@MikkeSauceBot <image URL>`) in any chat.

Both bots are the same Mikke: one process answers them, and they share the searches, the cache and the limits.

When nothing is found, or the SauceNAO limit is reached, Mikke links the image to Google Lens, Yandex, Bing, SauceNAO, ascii2d and TinEye instead.

Answers in chats also carry a 🎬 **Anime scene** button. It asks [trace.moe](https://trace.moe) for the anime, episode and moment of the picture and replies with the titles, the time range, the similarity and AniList / MyAnimeList links. It runs only when someone presses it: trace.moe gives a guest 100 searches a day, one at a time (`TRACE_MOE_API_KEY` raises that for sponsors).

## Running

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
cp .env.example .env    # fill in BOT_TOKEN (a test bot) and SAUCENAO_API_KEY
uv sync
uv run mikke
```

`BOT_TOKEN` is the primary bot; `EXTRA_BOT_TOKENS` (comma-separated) adds more bots to the same process. `BOT_MODE=polling` (the default) is for local runs. Production runs `BOT_MODE=webhook` in Docker: GitHub Actions tests every push, builds `main` into `ghcr.io/nyasume/mikke-bot`, and deploys when the CI workflow is run by hand with `deploy` checked. See `.env.example` for all settings.

`ADMIN_IDS` get owner reports: an activity report for every search (`REPORT_RESULTS`), a rich message with the searched image, what Mikke found (or that she found nothing, ran out of searches or failed), who asked, in which chat, through which bot and whether the answer came from the cache; and every error (`REPORT_ERRORS`). When Telegram rejects the rich message, the report comes as plain text with the picture resent.

The bot's HTTP server (port 8080) serves:

- `POST /` — the primary bot's Telegram webhook (webhook mode only), checked against `WEBHOOK_SECRET`;
- `POST /<bot id>/` — each extra bot's webhook, checked against a secret derived from `WEBHOOK_SECRET` and the bot id;
- `GET /img/<bot id>/<file_id>` — the Telegram file behind `file_id`, fetched through the bot that received it, so fallback links can point at a public URL without a bot token in it (`GET /img/<file_id>`, the older form, goes through the primary bot);
- `GET /healthz`.

## Development

```sh
uv run pytest
uv run ruff check .
```

Tests are offline: Telegram is replaced by a fake session, SauceNAO and trace.moe are mocked with respx.

## Error monitoring

With `SENTRY_DSN` set, unexpected errors go to [Sentry](https://sentry.io): handler and HTTP route exceptions and everything logged at ERROR. Used up quotas, empty results, flood protection and users who blocked the bot or deleted a message are not errors and stay in the logs. Events name the bot that received the update. The bot tokens, the API keys and the webhook secrets are filtered out of every event and breadcrumb, and no Telegram user data beyond numeric ids is sent. Docker images built by CI carry the release `mikke-bot@<commit sha>` (`SENTRY_RELEASE` build arg).

## Credits

Mikke started as [kawaiiDango/reverseSearchBot](https://github.com/kawaiiDango/reverseSearchBot) (Node.js, Apache-2.0), later run as the fork [AnyByte/reverseSearchBot](https://github.com/AnyByte/reverseSearchBot). The first commit of this repository imports upstream `5845465`; the current code is a from-scratch Python rewrite of its behaviour. See [NOTICE](NOTICE).

Licensed under the [Apache License 2.0](LICENSE).
