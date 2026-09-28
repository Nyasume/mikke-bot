# Mikke

<p align="center"><img src="assets/mikke.png" width="320" alt="Mikke, a chibi anime girl detective with pink twin tails holding up a magnifying glass"></p>

Mikke (みっけ, "found it!") is a Telegram bot, [@reverseSearch2Bot](https://t.me/reverseSearch2Bot) and [@MikkeSauceBot](https://t.me/MikkeSauceBot), who finds where pictures come from. Show her an image, a sticker, a GIF or a video and she answers with the source: title, characters, artist, anime episode and timestamp, with links to the sites she found it on. Searches go to [SauceNAO](https://saucenao.com).

- Private chat: send a picture, a sticker, an image file, a GIF or a video.
- Groups: reply to a media message with `/sauce` or `/source` (or just `sauce`, `source`, `what?`).
- Inline: type `@reverseSearch2Bot <image URL>` (or `@MikkeSauceBot <image URL>`) in any chat.

Both bots are the same Mikke: one process answers them, and they share the searches, the cache, the limits and the users' own keys.

When nothing is found, or the SauceNAO limit is reached, Mikke links the image to Google Lens, Yandex, Bing, SauceNAO, ascii2d and TinEye instead.

SauceNAO gives a free key 4 searches per 30 seconds and 100 a day. When a burst of pictures (an album, say) fills the 30 seconds, the searches wait their turn, up to about a minute, instead of failing.

### Your own SauceNAO key

Anyone can bring their own free SauceNAO key, so their searches use their own limits instead of Mikke's shared ones:

- `/apikey` in a private chat (or the 🔑 **Add my free key** button under a limit answer there) explains how to get one: sign up at [saucenao.com](https://saucenao.com/user.php), open the [API page](https://saucenao.com/user.php?page=search-api), copy the key and send it to Mikke.
- Sending the key itself, or `/apikey <key>`, checks it with one real SauceNAO search, saves it and shows the account's limits. A key SauceNAO does not know is not saved.
- Their searches in private, with `/sauce` in groups and inline then use their key. When it is used up for the day, or SauceNAO stops accepting it, Mikke's shared key stands in; a rejected key is mentioned to them once. Photos searched automatically in favourite groups always use the shared key.
- `/apikey remove` or the 🗑 button forgets the key. Mikke only ever shows its last four characters.
- A key sent with `/apikey` in a group is never saved; Mikke deletes the message if she may and suggests generating a new key.

Keys are kept in SQLite (`mikke.sqlite3` in `DATA_DIR`, a Docker volume in production), shared by both bots, and never logged, reported or sent to Sentry.

Answers in chats also carry a 🎬 **Anime scene** button. It asks [trace.moe](https://trace.moe) for the anime, episode and moment of the picture and replies with the titles, the time range, the similarity and AniList / MyAnimeList links. It runs only when someone presses it: trace.moe gives a guest 100 searches a day, one at a time (`TRACE_MOE_API_KEY` raises that for sponsors).

## Running

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
cp .env.example .env    # fill in BOT_TOKEN (a test bot) and SAUCENAO_API_KEY
uv sync
uv run mikke
```

`BOT_TOKEN` is the primary bot; `EXTRA_BOT_TOKENS` (comma-separated) adds more bots to the same process. `BOT_MODE=polling` (the default) is for local runs. Users' keys go to `./data/mikke.sqlite3` (`DATA_DIR`, gitignored). Production runs `BOT_MODE=webhook` in Docker: GitHub Actions tests every push, builds `main` into `ghcr.io/nyasume/mikke-bot`, and deploys when the CI workflow is run by hand with `deploy` checked. See `.env.example` for all settings.

`ADMIN_IDS` get owner reports: an activity report for every search (`REPORT_RESULTS`), a rich message with the searched image, what Mikke found (or that she found nothing, ran out of searches or failed), who asked, in which chat, through which bot, whether the answer came from the cache, whether the user's own key searched, and how much of the SauceNAO or trace.moe quota for the last 24 hours is used (the shared key's or the user's own, never the key); who added, removed or had a rejected key of their own (never the key); and every error (`REPORT_ERRORS`). When Telegram rejects the rich message, the report comes as plain text with the picture resent.

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

With `SENTRY_DSN` set, unexpected errors go to [Sentry](https://sentry.io): handler and HTTP route exceptions and everything logged at ERROR. Used up quotas, SauceNAO or trace.moe being down, empty results, flood protection, users' keys SauceNAO rejects and users who blocked the bot or deleted a message are not errors and stay in the logs. Events name the bot that received the update. The bot tokens, the API keys (users' own among them) and the webhook secrets are filtered out of every event and breadcrumb, and no Telegram user data beyond numeric ids is sent. Docker images built by CI carry the release `mikke-bot@<commit sha>` (`SENTRY_RELEASE` build arg).

## Credits

Mikke started as [kawaiiDango/reverseSearchBot](https://github.com/kawaiiDango/reverseSearchBot) (Node.js, Apache-2.0), later run as the fork [AnyByte/reverseSearchBot](https://github.com/AnyByte/reverseSearchBot). The first commit of this repository imports upstream `5845465`; the current code is a from-scratch Python rewrite of its behaviour. See [NOTICE](NOTICE).

Licensed under the [Apache License 2.0](LICENSE).
