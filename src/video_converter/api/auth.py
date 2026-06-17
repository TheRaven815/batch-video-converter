from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

from video_converter.core.config import get_settings
from video_converter.core.storage import create_storage_client

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)
logger = logging.getLogger(__name__)

INVALID_CREDENTIALS_MESSAGE = "Invalid username or password"
INVALID_TOKEN_MESSAGE = "Could not validate credentials"

_storage_client = None


def get_storage():
    global _storage_client
    if _storage_client is None:
        _storage_client = create_storage_client(get_settings())
    return _storage_client


AUTH_CREDENTIALS_KEY = "auth:credentials"


def hash_password(password: str) -> str:
    # Use a static salt to ensure persistence across restarts
    # since jwt_secret is randomly generated if not in .env.
    salt = "bvc_static_salt_123!"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def get_active_credentials() -> dict:
    storage = get_storage()
    settings = get_settings()

    raw = storage.get(AUTH_CREDENTIALS_KEY)
    if raw:
        try:
            data = json.loads(raw)
            if "username" in data and "password_hash" in data:
                return data
        except json.JSONDecodeError:
            pass

    return {
        "username": settings.app_username,
        "password_hash": hash_password(settings.app_password),
    }


ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30


class Token(BaseModel):
    access_token: str
    token_type: str


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    settings = get_settings()
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
    query_token: Annotated[str | None, Query(alias="token")] = None,
) -> str | None:
    token = token or query_token
    settings = get_settings()
    if not settings.app_password:
        return None  # No auth required

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=INVALID_TOKEN_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        username: str = payload.get("sub", "")

        creds = get_active_credentials()
        if username != creds["username"]:
            raise credentials_exception
    except jwt.InvalidTokenError:
        raise credentials_exception from None

    return username


async def _issue_login_token(form_data: OAuth2PasswordRequestForm) -> Token:
    settings = get_settings()
    if not settings.app_password:
        return Token(access_token="no-auth-required", token_type="bearer")

    creds = get_active_credentials()
    if (
        form_data.username != creds["username"]
        or hash_password(form_data.password) != creds["password_hash"]
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    access_token = create_access_token(
        data={"sub": creds["username"]}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")


@router.post("/login", response_model=Token)
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> Token:
    return await _issue_login_token(form_data)


@router.post("/token", response_model=Token, include_in_schema=False)
async def token_alias(form_data: Annotated[OAuth2PasswordRequestForm, Depends()]) -> Token:
    return await _issue_login_token(form_data)


class CredentialsUpdateRequest(BaseModel):
    current_password: str
    new_username: str | None = None
    new_password: str | None = None


@router.put("/credentials")
async def update_credentials(
    req: CredentialsUpdateRequest, current_user: Annotated[str, Depends(get_current_user)]
):
    creds = get_active_credentials()
    if hash_password(req.current_password) != creds["password_hash"]:
        raise HTTPException(status_code=400, detail="Invalid current password")

    if not req.new_username and not req.new_password:
        raise HTTPException(status_code=400, detail="No new credentials provided")

    new_creds = {
        "username": req.new_username if req.new_username else creds["username"],
        "password_hash": (
            hash_password(req.new_password) if req.new_password else creds["password_hash"]
        ),
    }
    get_storage().set(AUTH_CREDENTIALS_KEY, json.dumps(new_creds))
    return {"status": "ok"}


@router.post("/forgot-password")
async def forgot_password():
    temp_password = secrets.token_urlsafe(8)
    creds = get_active_credentials()

    new_creds = {"username": creds["username"], "password_hash": hash_password(temp_password)}
    get_storage().set(AUTH_CREDENTIALS_KEY, json.dumps(new_creds))

    logger.warning("=========================================================")
    logger.warning("TEMPORARY PASSWORD GENERATED: %s", temp_password)
    logger.warning("Please login with this password and change it immediately.")
    logger.warning("=========================================================")

    return {"status": "ok"}
