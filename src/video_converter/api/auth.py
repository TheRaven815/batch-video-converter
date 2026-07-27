from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from inspect import isawaitable
from typing import Annotated, Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from video_converter.core.config import Settings, get_settings
from video_converter.core.models import StructuredErrorResponse

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    responses={
        400: {"model": StructuredErrorResponse, "description": "Bad request"},
        401: {"model": StructuredErrorResponse, "description": "Authentication failed"},
        422: {"model": StructuredErrorResponse, "description": "Validation failed"},
        503: {"model": StructuredErrorResponse, "description": "Service unavailable"},
    },
)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
logger = logging.getLogger(__name__)

INVALID_CREDENTIALS_MESSAGE = "Invalid username or password"
INVALID_TOKEN_MESSAGE = "Could not validate credentials"
AUTH_NOT_CONFIGURED_MESSAGE = (
    "Authentication is not configured. Complete the first-run setup in the UI "
    "or set APP_USERNAME and APP_PASSWORD."
)

AUTH_CREDENTIALS_KEY = "auth:credentials"

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12
STREAM_TICKET_AUDIENCE = "stream"
STREAM_TICKET_EXPIRE_SECONDS = 60

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300

_storage_client: Any = None


def configure_runtime(settings: Settings, storage: Any) -> None:
    """Bind the storage instance owned by the API lifespan."""
    global _storage_client
    _storage_client = storage


def get_storage() -> Any:
    if _storage_client is None:
        raise RuntimeError("API storage is not initialized")
    return _storage_client


async def _maybe_await(value):
    return await value if isawaitable(value) else value


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_LEGACY_SALT = "bvc_static_salt_123!"


def _legacy_hash(password: str) -> str:
    """SHA-256 hash used by records written before the bcrypt migration."""
    return hashlib.sha256(f"{_LEGACY_SALT}{password}".encode()).hexdigest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, stored_hash: str) -> bool:
    if stored_hash.startswith("$2"):
        try:
            return bcrypt.checkpw(password.encode(), stored_hash.encode())
        except ValueError:
            return False
    return secrets.compare_digest(_legacy_hash(password), stored_hash)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


async def get_active_credentials() -> dict | None:
    """Return the active credentials, or None when authentication is unconfigured.

    Stored credentials (set via the UI) take precedence over the environment.
    An empty APP_PASSWORD no longer disables authentication — it means no one
    can log in until credentials are configured.
    """
    storage = get_storage()
    settings = get_settings()

    raw = await _maybe_await(storage.get(AUTH_CREDENTIALS_KEY))
    if raw:
        try:
            data = json.loads(raw)
            if "username" in data and "password_hash" in data:
                data.setdefault("credentials_updated_at", 0)
                return data
        except json.JSONDecodeError:
            pass

    if settings.app_password:
        return {
            "username": settings.app_username,
            "password_hash": _legacy_hash(settings.app_password),
            "credentials_updated_at": 0,
        }

    return None


# ---------------------------------------------------------------------------
# Login rate limiting (in-memory, per client address)
# ---------------------------------------------------------------------------

_login_failures: dict[str, list[float]] = {}
_login_lock = threading.Lock()


