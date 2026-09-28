"""SauceNAO or trace.moe not answering: down, overloaded, or out of reach for a moment.

That is not our bug. The clients retry such a failure once and then raise their
`UnavailableError`; the searchers tell the user to try another engine or again
in a minute, and log it as a warning, not a Sentry event.
"""

import httpx

# The pause before the one retry
RETRY_SECONDS = 3.0
# No answer at all; a request that failed on the way may never have reached the engine
UNREACHABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)
# Timeouts that come only after the whole read timeout, a minute
SLOW = (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)
# Cloudflare's own names for what it answers when the site behind it fails
CLOUDFLARE = {
    520: "unknown error",
    521: "web server is down",
    522: "connection timed out",
    523: "origin is unreachable",
    524: "a timeout occurred",
}


def is_down(response: httpx.Response) -> bool:
    """A 5xx: the engine, or Cloudflare in front of it, could not answer the search."""
    return response.is_server_error


def status(response: httpx.Response) -> str:
    """`HTTP 521 (Cloudflare: web server is down)`: never the body, which is a whole HTML page at times."""
    text = f"HTTP {response.status_code}"
    if meaning := CLOUDFLARE.get(response.status_code):
        text += f" (Cloudflare: {meaning})"
    return text


def describe(error: httpx.TransportError) -> str:
    """`ConnectTimeout`, with httpx's reason if it gives one."""
    return f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
