"""Profile photos: what is accepted, its URL, and who may see it.

The page crops and shrinks the photo itself (a 256×256 JPEG of about 30 KB,
views/settings.js), so the server only checks it is a JPEG or PNG (by its
first bytes, not by what the request says) and not too big. It is stored on
the User row (User.avatar) and deleted with the account.

The URL carries the time of the last change (?v=), so a browser may keep the
photo as long as it likes: a new photo is a new URL.
"""

from . import models

MAX_BYTES = 200 * 1024
_SIGNATURES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG\r\n\x1a\n": "image/png"}


def image_type(data: bytes) -> str | None:
    """ "image/jpeg" or "image/png" from the file's first bytes; None for anything else."""
    return next((kind for signature, kind in _SIGNATURES.items() if data.startswith(signature)), None)


def url(user: models.User) -> str | None:
    """Where the page fetches the photo, or None when there is none."""
    if user.avatar_updated_at is None:
        return None
    return f"/api/users/{user.id}/avatar?v={int(user.avatar_updated_at.timestamp())}"


def may_see(viewer: models.User, owner: models.User) -> bool:
    """Your own photo; the admin sees everyone's (the accounts table)."""
    return viewer.id == owner.id or bool(viewer.is_admin)