def _rate_limit_key(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _check_rate_limit(key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        failures = [t for t in _login_failures.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
        _login_failures[key] = failures
        if len(failures) >= LOGIN_MAX_FAILURES:
            retry_after = int(LOGIN_WINDOW_SECONDS - (now - failures[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(max(1, retry_after))},
            )


def _record_login_failure(key: str) -> None:
    with _login_lock:
        _login_failures.setdefault(key, []).append(time.monotonic())


def _reset_login_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


class Token(BaseModel):
    access_token: str
    token_type: str


class StreamTicket(BaseModel):
    ticket: str
    expires_in: int


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode = {**data, "exp": expire, "iat": now}
    settings = get_settings()
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)


async def _validate_token(token: str | None, *, audience: str | None = None) -> str:
    settings = get_settings()
    creds = await get_active_credentials()
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=AUTH_NOT_CONFIGURED_MESSAGE
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_TOKEN_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    try:
        options = {"require": ["exp", "iat"]}
        if audience is not None:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[ALGORITHM],
                audience=audience,
                options=options,
            )
        else:
            # Tokens carrying an "aud" claim (stream tickets) are rejected here
            # by PyJWT, so tickets cannot be replayed against regular endpoints.
            payload = jwt.decode(
                token, settings.jwt_secret, algorithms=[ALGORITHM], options=options
            )

        username = str(payload.get("sub", ""))
        if not secrets.compare_digest(username.encode(), str(creds["username"]).encode()):
            raise credentials_exception

        issued_at = int(payload.get("iat", 0))
        rotated_at = int(creds.get("credentials_updated_at") or 0)
        if issued_at < rotated_at:
            # Token predates the last credentials change — treat as revoked.
            raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception from None

    return username


async def get_current_user(token: Annotated[str | None, Depends(oauth2_scheme)] = None) -> str:
    return await _validate_token(token)


async def get_stream_user(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    ticket: Annotated[str | None, Query(max_length=2048)] = None,
) -> str:
    """Auth dependency for SSE: a Bearer header or a short-lived stream ticket.

    EventSource cannot send headers, so the stream endpoint accepts a ticket
    issued by POST /auth/stream-ticket. Long-lived access tokens are no longer
    accepted via the query string (they leak into access/proxy logs).
    """
    if ticket:
        return await _validate_token(ticket, audience=STREAM_TICKET_AUDIENCE)
    return await _validate_token(token)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class SetupStatus(BaseModel):
    needs_setup: bool


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


_setup_lock = asyncio.Lock()


async def _needs_setup() -> bool:
    return await get_active_credentials() is None


@router.get("/setup-status", response_model=SetupStatus)
async def setup_status() -> SetupStatus:
    """Unauthenticated probe used by the UI to decide between login and first-run setup."""
    return SetupStatus(needs_setup=await _needs_setup())


@router.post("/setup", response_model=Token)
async def initial_setup(req: SetupRequest) -> Token:
    """Create the admin account on first run.

    Only available while no credentials exist (neither stored nor via
    APP_PASSWORD); afterwards it permanently returns 409.
    """
    username = req.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Username must not be blank")

    async with _setup_lock:
        if not await _needs_setup():
            raise HTTPException(status_code=409, detail="Setup has already been completed")
        new_creds = {
            "username": username,
            "password_hash": hash_password(req.password),
            "credentials_updated_at": 0,
        }
        await _maybe_await(get_storage().set(AUTH_CREDENTIALS_KEY, json.dumps(new_creds)))

    logger.info("first-run setup completed; admin account created")
    access_token = create_access_token(data={"sub": username})
    return Token(access_token=access_token, token_type="bearer")


async def _issue_login_token(request: Request, form_data: OAuth2PasswordRequestForm) -> Token:
    creds = await get_active_credentials()
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=AUTH_NOT_CONFIGURED_MESSAGE
        )

    key = _rate_limit_key(request)
    _check_rate_limit(key)

    username_ok = secrets.compare_digest(
        form_data.username.encode(), str(creds["username"]).encode()
    )
    password_ok = verify_password(form_data.password, str(creds["password_hash"]))
    if not (username_ok and password_ok):
        _record_login_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )

    _reset_login_failures(key)

    # Upgrade stored legacy SHA-256 hashes to bcrypt on successful login.
    storage = get_storage()
    if await _maybe_await(storage.get(AUTH_CREDENTIALS_KEY)) and not str(
        creds["password_hash"]
    ).startswith("$2"):
        upgraded = {**creds, "password_hash": hash_password(form_data.password)}
        await _maybe_await(storage.set(AUTH_CREDENTIALS_KEY, json.dumps(upgraded)))

    access_token = create_access_token(data={"sub": creds["username"]})
    return Token(access_token=access_token, token_type="bearer")


@router.post("/login", response_model=Token)
async def login(
    request: Request, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    return await _issue_login_token(request, form_data)


@router.post("/token", response_model=Token, include_in_schema=False)
async def token_alias(
    request: Request, form_data: Annotated[OAuth2PasswordRequestForm, Depends()]
) -> Token:
    return await _issue_login_token(request, form_data)


@router.post("/stream-ticket", response_model=StreamTicket)
async def issue_stream_ticket(
    current_user: Annotated[str, Depends(get_current_user)],
) -> StreamTicket:
    ticket = create_access_token(
        data={"sub": current_user, "aud": STREAM_TICKET_AUDIENCE},
        expires_delta=timedelta(seconds=STREAM_TICKET_EXPIRE_SECONDS),
    )
    return StreamTicket(ticket=ticket, expires_in=STREAM_TICKET_EXPIRE_SECONDS)


class CredentialsUpdateRequest(BaseModel):
    current_password: str
    new_username: str | None = None
    new_password: str | None = None


@router.put("/credentials")
async def update_credentials(
    req: CredentialsUpdateRequest, current_user: Annotated[str, Depends(get_current_user)]
):
    creds = await get_active_credentials()
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=AUTH_NOT_CONFIGURED_MESSAGE
        )
    if not verify_password(req.current_password, str(creds["password_hash"])):
        raise HTTPException(status_code=400, detail="Invalid current password")

    if not req.new_username and not req.new_password:
        raise HTTPException(status_code=400, detail="No new credentials provided")

    new_creds = {
        "username": req.new_username if req.new_username else creds["username"],
        "password_hash": (
            hash_password(req.new_password) if req.new_password else creds["password_hash"]
        ),
        # Revokes every token issued before this moment (see _validate_token).
        "credentials_updated_at": int(time.time()),
    }
    await _maybe_await(get_storage().set(AUTH_CREDENTIALS_KEY, json.dumps(new_creds)))
    return {"status": "ok"}
