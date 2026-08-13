from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from .csrf import enforce_csrf_for_cookie_auth
from .state import PORTAL_SESSION_MINUTES, SESSION_MINUTES, store

SESSION_COOKIE_NAME = 'clinic_session'
PORTAL_SESSION_COOKIE_NAME = 'portal_session'

_PASSWORD_CHANGE_ONLY = {'/api/auth/me', '/api/auth/change-password', '/api/auth/logout'}


def extract_session_token(request: Request, authorization: str | None, cookie_name: str) -> tuple[str | None, bool]:
    """Returns (token, via_cookie). An explicit Authorization header takes
    priority over a cookie when both are present -- a client that went out
    of its way to set that header (an API script, or a test switching
    between users on a shared HTTP client whose cookie jar still holds an
    older login) means it deliberately, not accidentally. via_cookie tells
    the caller whether CSRF validation applies -- Bearer-token requests are
    CSRF-immune by construction (see csrf.py), cookie-authenticated ones
    are not."""
    if authorization and authorization.startswith('Bearer '):
        return authorization.split(' ', 1)[1], False
    cookie_token = request.cookies.get(cookie_name)
    if cookie_token:
        return cookie_token, True
    return None, False


def current_user(request: Request, authorization: str | None = Header(default=None)):
    token, via_cookie = extract_session_token(request, authorization, SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(401, 'Authentication required')
    session = store.get_session(token)
    if not session:
        raise HTTPException(401, 'Invalid or expired session')
    user = store.get_user(session['user_id'])
    if not user or not user.active:
        store.delete_session(token)
        raise HTTPException(401, 'Invalid or expired session')
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_ONLY:
        raise HTTPException(403, 'Password change required before accessing clinic data')
    if via_cookie:
        enforce_csrf_for_cookie_auth(request)
    store.touch_session(token, SESSION_MINUTES)
    return user


def require_roles(*roles):
    def dep(user=Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403, 'Your role does not permit this action')
        return user
    return dep


def patient_or_404(user, patient_id):
    p = store.get_patient(user.organization_id, patient_id)
    if not p:
        raise HTTPException(404, 'Patient not found')
    return p


_PORTAL_PASSWORD_CHANGE_ONLY = {'/api/portal/auth/me', '/api/portal/auth/change-password', '/api/portal/auth/logout'}

def current_portal_account(request: Request, authorization: str | None = Header(default=None)):
    """Deliberately separate from current_user: a portal (patient) token is
    validated against portal_sessions, never against the staff `sessions`
    table, and the reverse. There is no code path anywhere that accepts a
    staff token for a portal endpoint or a portal token for a staff
    endpoint -- see models.py's note on the patient-portal section for why
    that separation is a hard security requirement here, not a style
    preference. Same cookie-or-Bearer-token pattern as current_user, and
    the same CSRF rule: only cookie-authenticated requests are checked."""
    token, via_cookie = extract_session_token(request, authorization, PORTAL_SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(401, 'Authentication required')
    session = store.get_portal_session(token)
    if not session:
        raise HTTPException(401, 'Invalid or expired session')
    account = store.get_portal_account(session['account_id'])
    if not account or not account.active:
        store.delete_portal_session(token)
        raise HTTPException(401, 'Invalid or expired session')
    if account.must_change_password and request.url.path not in _PORTAL_PASSWORD_CHANGE_ONLY:
        raise HTTPException(403, 'Password change required before using the portal')
    if via_cookie:
        enforce_csrf_for_cookie_auth(request)
    store.touch_portal_session(token, PORTAL_SESSION_MINUTES)
    return account
