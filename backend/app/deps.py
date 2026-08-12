from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request

from .state import SESSION_MINUTES, store


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
