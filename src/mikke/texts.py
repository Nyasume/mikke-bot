"""User-facing strings (HTML parse mode)."""

HELP = (
    "Hi, I'm Mikke! 🔍 Show me a picture, a sticker, an image file or a GIF, "
    "and I'll find where it comes from.\n\n"
    "Anime screenshot? Tap 🎬 Anime scene under my answer and I'll find the episode and the moment.\n\n"
    "<i>In a group, reply to a picture with /sauce or /source (note the slash) and I'll go look.</i>\n\n"
    "🔑 Out of searches? /apikey gets you your own, for free."
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

# Users' own SauceNAO keys (/apikey). Only the tail of a key is ever shown: <code>…a1b2</code>.
API_PAGE = "https://saucenao.com/user.php?page=search-api"
ADD_KEY_BUTTON = "🔑 Add my free key"
API_PAGE_BUTTON = "Open the SauceNAO API page"
REMOVE_KEY_BUTTON = "🗑 Remove my key"
# after LIMIT_REACHED, in private, for someone without a key of their own
KEY_PITCH = (
    "🔑 <b>Psst!</b> With your own free SauceNAO key you get your own searches, 100 a day, "
    "and never have to wait for Mikke's. Tap the button below, it only takes a minute!"
)
# after the answer, once, when SauceNAO stopped accepting the user's key
KEY_REJECTED = (
    "🔑 <i>SauceNAO stopped accepting your API key, so Mikke used her own this time. "
    "Send her a new one in private: /apikey</i>"
)
KEY_STEPS = (
    "🔑 <b>Your own SauceNAO key</b>\n\n"
    "Mikke shares her SauceNAO searches with everyone, so sometimes they run out. "
    "With your own free key, your searches use your own limits instead.\n\n"
    '1. Sign up for free at <a href="https://saucenao.com/user.php">saucenao.com</a>\n'
    f'2. Open the <a href="{API_PAGE}">API page</a>\n'
    "3. Copy your <b>api key</b> and send it to me right here"
)
KEY_STATUS_SAVED = "✅ Your key <code>{}</code> is saved, and your searches use it. Send a new one to replace it."
KEY_STATUS_INVALID = (
    "⚠ SauceNAO stopped accepting your key <code>{}</code>, so your searches use Mikke's again. "
    "Send a new one to replace it."
)
KEY_SAVED = (
    "✅ <b>Yay, got it!</b> From now on your searches use your own SauceNAO key <code>{}</code> "
    "instead of Mikke's shared one."
)
KEY_ACCOUNT = "<b>Account:</b> {}"
KEY_LIMITS = "<b>Your limits:</b> {}"
KEY_LEFT_TODAY = "<b>Left today:</b> {}"
KEY_USED_UP_TODAY = "SauceNAO says this key has no searches left for today, so Mikke will use hers until it recovers."
KEY_REMOVE_HINT = "Changed your mind? Tap the button below or send <code>/apikey remove</code>."
KEY_REFUSED = (
    "Hmm, SauceNAO doesn't know this key, so Mikke didn't save it. Make sure you copied the whole "
    f'<b>api key</b> from the <a href="{API_PAGE}">API page</a> while logged in.'
)
KEY_MALFORMED = (
    "That doesn't look like a SauceNAO API key. "
    f'Copy the <b>api key</b> from the <a href="{API_PAGE}">API page</a> and send it to me right here.'
)
KEY_BUSY = "Mikke is checking this key too often right now. Please try again in a minute!"
KEY_CHECK_FAILED = "Mikke couldn't reach SauceNAO to check your key. Please try again in a bit!"
KEY_REMOVED = "Done! Mikke forgot your key, and your searches use hers again."
KEY_NOTHING_TO_REMOVE = "You don't have a key saved, so there's nothing to remove."
# /apikey in a group
KEY_PRIVATE_ONLY = "Your own SauceNAO key goes to Mikke in a private chat: open @{} and send /apikey there."
KEY_IN_CHAT_DELETED = (
    "Psst{}! API keys go to Mikke in a private chat, never in a group. She deleted your message and didn't save "
    "the key, but others may have seen it, so you may want to generate a new one on the "
    f'<a href="{API_PAGE}">API page</a>.'
)
KEY_IN_CHAT_KEPT = (
    "Psst{}! API keys go to Mikke in a private chat, never in a group. She didn't save the key and can't "
    "delete your message here, so please delete it yourself. Others may have seen it, so you may want to "
    f'generate a new one on the <a href="{API_PAGE}">API page</a>.'
)
