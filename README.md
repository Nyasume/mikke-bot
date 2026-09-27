# Reverse Search Bot

Telegram bot [@reverseSearch2Bot](https://t.me/reverseSearch2Bot) that finds the source of an image, sticker, GIF or video: title, characters, artist, anime episode and timestamp, with links to the sites it was found on. Searches go to [SauceNAO](https://saucenao.com).

- Private chat: send a picture, a sticker, an image file, a GIF or a video.
- Groups: reply to a media message with `/sauce` or `/source` (or just `sauce`, `source`, `what?`).
- Inline: type `@reverseSearch2Bot <image URL>` in any chat.

When nothing is found, or the SauceNAO limit is reached, the bot links the image to Google Lens, SauceNAO and TinEye instead.

## Running

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13.

```sh
cp .env.example .env    # fill in BOT_TOKEN (a test bot) and SAUCENAO_API_KEY
uv sync
uv run reverse-search-bot
```

`BOT_MODE=polling` (the default) is for local runs. Production runs `BOT_MODE=webhook` in Docker (`docker compose up -d --build`); see `.env.example` for all settings.

The bot's HTTP server (port 8080) serves:

- `POST /` — the Telegram webhook (webhook mode only), checked against `WEBHOOK_SECRET`;
- `GET /img/<file_id>` — the Telegram file behind `file_id`, so fallback links can point at a public URL without the bot token in it;
- `GET /healthz`.

## Development

```sh
uv run pytest
uv run ruff check .
```

Tests are offline: Telegram is replaced by a fake session and SauceNAO is mocked with respx.

## Credits

This bot started as [kawaiiDango/reverseSearchBot](https://github.com/kawaiiDango/reverseSearchBot) (Node.js, Apache-2.0), later run as the fork [AnyByte/reverseSearchBot](https://github.com/AnyByte/reverseSearchBot). The first commit of this repository imports upstream `5845465`; the current code is a from-scratch Python rewrite of its behaviour. See [NOTICE](NOTICE).

Licensed under the [Apache License 2.0](LICENSE).
