from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from aiogram.types import Message

IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "bmp"})

MediaKind = Literal["photo", "sticker", "document", "video"]


@dataclass(frozen=True)
class Media:
    """What to search in a message.

    `file_id` is the image sent to SauceNAO: the thumbnail for videos, GIFs and
    animated stickers, which Telegram does not let bots resend. The owner report
    resends the original media instead, hence `kind` and `original_file_id`.
    """

    file_id: str
    file_unique_id: str
    kind: MediaKind
    original_file_id: str


def find_media(message: Message) -> Media | None:
    if message.photo:
        largest = message.photo[-1]
        return Media(largest.file_id, largest.file_unique_id, "photo", largest.file_id)

    if sticker := message.sticker:
        if not (sticker.is_animated or sticker.is_video):
            return Media(sticker.file_id, sticker.file_unique_id, "sticker", sticker.file_id)
        if thumb := sticker.thumbnail:
            return Media(thumb.file_id, thumb.file_unique_id, "sticker", sticker.file_id)
        return None

    if document := message.document:
        name = document.file_name or ""
        if "." in name and name.rsplit(".", 1)[1].lower() in IMAGE_EXTENSIONS:
            return Media(document.file_id, document.file_unique_id, "document", document.file_id)
        # Telegram GIFs are mp4 documents
        if document.mime_type == "video/mp4" and (thumb := document.thumbnail):
            return Media(thumb.file_id, thumb.file_unique_id, "document", document.file_id)
        return None

    if (video := message.video) and (thumb := video.thumbnail):
        return Media(thumb.file_id, thumb.file_unique_id, "video", video.file_id)

    return None


def normalize_url(text: str) -> str | None:
    """Return `text` as an http(s) URL if it looks like one (the scheme is optional)."""
    text = text.strip()
    if not text or any(char.isspace() for char in text):
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or "." not in host.strip("."):
        return None
    return text
