from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from .state import PORTAL_SESSION_MINUTES, SESSION_MINUTES, store


_PASSWORD_CHANGE_ONLY = {'/api/auth/me', '/api/auth/change-password', '/api/auth/logout'}

def current_user(request: Request, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Authentication required')
    token = authorization.split(' ', 1)[1]
    session = store.get_session(token)
    if not session:
        raise HTTPException(401, 'Invalid or expired session')
    user = store.get_user(session['user_id'])
    if not user or not user.active:
        store.delete_session(token)
        raise HTTPException(401, 'Invalid or expired session')
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_ONLY:
        raise HTTPException(403, 'Password change required before accessing clinic data')
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
    preference."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'Authentication required')
    token = authorization.split(' ', 1)[1]
    session = store.get_portal_session(token)
    if not session:
        raise HTTPException(401, 'Invalid or expired session')
    account = store.get_portal_account(session['account_id'])
    if not account or not account.active:
        store.delete_portal_session(token)
        raise HTTPException(401, 'Invalid or expired session')
    if account.must_change_password and request.url.path not in _PORTAL_PASSWORD_CHANGE_ONLY:
        raise HTTPException(403, 'Password change required before using the portal')
    store.touch_portal_session(token, PORTAL_SESSION_MINUTES)
    return account
