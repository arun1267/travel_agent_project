"""Password sign-in, API access tokens, and Redis-backed request limits."""

import hashlib
import hmac
import json
import os
import secrets
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

import redis
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from local_auth_store import LocalAuthStore


TOKEN_TTL = 3600
SESSION_TTL = 21600
RATE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('TTL', KEYS[1])}
"""
bearer = HTTPBearer(auto_error=False)
accounts_lock = Lock()


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class SignupRequest(LoginRequest):
    username: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


def accounts_path() -> Path:
    return Path(os.getenv("TRAVEL_ACCOUNTS_FILE") or Path(__file__).with_name("login.json"))


def load_accounts(path):
    try:
        accounts = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(accounts, dict):
        raise ValueError("Invalid login file")
    return accounts


def save_accounts(path, accounts):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(accounts, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def get_auth_store():
    if os.getenv("TRAVEL_AUTH_STORE") == "memory":
        return LocalAuthStore()
    return redis.from_url(
        os.getenv("REDIS_URL") or "redis://localhost:6379/0",
        decode_responses=True, socket_connect_timeout=1, socket_timeout=1,
        retry=Retry(NoBackoff(), 0),
    )


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token_key(token: str) -> str:
    return f"travel:auth:token:{digest(token)}"


def redis_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except RedisError as error:
        raise HTTPException(503, "Authentication and session storage is temporarily unavailable.") from error


def rate_limit(client, bucket: str, limit: int, window: int = 60):
    key = f"travel:rate:{bucket}"
    if isinstance(client, LocalAuthStore):
        count, ttl = client.increment_rate(key, window)
    else:
        count, ttl = redis_call(client.eval, RATE_SCRIPT, 1, key, window)
    if count > limit:
        raise HTTPException(429, "Too many requests. Try again shortly.", headers={"Retry-After": str(max(1, ttl))})


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024**2)


def signup(payload: SignupRequest, request: Request, client=Depends(get_auth_store)):
    address = request.client.host if request.client else "unknown"
    rate_limit(client, f"signup:address:{digest(address)}", 5)
    username = payload.username.lower()
    salt = secrets.token_bytes(16)
    record = [salt.hex(), hash_password(payload.password, salt).hex()]
    try:
        with accounts_lock:
            path = accounts_path()
            accounts = load_accounts(path)
            if username in accounts:
                raise HTTPException(409, "That username is already taken.")
            accounts[username] = record
            save_accounts(path, accounts)
    except (OSError, ValueError) as error:
        raise HTTPException(503, "The login file is temporarily unavailable.") from error


def login(payload: LoginRequest, request: Request, response: Response, client=Depends(get_auth_store)):
    username = payload.username.lower()
    address = request.client.host if request.client else "unknown"
    rate_limit(client, f"login:address:{digest(address)}", 10)
    rate_limit(client, f"login:account:{digest(username)}", 5)
    try:
        with accounts_lock:
            record = load_accounts(accounts_path()).get(username)
        salt, expected = (
            (bytes.fromhex(record[0]), bytes.fromhex(record[1]))
            if record is not None else (bytes(16), bytes(64))
        )
        if len(salt) != 16 or len(expected) != 64:
            raise ValueError("Invalid password record")
    except (OSError, KeyError, TypeError, IndexError, ValueError) as error:
        raise HTTPException(503, "The login file is temporarily unavailable.") from error
    actual = hash_password(payload.password, salt)
    if not hmac.compare_digest(actual, expected) or record is None:
        raise HTTPException(401, "Invalid username or password.", headers={"WWW-Authenticate": "Bearer"})
    token = secrets.token_urlsafe(32)
    redis_call(client.set, token_key(token), digest(username), ex=TOKEN_TTL)
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL}


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), client=Depends(get_auth_store)):
    user = redis_call(client.get, token_key(credentials.credentials)) if credentials else None
    if not user:
        raise HTTPException(401, "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    return user


def limit_api(request: Request, user: str = Depends(current_user), client=Depends(get_auth_store)):
    lane = "chat" if request.url.path == "/api/chat" else "search"
    default = 10 if lane == "chat" else 30
    try:
        limit = int(os.getenv(f"TRAVEL_{lane.upper()}_RATE_LIMIT") or default)
        if limit < 1:
            raise ValueError("Rate limit must be positive")
    except ValueError as error:
        raise HTTPException(503, "API rate limit is not configured correctly.") from error
    rate_limit(client, f"{lane}:{user}", limit)
    return user


def logout(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), client=Depends(get_auth_store)):
    if credentials:
        redis_call(client.delete, token_key(credentials.credentials))


def chat_session(client, user: str, session_id: str | None) -> str:
    if session_id is None:
        while True:
            session_id = secrets.token_urlsafe(24)
            if redis_call(client.set, f"travel:session:{user}:{session_id}", "1", ex=SESSION_TTL, nx=True):
                return session_id
    key = f"travel:session:{user}:{session_id}"
    if not redis_call(client.expire, key, SESSION_TTL):
        raise HTTPException(404, "Chat session not found. Start a new trip.")
    return session_id
