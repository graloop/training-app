"""What the reverse proxy or tunnel in front of the app says about a visitor.

On the internet the app is never reached directly: Cloudflare or a reverse
proxy takes the visitor's HTTPS connection and passes the request on over the
server's private network, adding headers that say who is visiting and how.
"""

import ipaddress

from fastapi import Request


def _ip(value: str | None):
    try:
        return ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None


def _behind_proxy(request: Request) -> bool:
    """The request arrives from a private address (a Docker network, the server
    itself), as it does from a proxy. Only then are the headers below believed,
    so someone connecting straight from the internet can't make them up."""
    peer = _ip(request.client.host if request.client else None)
    return peer is not None and (peer.is_private or peer.is_loopback)


def is_https(request: Request) -> bool:
    """Whether the visitor's own connection is encrypted."""
    if request.url.scheme == "https":
        return True
    if not _behind_proxy(request):
        return False
    scheme = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    # CF-Visitor still says https when a proxy behind Cloudflare rewrote
    # X-Forwarded-Proto to the scheme of its own, unencrypted, hop.
    return scheme == "https" or '"https"' in request.headers.get("cf-visitor", "")


def address(request: Request) -> str:
    """The visitor's address, used to limit login attempts. Cloudflare sets
    CF-Connecting-IP itself; nginx-style proxies set X-Real-IP and add the
    visitor at the end of X-Forwarded-For. A proxy that isn't behind Cloudflare
    passes a made-up CF-Connecting-IP along, which is why the lockout of each
    username doesn't depend on this."""
    peer = request.client.host if request.client else "unknown"
    if _behind_proxy(request):
        for value in (
            request.headers.get("cf-connecting-ip"),
            request.headers.get("x-real-ip"),
            request.headers.get("x-forwarded-for", "").split(",")[-1],
        ):
            visitor = _ip(value)
            if visitor is not None:
                return str(visitor)
    return peer
