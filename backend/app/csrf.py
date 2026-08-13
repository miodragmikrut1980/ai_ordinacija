"""CSRF protection for cookie-authenticated requests.

Why this exists: as of this version, the browser-facing apps (web/static
app.js and portal.js) authenticate via an HttpOnly session cookie instead
of a Bearer token kept in localStorage (the fix for the localStorage/XSS
token-theft gap this module's sibling change addresses). A cookie is sent
automatically by the browser on every request to this origin, including
ones a malicious third-party page could trigger (a form auto-submit, an
img/fetch from another site) -- that's the CSRF risk a bearer token in a
JS-managed header never had, since a cross-site page cannot read our
cookies to construct that header itself.

The mitigation is the standard double-submit cookie pattern: on login, the
server sets a second, non-HttpOnly cookie holding a random CSRF token. The
frontend JS reads that cookie and echoes its value back in an
`X-CSRF-Token` request header on every mutating request. A cross-site
attacker can trigger the request but cannot read our cookie's value (the
same-origin policy blocks that), so it cannot construct a matching header.
This check does not apply to requests authenticated via a Bearer token
(see deps.py) -- forging an Authorization header requires script access
this attack model doesn't have, so token-authenticated requests are
already CSRF-immune by construction.
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

CSRF_COOKIE_NAME = 'csrf_token'
CSRF_HEADER_NAME = 'x-csrf-token'
_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def enforce_csrf_for_cookie_auth(request: Request) -> None:
    """Call this only when the request was authenticated via a session
    cookie (not a Bearer token). Raises 403 if the double-submit check
    fails. A missing CSRF cookie (e.g. an old session predating this
    feature) fails closed, same as a mismatched one."""
    if request.method in _SAFE_METHODS:
        return
    cookie_value = request.cookies.get(CSRF_COOKIE_NAME)
    header_value = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_value or not header_value or not secrets.compare_digest(cookie_value, header_value):
        raise HTTPException(403, 'CSRF validation failed')
