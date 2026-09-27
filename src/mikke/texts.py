"""User-facing strings (HTML parse mode)."""

HELP = (
    "Hi, I'm Mikke! 🔍 Show me a picture, a sticker, an image file or a GIF, "
    "and I'll find where it comes from.\n\n"
    "Anime screenshot? Tap 🎬 Anime scene under my answer and I'll find the episode and the moment.\n\n"
    "<i>In a group, reply to a picture with /sauce or /source (note the slash) and I'll go look.</i>"
)
USAGE = (
    "Mikke needs something to look at!\n\n"
    "Send me a picture, a GIF, a sticker or an image file in private, "
    "or reply to one in a group with /sauce or /source."
)

LOADING = "<i>Mikke is looking...</i>"
LOADING_BUTTON = "🔍"

NO_RESULT = "Mikke looked everywhere, but couldn't find it. <i>Maybe another search engine will have better luck?</i>"
LIMIT_REACHED = "Mikke has searched so much she needs a little break. Please try again in a bit!"
INVALID_FILE = "<i>Mikke can't open this file.</i>"
ERROR = "<b>Oops!</b> Something went wrong while Mikke was looking. Please try again in a bit..."
NO_TITLE = "-no title-"

INLINE_TITLE = "Tap and Mikke will find where this picture comes from"

# The on-demand trace.moe search behind the button under an answer. Alerts are plain text, up to 200 characters.
SCENE_BUTTON = "🎬 Anime scene"
SCENE_WEAK = "<i>Only a weak match, so Mikke isn't sure about this one.</i>"
SCENE_NOT_FOUND = "Mikke couldn't find this scene in any anime she knows."
SCENE_NO_MEDIA = "Mikke can't see that picture anymore."
SCENE_INVALID_FILE = "Mikke can't open this file."
SCENE_LIMIT = "Mikke is out of anime scene searches for now. Please try again later!"
SCENE_ERROR = "Mikke couldn't reach trace.moe right now. Please try again later!"
