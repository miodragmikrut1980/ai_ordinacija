from __future__ import annotations
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .logging_setup import access_logger, bind_request_id, configure_logging, new_request_id, reset_request_id
from .routers import admin_tools, auth, clinical_ai, epidemiology, finance, patients, scheduling
from . import pediatrics, portal
from .state import APP_VERSION, RATE_LIMIT_PER_MINUTE, SESSION_MINUTES, TRUST_PROXY_HEADERS, WEB_DIR, ai, store

configure_logging()

app = FastAPI(title='Clinic AI Assistant', version=APP_VERSION)
app.mount('/static', StaticFiles(directory=WEB_DIR), name='static')
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(scheduling.router)
app.include_router(finance.router)
app.include_router(portal.router)
app.include_router(pediatrics.router)
app.include_router(epidemiology.router)
app.include_router(clinical_ai.router)
app.include_router(admin_tools.router)


@app.middleware('http')
async def request_id_and_access_log(request: Request, call_next):
    request_id = new_request_id()
    token = bind_request_id(request_id)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        # request_id is embedded directly in the message rather than relied
        # on via the context-var log filter: Starlette's BaseHTTPMiddleware
        # runs call_next through its own anyio task group, and a contextvar
        # set here is not guaranteed to still be visible to the filter by
        # the time this log call is handled. Explicit beats implicit.
        access_logger.exception('[%s] %s %s -> unhandled exception (%.1fms)', request_id, request.method, request.url.path, (time.monotonic() - started) * 1000)
        reset_request_id(token)
        raise
    duration_ms = (time.monotonic() - started) * 1000
    response.headers['X-Request-ID'] = request_id
    access_logger.info('[%s] %s %s -> %s (%.1fms)', request_id, request.method, request.url.path, response.status_code, duration_ms)
    reset_request_id(token)
    return response


# Simple in-process per-IP sliding-window rate limiter. This protects against
# accidental hammering and basic scripted abuse; it resets on restart and is
# not a substitute for rate limiting at a reverse proxy / WAF in front of a
# real deployment, but it is a meaningful floor for a single-process app that
# ships with none by default.
_rate_lock = Lock()
_rate_windows: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # X-Forwarded-For is only trusted when CLINIC_TRUST_PROXY_HEADERS=1 is
    # explicitly set (see state.py / README). Trusting it unconditionally
    # would let any direct client set an arbitrary value and get a fresh
    # rate-limit budget on every request -- the opposite of what this
    # middleware is for. Only enable it when this app is actually reachable
    # exclusively through a reverse proxy that overwrites/strips the header
    # for external clients before setting its own.
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.client.host if request.client else 'unknown'


def _rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        window = _rate_windows[client_ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= RATE_LIMIT_PER_MINUTE:
            return True
        window.append(now)
        return False


@app.middleware('http')
async def rate_limit(request: Request, call_next):
    if _rate_limited(_client_ip(request)):
        return JSONResponse({'detail': 'Too many requests, slow down.'}, status_code=429, headers={'Retry-After': '60'})
    return await call_next(request)


@app.middleware('http')
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    # This app can be served over plain HTTP by the bundled uvicorn dev
    # server, or over HTTPS with the self-signed cert start.sh generates (see
    # CLINIC_TLS in start.sh). A real deployment should terminate TLS with a
    # proper certificate at a reverse proxy. Once the connection is HTTPS,
    # HSTS tells browsers to only ever use HTTPS for this host going forward.
    if request.url.scheme == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains'
    return response


@app.get('/')
def index():
    return FileResponse(WEB_DIR / 'index.html')


@app.get('/portal')
def portal_index():
    return FileResponse(WEB_DIR / 'portal.html')


@app.get('/api/health')
def health():
    db_ok = store.health_check()
    return {
        'status': 'ok' if db_ok else 'degraded', 'version': APP_VERSION, 'ai_provider': 'ollama' if ai.enabled else 'local',
        'storage': 'tenant-isolated-encrypted-local', 'database': 'ok' if db_ok else 'unreachable', 'session_minutes': SESSION_MINUTES,
    }
