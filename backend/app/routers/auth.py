from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from ..deps import current_user, require_roles
from ..models import LoginRequest, MfaAdminReset, MfaCode, MfaLoginComplete, MfaSetupResponse, PasswordChange, UserCreate, UserStatusUpdate
from ..state import SESSION_MINUTES, store

router = APIRouter()


@router.post('/api/auth/login')
def login(payload: LoginRequest):
    locked, retry_after = store.is_locked_out(payload.organization, payload.username)
    if locked:
        raise HTTPException(429, f'Too many failed login attempts. Try again in {retry_after} seconds.', headers={'Retry-After': str(retry_after)})
    user = store.authenticate(payload.organization, payload.username, payload.password)
    store.record_login_attempt(payload.organization, payload.username, success=bool(user))
    if not user:
        raise HTTPException(401, 'Invalid clinic, username or password')
    if user.mfa_enabled:
        challenge = store.begin_mfa_login_challenge(user.id)
        store.audit(user, 'mfa_challenge', 'session')
        return {'mfa_required': True, 'mfa_challenge': challenge}
    token, _ = store.create_session(user.id, SESSION_MINUTES)
    store.audit(user, 'login', 'session')
    return {'token': token, 'expires_in_minutes': SESSION_MINUTES, 'user': store.public_user(user)}


@router.post('/api/auth/mfa/complete-login')
def complete_mfa_login(payload: MfaLoginComplete):
    user = store.complete_mfa_login_challenge(payload.challenge, payload.code)
    if not user or not user.active:
        raise HTTPException(401, 'Invalid or expired verification code')
    token, _ = store.create_session(user.id, SESSION_MINUTES)
    store.audit(user, 'login_mfa', 'session')
    return {'token': token, 'expires_in_minutes': SESSION_MINUTES, 'user': store.public_user(user)}


@router.get('/api/auth/me')
def me(user=Depends(current_user)):
    return store.public_user(user)


@router.post('/api/auth/logout', status_code=204)
def logout(authorization: str | None = Header(default=None), user=Depends(current_user)):
    store.delete_session(authorization.split(' ', 1)[1])
    store.audit(user, 'logout', 'session')


@router.post('/api/auth/change-password', status_code=204)
def change_password(payload: PasswordChange, user=Depends(current_user)):
    if payload.current_password == payload.new_password:
        raise HTTPException(422, 'New password must be different')
    if not store.change_password(user, payload.current_password, payload.new_password):
        raise HTTPException(400, 'Current password is incorrect')
    store.audit(user, 'change_password', 'user', user.id)
    # A stolen/leaked token should stop working the moment the password is
    # changed, not linger until it naturally expires -- so every session for
    # this user, including the one used to make this request, is revoked.
    # The client is expected to log in again with the new password.
    store.delete_sessions_for_user(user.id)


@router.post('/api/auth/mfa/setup', response_model=MfaSetupResponse)
def setup_mfa(user=Depends(current_user)):
    secret = store.begin_mfa_setup(user.id)
    issuer = 'Clinic AI Assistant'
    account = f'{user.organization_id}:{user.username}'
    from urllib.parse import quote
    store.audit(user, 'mfa_setup_started', 'user', user.id)
    return MfaSetupResponse(secret=secret, issuer=issuer, account_name=account, otpauth_uri=f'otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30')


@router.post('/api/auth/mfa/confirm', status_code=204)
def confirm_mfa(payload: MfaCode, user=Depends(current_user)):
    if not store.confirm_mfa_setup(user.id, payload.code):
        raise HTTPException(400, 'Verification code is invalid or MFA setup has expired')
    store.audit(user, 'mfa_enabled', 'user', user.id)


@router.post('/api/auth/mfa/disable', status_code=204)
def disable_mfa(payload: MfaCode, user=Depends(current_user)):
    if not store.disable_mfa(user.id, payload.code):
        raise HTTPException(400, 'Verification code is invalid or MFA is not enabled')
    store.audit(user, 'mfa_disabled', 'user', user.id)

@router.post('/api/users/{user_id}/mfa-reset', status_code=204)
def reset_mfa(user_id: str, payload: MfaAdminReset, user=Depends(require_roles('admin'))):
    if user_id == user.id:
        raise HTTPException(422, 'Use MFA disable with a current code for your own account')
    target = store.reset_mfa_as_admin(user.organization_id, user_id)
    if not target:
        raise HTTPException(404, 'Active user not found')
    store.audit(user, 'mfa_recovery_reset', 'user', target.id, f'{target.username} · reason: {payload.reason.strip()}')


@router.get('/api/sessions')
def list_sessions(user=Depends(require_roles('admin'))):
    return store.list_active_sessions(user.organization_id)


@router.delete('/api/sessions/{session_id}', status_code=204)
def revoke_session(session_id: str, user=Depends(require_roles('admin'))):
    if not store.revoke_session(user.organization_id, session_id):
        raise HTTPException(404, 'Session not found')
    store.audit(user, 'revoke', 'session', session_id)


@router.get('/api/users')
def users(user=Depends(require_roles('admin'))):
    return [store.public_user(x) for x in store.list_users(user.organization_id)]


@router.post('/api/users')
def create_user(payload: UserCreate, user=Depends(require_roles('admin'))):
    try:
        u = store.create_user(user.organization_id, payload, force_password_change=True)
    except ValueError as e:
        raise HTTPException(409, str(e))
    store.audit(user, 'create', 'user', u.id, f'{u.username} · {u.role}')
    return store.public_user(u)


@router.patch('/api/users/{user_id}/status')
def user_status(user_id: str, payload: UserStatusUpdate, user=Depends(require_roles('admin'))):
    if user_id == user.id and not payload.active:
        raise HTTPException(422, 'You cannot deactivate your own account')
    target = store.set_user_active(user.organization_id, user_id, payload.active)
    if not target:
        raise HTTPException(404, 'User not found')
    store.audit(user, 'activate' if payload.active else 'deactivate', 'user', target.id, target.username)
    return store.public_user(target)


@router.get('/api/audit')
def audit(user=Depends(require_roles('doctor', 'admin'))):
    return store.list_audit(user.organization_id)[:300]


@router.get('/api/audit/verify')
def audit_verify(user=Depends(require_roles('admin'))):
    return store.verify_audit_chain(user.organization_id)
